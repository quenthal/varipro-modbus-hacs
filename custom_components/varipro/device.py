"""VariPro room controller logic, independent of Home Assistant.

Ported from the varipro-modbus-mqtt bridge. All bus access goes through an
injected async bus (see modbus.py) and the fan-only snapshot through an
injected async store, so the logic can be tested without a device or HA.

Register map: VariPro user interface register map v1.4 (manual N03104128).
Addresses are 0-based (holding 4x00001 = 0, input 3x00001 = 0).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
import time
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)

# Holding registers
REG_MODE = 0
REG_FAN = 2
REG_TARGET_HOME = 3
REG_TARGET_AWAY = 4
REG_DEADZONE = 201  # 4x00202, away-mode extended dead zone (manual 9.3)

# Application mode (holding 0)
MODE_OFF = 1
MODE_NORMAL = 2
MODE_AWAY = 3
MODE_TO_HVAC = {MODE_OFF: "off", MODE_NORMAL: "cool", MODE_AWAY: "cool"}

HVAC_MODES = ["off", "cool", "fan_only"]
PRESETS = ["home", "away"]
FAN_TO_RAW = {"off": 0, "low": 1, "medium": 2, "high": 3, "auto": 4}
RAW_TO_FAN = {v: k for k, v in FAN_TO_RAW.items()}
FAN_MODES = ["auto", "low", "medium", "high"]

APP_STATES = {0: "init", 1: "standby", 2: "normal", 3: "away", 4: "flush", 5: "stopped", 10: "test"}

# Alarm bit field (input 3x00103): bit -> key
ALARM_BITS = {
    0: "condensation",
    1: "fan1_rpm",
    2: "fan2_rpm",
    3: "sensor_water_in",
    4: "sensor_water_out",
    11: "parameter_file",
    15: "filter",
}

FANONLY_DEADZONE_RAW = 150  # 15.0 °C, cooling never starts
DEFAULT_DEADZONE_RAW = 20  # 2.0 °C, if the real value has not been read yet
DEADZONE_MAX = 15.0
WRITE_GUARD_S = 15  # a differing read this soon after a write is treated as stale
FAIL_LIMIT = 3  # consecutive read failures before the device is reported unavailable


def scale10(raw: int) -> float:
    return round(raw * 0.1, 1)


def to_raw10(value: float) -> int:
    return round(float(value) * 10)


class VariProError(Exception):
    """Base error."""


class VariProConnectionError(VariProError):
    """The device does not answer."""


class VariProWriteError(VariProError):
    """A register write failed."""


class VariProStateError(VariProError):
    """The command is not possible in the current state."""


class Bus(Protocol):
    last_error: str | None

    async def read(self, kind: str, start: int, count: int) -> list[int] | None: ...

    async def write(self, addr: int, value: int) -> bool: ...


class SnapshotStore(Protocol):
    async def load(self) -> dict[str, Any] | None: ...

    async def save(self, snapshot: dict[str, Any]) -> None: ...

    async def clear(self) -> None: ...


@dataclass
class RegisterGroup:
    name: str
    kind: str  # "holding" or "input"
    start: int
    count: int
    interval: float  # seconds
    handlers: dict[int, Callable[[int], None]]


class VariProDevice:
    """State and commands of one VariPro room controller."""

    def __init__(
        self,
        bus: Bus,
        store: SnapshotStore,
        *,
        min_temp: float = 18.0,
        max_temp: float = 24.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bus = bus
        self.store = store
        self.min_temp = min_temp
        self.max_temp = max_temp
        self.clock = clock

        # Device state (None = not read yet)
        self.mode_raw: int | None = None
        self.fan: str | None = None
        self.target_home: float | None = None
        self.target_away: float | None = None
        self.deadzone_raw: int | None = None  # the real away dead zone, never the fan-only 15.0
        self.deadzone_device: float | None = None  # what the device currently has
        self.app_state: str | None = None
        self.fan_actual: str | None = None
        self.room_temp: float | None = None
        self.setpoint_active: float | None = None
        self.fan_speed: int | None = None
        self.cooling_pid: int | None = None
        self.heating_pid: int | None = None
        self.water_in: float | None = None
        self.water_out: float | None = None
        self.alarm_raw: int | None = None

        # Fan only
        self.fan_only_active = False
        self.fan_only_snapshot: dict[str, Any] | None = None
        # (snapshot, restore application mode) - state still to be written back to the device
        self.pending_restore: tuple[dict[str, Any], bool] | None = None

        self.write_guard: dict[int, tuple[int, float]] = {}
        self.last_poll: dict[str, float] = {}
        self.failures = 0

        self.groups = [
            RegisterGroup(
                "holding_main",
                "holding",
                0,
                5,
                10,
                {
                    REG_MODE: self._on_mode,
                    REG_FAN: self._on_fan,
                    REG_TARGET_HOME: self._on_target_home,
                    REG_TARGET_AWAY: self._on_target_away,
                },
            ),
            RegisterGroup(
                "holding_deadzone",
                "holding",
                REG_DEADZONE,
                1,
                60,
                {
                    REG_DEADZONE: self._on_deadzone,
                },
            ),
            RegisterGroup(
                "input_main",
                "input",
                3,
                8,
                10,
                {
                    3: self._on_app_state,  # 3x00004
                    5: self._on_fan_actual,  # 3x00006
                    6: self._on_room_temp,  # 3x00007
                    7: self._on_setpoint_active,  # 3x00008
                    8: self._on_fan_speed,  # 3x00009
                    9: self._on_cooling_pid,  # 3x00010
                    10: self._on_heating_pid,  # 3x00011
                },
            ),
            RegisterGroup(
                "input_water",
                "input",
                115,
                2,
                30,
                {
                    115: self._on_water_in,  # 3x00116
                    116: self._on_water_out,  # 3x00117
                },
            ),
            RegisterGroup(
                "alarm",
                "input",
                102,
                1,
                60,
                {
                    102: self._on_alarm,  # 3x00103
                },
            ),
        ]

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self.mode_raw is not None and self.failures < FAIL_LIMIT

    @property
    def hvac_mode(self) -> str | None:
        if self.fan_only_active:
            return "fan_only"
        if self.mode_raw is None:
            return None
        return MODE_TO_HVAC.get(self.mode_raw, "off")

    @property
    def preset(self) -> str:
        """Active preset; frozen to the pre-fan-only preset during fan only."""
        if self.fan_only_active and self.fan_only_snapshot:
            return self.fan_only_snapshot.get("preset", "home")
        return "away" if self.mode_raw == MODE_AWAY else "home"

    @property
    def target_temperature(self) -> float | None:
        if self.fan_only_active:
            return None  # no target in fan only
        return self.target_away if self.preset == "away" else self.target_home

    @property
    def hvac_action(self) -> str | None:
        if self.cooling_pid is None:
            return None
        if not self.fan_only_active and self.mode_raw == MODE_OFF:
            return "off"
        if self.cooling_pid > 0:
            return "cooling"
        if (self.heating_pid or 0) > 0:
            return "heating"
        if (self.fan_speed or 0) > 4:
            return "fan"
        return "idle"

    @property
    def deadzone(self) -> float | None:
        """Away dead zone as shown to the user (15.0 during fan only)."""
        return self.deadzone_device

    def alarm(self, key: str) -> bool | None:
        if self.alarm_raw is None:
            return None
        bit = next(b for b, k in ALARM_BITS.items() if k == key)
        return bool(self.alarm_raw & (1 << bit))

    # ------------------------------------------------------------------
    # Bus helpers
    # ------------------------------------------------------------------

    async def _write(self, addr: int, raw: int) -> bool:
        if not await self.bus.write(addr, raw):
            _LOGGER.error("Write failed: register %s = %s (%s)", addr, raw, self.bus.last_error)
            return False
        self.write_guard[addr] = (raw, self.clock() + WRITE_GUARD_S)
        return True

    async def _write_or_raise(self, addr: int, raw: int) -> None:
        if not await self._write(addr, raw):
            raise VariProWriteError(f"register {addr}: {self.bus.last_error}")

    def _guard_blocks(self, addr: int, raw: int) -> bool:
        """True if the read is probably the old value of a just-written register."""
        guard = self.write_guard.get(addr)
        if guard is None:
            return False
        written, until = guard
        if raw == written or self.clock() >= until:
            del self.write_guard[addr]
            return False
        _LOGGER.debug("Ignoring read %s of register %s (wrote %s)", raw, addr, written)
        return True

    # ------------------------------------------------------------------
    # Setup and polling
    # ------------------------------------------------------------------

    async def async_initialize(self) -> None:
        """Handle a fan-only snapshot left by a previous run.

        If the device is still in the fan-only state (away + 15 °C dead zone),
        fan only simply continues (e.g. across a Home Assistant restart).
        Otherwise the pre-fan-only state is restored.
        """
        snapshot = await self.store.load()
        if not snapshot:
            return
        mode = await self.bus.read("holding", REG_MODE, 1)
        deadzone = await self.bus.read("holding", REG_DEADZONE, 1)
        if mode is None or deadzone is None:
            raise VariProConnectionError(self.bus.last_error or "no response")
        if mode[0] == MODE_AWAY and deadzone[0] == FANONLY_DEADZONE_RAW:
            _LOGGER.info("Continuing fan only (snapshot %s)", snapshot)
            self.fan_only_active = True
            self.fan_only_snapshot = snapshot
            self.deadzone_raw = snapshot.get("deadzone_raw", DEFAULT_DEADZONE_RAW)
        else:
            _LOGGER.warning("Fan-only snapshot found but device has left fan only - restoring")
            self.pending_restore = (snapshot, True)

    async def async_refresh(self) -> None:
        """Poll the register groups that are due. Raises VariProConnectionError
        when the device has not answered FAIL_LIMIT times in a row or has
        never been read."""
        for group in self.groups:
            last = self.last_poll.get(group.name)
            if last is not None and self.clock() - last < group.interval:
                continue
            registers = await self.bus.read(group.kind, group.start, group.count)
            if registers is None:
                self.failures += 1
                _LOGGER.debug("Error reading %s: %s", group.name, self.bus.last_error)
                break  # do not wait for a timeout on every group
            self.failures = 0
            self.last_poll[group.name] = self.clock()
            for addr, handler in group.handlers.items():
                raw = registers[addr - group.start]
                if group.kind == "holding" and self._guard_blocks(addr, raw):
                    continue
                handler(raw)
            if group.name == "holding_main":
                await self._check_external_mode_change()

        if not self.available:
            raise VariProConnectionError(self.bus.last_error or "no response")
        if self.pending_restore is not None and self.failures == 0:
            await self._retry_pending_restore()

    def _on_mode(self, raw: int) -> None:
        self.mode_raw = raw

    async def _check_external_mode_change(self) -> None:
        if self.fan_only_active and self.mode_raw != MODE_AWAY:
            _LOGGER.warning("Fan only ended by an external mode change (register 0 = %s)", self.mode_raw)
            await self._exit_fan_only(write_mode=False)

    def _on_fan(self, raw: int) -> None:
        self.fan = RAW_TO_FAN.get(raw, "auto")

    def _on_target_home(self, raw: int) -> None:
        self.target_home = scale10(raw)

    def _on_target_away(self, raw: int) -> None:
        self.target_away = scale10(raw)

    def _on_deadzone(self, raw: int) -> None:
        self.deadzone_device = scale10(raw)
        # The fan-only 15.0 (or one left by an unfinished restore) is not the real value
        if not self.fan_only_active and self.pending_restore is None:
            self.deadzone_raw = raw

    def _on_app_state(self, raw: int) -> None:
        self.app_state = APP_STATES.get(raw, "unknown")

    def _on_fan_actual(self, raw: int) -> None:
        self.fan_actual = RAW_TO_FAN.get(raw, "unknown")

    def _on_room_temp(self, raw: int) -> None:
        self.room_temp = scale10(raw)

    def _on_setpoint_active(self, raw: int) -> None:
        self.setpoint_active = scale10(raw)

    def _on_fan_speed(self, raw: int) -> None:
        self.fan_speed = raw

    def _on_cooling_pid(self, raw: int) -> None:
        self.cooling_pid = raw

    def _on_heating_pid(self, raw: int) -> None:
        self.heating_pid = raw

    def _on_water_in(self, raw: int) -> None:
        self.water_in = scale10(raw)

    def _on_water_out(self, raw: int) -> None:
        self.water_out = scale10(raw)

    def _on_alarm(self, raw: int) -> None:
        if self.alarm_raw is not None:
            for bit, key in ALARM_BITS.items():
                if raw & (1 << bit) and not self.alarm_raw & (1 << bit):
                    _LOGGER.warning("Alarm active: %s", key)
        self.alarm_raw = raw

    # ------------------------------------------------------------------
    # Fan only
    # ------------------------------------------------------------------

    @staticmethod
    def _snapshot_mode(snapshot: dict[str, Any]) -> int:
        mode_raw = snapshot.get("mode_raw")
        if mode_raw in MODE_TO_HVAC:
            return mode_raw
        return MODE_AWAY if snapshot.get("preset") == "away" else MODE_NORMAL

    async def _enter_fan_only(self) -> None:
        """Away mode + 15 °C dead zone + manual fan speed. Partial failures are rolled back."""
        if self.mode_raw is None or self.pending_restore is not None:
            raise VariProStateError("device state unknown or restore pending")

        deadzone_raw = self.deadzone_raw if self.deadzone_raw is not None else DEFAULT_DEADZONE_RAW
        snapshot = {
            "preset": self.preset,
            "mode_raw": self.mode_raw,
            "deadzone_raw": deadzone_raw,
            "fan": self.fan or "auto",
            "time": datetime.now().isoformat(timespec="seconds"),
        }
        await self.store.save(snapshot)  # before writing: a crash halfway is recoverable

        if not await self._write(REG_DEADZONE, FANONLY_DEADZONE_RAW):
            await self.store.clear()
            raise VariProWriteError(f"dead zone: {self.bus.last_error}")
        if not await self._write(REG_MODE, MODE_AWAY):
            if await self._write(REG_DEADZONE, deadzone_raw):
                await self.store.clear()
            else:
                self.pending_restore = (snapshot, False)  # snapshot kept: retried later
            raise VariProWriteError(f"mode: {self.bus.last_error}")
        self.mode_raw = MODE_AWAY
        self.deadzone_device = scale10(FANONLY_DEADZONE_RAW)

        # Manual fan speed if auto/off (auto would stop the fan without cooling demand)
        if snapshot["fan"] in ("auto", "off") and await self._write(REG_FAN, FAN_TO_RAW["medium"]):
            self.fan = "medium"

        self.fan_only_active = True
        self.fan_only_snapshot = snapshot
        _LOGGER.info("Fan only on (snapshot %s)", snapshot)

    async def _restore_snapshot(self, snapshot: dict[str, Any], write_mode: bool) -> bool:
        """Write the snapshot back. write_mode also restores the application
        mode, but only if the device is still in away mode (a change made on
        the panel is not undone). True if everything succeeded."""
        ok = True
        deadzone_raw = snapshot.get("deadzone_raw", DEFAULT_DEADZONE_RAW)
        if await self._write(REG_DEADZONE, deadzone_raw):
            self.deadzone_raw = deadzone_raw
            self.deadzone_device = scale10(deadzone_raw)
        else:
            ok = False

        if write_mode:
            regs = await self.bus.read("holding", REG_MODE, 1)
            if regs is None:
                ok = False
            elif regs[0] == MODE_AWAY:
                mode_raw = self._snapshot_mode(snapshot)
                if mode_raw != MODE_AWAY and not await self._write(REG_MODE, mode_raw):
                    ok = False
                else:
                    self.mode_raw = mode_raw

        # Fan only is a bubble: the forced medium and fan changes during fan only are undone
        orig_fan = snapshot.get("fan")
        if orig_fan in FAN_TO_RAW and orig_fan != self.fan:
            if await self._write(REG_FAN, FAN_TO_RAW[orig_fan]):
                self.fan = orig_fan
            else:
                ok = False
        return ok

    async def _exit_fan_only(self, write_mode: bool = True) -> str:
        """Leave fan only; returns the pre-fan-only preset. write_mode=False
        when the mode already changed elsewhere or the caller writes it."""
        snapshot = self.fan_only_snapshot or await self.store.load() or {}
        self.fan_only_active = False
        self.fan_only_snapshot = None
        if await self._restore_snapshot(snapshot, write_mode):
            await self.store.clear()
        else:
            _LOGGER.error("Fan-only restore incomplete - will retry")
            self.pending_restore = (snapshot, write_mode)
        _LOGGER.info("Fan only off (preset before: %s)", snapshot.get("preset", "home"))
        return snapshot.get("preset", "home")

    async def _retry_pending_restore(self) -> None:
        assert self.pending_restore is not None
        snapshot, write_mode = self.pending_restore
        if await self._restore_snapshot(snapshot, write_mode):
            _LOGGER.info("Fan-only snapshot restored to the device")
            self.pending_restore = None
            await self.store.clear()

    async def async_restore_normal(self) -> None:
        """Leave fan only and restore the previous state (integration removed)."""
        if self.fan_only_active:
            await self._exit_fan_only(write_mode=True)
        elif self.pending_restore is not None:
            await self._retry_pending_restore()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_set_hvac_mode(self, mode: str) -> None:
        if mode not in HVAC_MODES:
            raise ValueError(f"unknown hvac mode {mode}")
        if mode == "fan_only":
            if not self.fan_only_active:
                await self._enter_fan_only()
            return

        restored_preset = None
        if self.fan_only_active:
            restored_preset = await self._exit_fan_only(write_mode=False)

        if mode == "off":
            value = MODE_OFF
        elif restored_preset == "away" or (restored_preset is None and self.mode_raw == MODE_AWAY):
            value = MODE_AWAY  # cool keeps away cooling (also when returning from fan only)
        else:
            value = MODE_NORMAL
        await self._write_or_raise(REG_MODE, value)
        self.mode_raw = value

    async def async_set_fan_mode(self, fan: str) -> None:
        # 'off' is not offered: the device does not keep 0 (returns to auto),
        # and it would equal turning the whole unit off.
        if fan not in FAN_MODES:
            raise ValueError(f"unknown fan mode {fan}")
        if self.fan_only_active and fan == "auto":
            raise VariProStateError("fan auto would stop the fan in fan only")
        await self._write_or_raise(REG_FAN, FAN_TO_RAW[fan])
        self.fan = fan

    async def async_set_temperature(self, value: float) -> None:
        if self.fan_only_active:
            raise VariProStateError("no target temperature in fan only")
        if not self.min_temp <= value <= self.max_temp:
            raise ValueError(f"temperature {value} outside {self.min_temp}-{self.max_temp}")
        raw = to_raw10(value)
        if self.preset == "away":
            await self._write_or_raise(REG_TARGET_AWAY, raw)
            self.target_away = scale10(raw)
        else:
            await self._write_or_raise(REG_TARGET_HOME, raw)
            self.target_home = scale10(raw)

    async def async_set_preset(self, preset: str) -> None:
        if preset not in PRESETS:
            raise ValueError(f"unknown preset {preset}")
        # Selecting a preset during fan only = back to cooling
        if self.fan_only_active:
            await self._exit_fan_only(write_mode=False)
        if self.mode_raw == MODE_OFF:
            _LOGGER.info("Preset %s requested while off - the unit turns on", preset)
        value = MODE_AWAY if preset == "away" else MODE_NORMAL
        await self._write_or_raise(REG_MODE, value)
        self.mode_raw = value

    async def async_set_deadzone(self, value: float) -> None:
        if self.fan_only_active:
            raise VariProStateError("dead zone is used by fan only")
        if not 0 <= value <= DEADZONE_MAX:
            raise ValueError(f"dead zone {value} outside 0-{DEADZONE_MAX}")
        raw = to_raw10(value)
        await self._write_or_raise(REG_DEADZONE, raw)
        self.deadzone_raw = raw
        self.deadzone_device = scale10(raw)

    def as_dict(self) -> dict[str, Any]:
        """State for diagnostics."""
        keys = (
            "mode_raw",
            "fan",
            "target_home",
            "target_away",
            "deadzone_raw",
            "deadzone_device",
            "app_state",
            "fan_actual",
            "room_temp",
            "setpoint_active",
            "fan_speed",
            "cooling_pid",
            "heating_pid",
            "water_in",
            "water_out",
            "alarm_raw",
            "fan_only_active",
            "fan_only_snapshot",
            "pending_restore",
            "failures",
        )
        return {k: getattr(self, k) for k in keys}

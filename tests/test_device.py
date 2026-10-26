"""Device logic tests (no Home Assistant needed)."""

import pytest

from custom_components.varipro import device as vd

from .fakes import Clock, FakeBus, FakeStore


@pytest.fixture
def env():
    bus, store, clock = FakeBus(), FakeStore(), Clock()
    dev = vd.VariProDevice(bus, store, clock=clock)
    return dev, bus, store, clock


async def poll(dev, clock):
    dev.last_poll.clear()
    await dev.async_refresh()
    clock.now += 1


def test_to_raw10_rounds_instead_of_truncating():
    assert vd.to_raw10(21.7) == 217
    assert vd.to_raw10(22.5) == 225


# --- Polling and derived state ---


async def test_initial_poll(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    assert dev.hvac_mode == "cool"
    assert dev.preset == "home"
    assert dev.target_temperature == 21.5
    assert dev.target_away == 26.0
    assert dev.fan == "auto"
    assert dev.room_temp == 22.3
    assert dev.hvac_action == "idle"
    assert dev.deadzone == 2.0
    assert dev.app_state == "normal"
    assert dev.alarm("filter") is False


async def test_away_preset_uses_away_target(env):
    dev, bus, store, clock = env
    bus.holding[0] = vd.MODE_AWAY
    await poll(dev, clock)
    assert dev.preset == "away"
    assert dev.target_temperature == 26.0


@pytest.mark.parametrize(
    ("cooling", "heating", "fan", "mode", "expected"),
    [
        (30, 0, 50, vd.MODE_NORMAL, "cooling"),
        (0, 20, 50, vd.MODE_NORMAL, "heating"),
        (0, 0, 50, vd.MODE_NORMAL, "fan"),
        (0, 0, 0, vd.MODE_NORMAL, "idle"),
        (30, 0, 50, vd.MODE_OFF, "off"),
    ],
)
async def test_action(env, cooling, heating, fan, mode, expected):
    dev, bus, store, clock = env
    bus.holding[0] = mode
    bus.input.update({9: cooling, 10: heating, 8: fan})
    await poll(dev, clock)
    assert dev.hvac_action == expected


async def test_alarm_bits(env):
    dev, bus, store, clock = env
    bus.input[102] = (1 << 0) | (1 << 15)
    await poll(dev, clock)
    assert dev.alarm("condensation") is True
    assert dev.alarm("filter") is True
    assert dev.alarm("fan1_rpm") is False


async def test_unavailable_only_after_repeated_failures(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    bus.offline = True
    for _ in range(vd.FAIL_LIMIT - 1):
        await poll(dev, clock)  # no exception: short WiFi drops do not flap
    with pytest.raises(vd.VariProConnectionError):
        await poll(dev, clock)
    assert not dev.available
    bus.offline = False
    await poll(dev, clock)
    assert dev.available


async def test_never_read_raises(env):
    dev, bus, store, clock = env
    bus.offline = True
    with pytest.raises(vd.VariProConnectionError):
        await poll(dev, clock)


# --- Write guard ---


async def test_stale_read_after_write_is_ignored(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    await dev.async_set_temperature(22.5)
    bus.holding[3] = 215  # the device still shows the old value
    await poll(dev, clock)
    assert dev.target_temperature == 22.5
    clock.now += vd.WRITE_GUARD_S
    await poll(dev, clock)
    assert dev.target_temperature == 21.5


# --- Commands ---


async def test_temperature_written_rounded(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    await dev.async_set_temperature(21.7)
    assert bus.writes[-1] == (vd.REG_TARGET_HOME, 217)


async def test_temperature_in_away_writes_away_register(env):
    dev, bus, store, clock = env
    bus.holding[0] = vd.MODE_AWAY
    await poll(dev, clock)
    await dev.async_set_temperature(23)
    assert bus.writes[-1] == (vd.REG_TARGET_AWAY, 230)


@pytest.mark.parametrize("value", [30, 10])
async def test_temperature_out_of_range(env, value):
    dev, bus, store, clock = env
    await poll(dev, clock)
    with pytest.raises(ValueError):
        await dev.async_set_temperature(value)
    assert bus.writes == []


async def test_failed_write_raises_and_keeps_state(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    bus.fail_writes.add(vd.REG_FAN)
    with pytest.raises(vd.VariProWriteError):
        await dev.async_set_fan_mode("high")
    assert dev.fan == "auto"


async def test_fan_off_not_supported(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    with pytest.raises(ValueError):
        await dev.async_set_fan_mode("off")


async def test_cool_keeps_away_and_off_cool_returns_home(env):
    dev, bus, store, clock = env
    bus.holding[0] = vd.MODE_AWAY
    await poll(dev, clock)
    await dev.async_set_hvac_mode("cool")
    assert bus.holding[0] == vd.MODE_AWAY
    await dev.async_set_hvac_mode("off")
    await dev.async_set_hvac_mode("cool")
    assert bus.holding[0] == vd.MODE_NORMAL


@pytest.mark.parametrize("value", [-1, 16])
async def test_deadzone_out_of_range(env, value):
    dev, bus, store, clock = env
    await poll(dev, clock)
    with pytest.raises(ValueError):
        await dev.async_set_deadzone(value)


# --- Fan only ---


async def test_fan_only_enter_and_exit(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    await dev.async_set_hvac_mode("fan_only")
    assert bus.holding[0] == vd.MODE_AWAY
    assert bus.holding[201] == vd.FANONLY_DEADZONE_RAW
    assert bus.holding[2] == vd.FAN_TO_RAW["medium"]
    assert dev.hvac_mode == "fan_only"
    assert dev.target_temperature is None
    assert dev.preset == "home"  # frozen
    assert store.data["deadzone_raw"] == 20

    await poll(dev, clock)  # 15.0 must not become the real dead zone
    assert dev.deadzone_raw == 20

    await dev.async_set_hvac_mode("cool")
    assert bus.holding[0] == vd.MODE_NORMAL
    assert bus.holding[201] == 20
    assert bus.holding[2] == vd.FAN_TO_RAW["auto"]
    assert dev.target_temperature == 21.5
    assert store.data is None


async def test_fan_only_from_away_returns_to_away(env):
    dev, bus, store, clock = env
    bus.holding[0] = vd.MODE_AWAY
    await poll(dev, clock)
    await dev.async_set_hvac_mode("fan_only")
    await dev.async_set_hvac_mode("cool")
    assert bus.holding[0] == vd.MODE_AWAY


async def test_fan_only_ended_from_panel(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    await dev.async_set_hvac_mode("fan_only")
    bus.holding[0] = vd.MODE_OFF  # turned off on the panel
    clock.now += vd.WRITE_GUARD_S
    await poll(dev, clock)
    assert not dev.fan_only_active
    assert dev.hvac_mode == "off"
    assert bus.holding[201] == 20
    assert bus.holding[2] == vd.FAN_TO_RAW["auto"]


async def test_fan_only_no_periodic_writes(env):
    """Unchanged values are not rewritten and panel changes are not undone."""
    dev, bus, store, clock = env
    await poll(dev, clock)
    await dev.async_set_hvac_mode("fan_only")
    bus.writes.clear()
    bus.holding[2] = vd.FAN_TO_RAW["high"]
    for _ in range(10):
        clock.now += 60
        await poll(dev, clock)
    assert bus.writes == []
    assert dev.fan == "high"


async def test_fan_only_rolls_back_on_partial_failure(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    bus.fail_writes.add(vd.REG_MODE)
    with pytest.raises(vd.VariProWriteError):
        await dev.async_set_hvac_mode("fan_only")
    assert not dev.fan_only_active
    assert bus.holding[201] == 20
    assert store.data is None


async def test_commands_rejected_in_fan_only(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    await dev.async_set_hvac_mode("fan_only")
    bus.writes.clear()
    with pytest.raises(vd.VariProStateError):
        await dev.async_set_temperature(22)
    with pytest.raises(vd.VariProStateError):
        await dev.async_set_deadzone(3)
    with pytest.raises(vd.VariProStateError):
        await dev.async_set_fan_mode("auto")
    assert bus.writes == []


async def test_preset_ends_fan_only(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    await dev.async_set_hvac_mode("fan_only")
    await dev.async_set_preset("away")
    assert not dev.fan_only_active
    assert bus.holding[0] == vd.MODE_AWAY
    assert bus.holding[201] == 20


async def test_failed_exit_retried(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    await dev.async_set_hvac_mode("fan_only")
    bus.fail_writes.add(vd.REG_DEADZONE)
    await dev.async_set_hvac_mode("cool")
    assert dev.pending_restore is not None
    assert store.data is not None
    bus.fail_writes.clear()
    await poll(dev, clock)  # retried after a successful poll
    assert bus.holding[201] == 20
    assert dev.pending_restore is None
    assert dev.deadzone_raw == 20


# --- Restart behaviour ---


async def test_restart_continues_fan_only(env):
    dev, bus, store, clock = env
    await poll(dev, clock)
    await dev.async_set_hvac_mode("fan_only")
    # Home Assistant restarts: a new device object with the same store
    dev2 = vd.VariProDevice(bus, store, clock=clock)
    bus.writes.clear()
    await dev2.async_initialize()
    await poll(dev2, clock)
    assert dev2.hvac_mode == "fan_only"
    assert bus.writes == []
    await dev2.async_set_hvac_mode("cool")
    assert bus.holding[201] == 20
    assert bus.holding[2] == vd.FAN_TO_RAW["auto"]


async def test_restart_restores_when_device_left_fan_only(env):
    dev, bus, store, clock = env
    bus.holding.update({0: vd.MODE_OFF, 201: 150, 2: 2})  # turned off on the panel meanwhile
    store.data = {"preset": "home", "mode_raw": 2, "deadzone_raw": 30, "fan": "low"}
    await dev.async_initialize()
    await poll(dev, clock)
    assert not dev.fan_only_active
    assert bus.holding[0] == vd.MODE_OFF  # panel choice kept
    assert bus.holding[201] == 30
    assert bus.holding[2] == vd.FAN_TO_RAW["low"]
    assert store.data is None


async def test_initialize_raises_when_offline(env):
    dev, bus, store, clock = env
    store.data = {"preset": "home", "deadzone_raw": 20, "fan": "auto"}
    bus.offline = True
    with pytest.raises(vd.VariProConnectionError):
        await dev.async_initialize()


async def test_restore_normal_on_removal(env):
    dev, bus, store, clock = env
    bus.holding[0] = vd.MODE_OFF
    await poll(dev, clock)
    await dev.async_set_hvac_mode("fan_only")
    await dev.async_restore_normal()
    assert bus.holding[0] == vd.MODE_OFF  # exactly the previous state
    assert bus.holding[201] == 20
    assert store.data is None

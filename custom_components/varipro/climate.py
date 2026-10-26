"""Climate entity for the VariPro integration."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    PRESET_AWAY,
    PRESET_HOME,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VariProConfigEntry, VariProCoordinator
from .entity import VariProEntity

PARALLEL_UPDATES = 1

HVAC_MODE_MAP = {"off": HVACMode.OFF, "cool": HVACMode.COOL, "fan_only": HVACMode.FAN_ONLY}
HVAC_ACTION_MAP = {
    "off": HVACAction.OFF,
    "cooling": HVACAction.COOLING,
    "heating": HVACAction.HEATING,
    "fan": HVACAction.FAN,
    "idle": HVACAction.IDLE,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VariProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([VariProClimate(entry.runtime_data)])


class VariProClimate(VariProEntity, ClimateEntity):
    """The room controller: off / cool / fan only, presets home and away."""

    _attr_name = None
    _attr_translation_key = "varipro"
    _attr_hvac_modes = list(HVAC_MODE_MAP.values())
    _attr_preset_modes = [PRESET_HOME, PRESET_AWAY]
    _attr_fan_modes = [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    _attr_target_temperature_step = 0.5
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: VariProCoordinator) -> None:
        super().__init__(coordinator, "climate")
        self._attr_min_temp = coordinator.device.min_temp
        self._attr_max_temp = coordinator.device.max_temp

    @property
    @override
    def hvac_mode(self) -> HVACMode | None:
        mode = self.device.hvac_mode
        return None if mode is None else HVAC_MODE_MAP[mode]

    @property
    @override
    def hvac_action(self) -> HVACAction | None:
        action = self.device.hvac_action
        return None if action is None else HVAC_ACTION_MAP[action]

    @property
    @override
    def current_temperature(self) -> float | None:
        return self.device.room_temp

    @property
    @override
    def target_temperature(self) -> float | None:
        return self.device.target_temperature

    @property
    @override
    def preset_mode(self) -> str | None:
        return self.device.preset if self.device.mode_raw is not None else None

    @property
    @override
    def fan_mode(self) -> str | None:
        return self.device.fan

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.coordinator.async_command(lambda: self.device.async_set_hvac_mode(hvac_mode.value))

    @override
    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.COOL)

    @override
    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self.coordinator.async_command(lambda: self.device.async_set_temperature(temperature))

    @override
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.coordinator.async_command(lambda: self.device.async_set_preset(preset_mode))

    @override
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator.async_command(lambda: self.device.async_set_fan_mode(fan_mode))

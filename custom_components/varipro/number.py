"""Away dead zone number for the VariPro integration."""

from __future__ import annotations

from typing import override

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VariProConfigEntry, VariProCoordinator
from .device import DEADZONE_MAX
from .entity import VariProEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VariProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([VariProDeadzone(entry.runtime_data)])


class VariProDeadzone(VariProEntity, NumberEntity):
    """Extended dead zone of the away mode (holding 4x00202)."""

    _attr_translation_key = "away_deadzone"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = DEADZONE_MAX
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: VariProCoordinator) -> None:
        super().__init__(coordinator, "away_deadzone")

    @property
    @override
    def native_value(self) -> float | None:
        return self.device.deadzone

    @override
    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_command(lambda: self.device.async_set_deadzone(value))

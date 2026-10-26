"""Alarm binary sensors for the VariPro integration."""

from __future__ import annotations

from typing import override

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VariProConfigEntry, VariProCoordinator
from .device import ALARM_BITS
from .entity import VariProEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VariProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(VariProAlarm(entry.runtime_data, key) for key in ALARM_BITS.values())


class VariProAlarm(VariProEntity, BinarySensorEntity):
    """One bit of the alarm register 3x00103."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: VariProCoordinator, key: str) -> None:
        super().__init__(coordinator, f"alarm_{key}")
        self._key = key
        self._attr_translation_key = f"alarm_{key}"

    @property
    @override
    def is_on(self) -> bool | None:
        return self.device.alarm(self._key)

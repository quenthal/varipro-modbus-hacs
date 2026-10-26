"""Sensors for the VariPro integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VariProConfigEntry, VariProCoordinator
from .device import APP_STATES, VariProDevice
from .entity import VariProEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VariProSensorDescription(SensorEntityDescription):
    value_fn: Callable[[VariProDevice], float | int | str | None]


def _temperature(key: str, fn: Callable[[VariProDevice], float | None], diagnostic: bool = True):
    return VariProSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC if diagnostic else None,
        value_fn=fn,
    )


def _percentage(key: str, fn: Callable[[VariProDevice], int | None]):
    return VariProSensorDescription(
        key=key,
        translation_key=key,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=fn,
    )


SENSORS: tuple[VariProSensorDescription, ...] = (
    _temperature("room_temperature", lambda d: d.room_temp, diagnostic=False),
    _temperature("setpoint_active", lambda d: d.setpoint_active),
    _temperature("target_home", lambda d: d.target_home),
    _temperature("target_away", lambda d: d.target_away),
    _temperature("water_in", lambda d: d.water_in),
    _temperature("water_out", lambda d: d.water_out),
    _percentage("fan_speed", lambda d: d.fan_speed),
    _percentage("cooling_output", lambda d: d.cooling_pid),
    _percentage("heating_output", lambda d: d.heating_pid),
    VariProSensorDescription(
        key="app_state",
        translation_key="app_state",
        device_class=SensorDeviceClass.ENUM,
        options=[*APP_STATES.values(), "unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.app_state,
    ),
    VariProSensorDescription(
        key="fan_actual",
        translation_key="fan_actual",
        device_class=SensorDeviceClass.ENUM,
        options=["off", "low", "medium", "high", "auto", "unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.fan_actual,
    ),
    VariProSensorDescription(
        key="alarm_raw",
        translation_key="alarm_raw",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.alarm_raw,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VariProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(VariProSensor(entry.runtime_data, description) for description in SENSORS)


class VariProSensor(VariProEntity, SensorEntity):
    entity_description: VariProSensorDescription

    def __init__(self, coordinator: VariProCoordinator, description: VariProSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> float | int | str | None:
        return self.entity_description.value_fn(self.device)

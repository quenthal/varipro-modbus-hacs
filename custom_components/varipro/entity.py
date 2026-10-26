"""Base entity for the VariPro integration."""

from __future__ import annotations

from typing import override

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import VariProCoordinator
from .device import VariProDevice


class VariProEntity(CoordinatorEntity[VariProCoordinator]):
    """Entity of one VariPro room controller."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VariProCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="VariPro",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def device(self) -> VariProDevice:
        return self.coordinator.device

    @property
    @override
    def available(self) -> bool:
        return super().available and self.device.available

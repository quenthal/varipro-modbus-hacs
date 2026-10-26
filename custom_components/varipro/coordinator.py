"""Data update coordinator for the VariPro integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from typing import Any, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL, STORAGE_VERSION
from .device import (
    VariProConnectionError,
    VariProDevice,
    VariProStateError,
    VariProWriteError,
)

_LOGGER = logging.getLogger(__name__)

type VariProConfigEntry = ConfigEntry[VariProCoordinator]


class SnapshotStore:
    """Fan-only snapshot in Home Assistant's .storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.fan_only")

    async def load(self) -> dict[str, Any] | None:
        return await self._store.async_load() or None

    async def save(self, snapshot: dict[str, Any]) -> None:
        await self._store.async_save(snapshot)

    async def clear(self) -> None:
        await self._store.async_remove()


class VariProCoordinator(DataUpdateCoordinator[None]):
    """Polls the device; entities read their state from coordinator.device."""

    config_entry: VariProConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: VariProConfigEntry, device: VariProDevice) -> None:
        self.device = device
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    @override
    async def _async_update_data(self) -> None:
        try:
            await self.device.async_refresh()
        except VariProConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

    async def async_command(self, command: Callable[[], Awaitable[None]]) -> None:
        """Run a device command and push the new state to the entities."""
        try:
            await command()
        except VariProStateError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_in_fan_only",
                translation_placeholders={"error": str(err)},
            ) from err
        except VariProWriteError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            self.async_update_listeners()

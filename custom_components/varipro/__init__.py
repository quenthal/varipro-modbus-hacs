"""The VariPro integration: Chiller VariPro room controller over Modbus TCP."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_SLAVE,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DOMAIN,
)
from .coordinator import SnapshotStore, VariProConfigEntry, VariProCoordinator
from .device import VariProConnectionError, VariProDevice
from .modbus import VariProModbus

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.NUMBER, Platform.SENSOR]


def _create_device(hass: HomeAssistant, entry: VariProConfigEntry) -> VariProDevice:
    bus = VariProModbus(entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_SLAVE])
    return VariProDevice(
        bus,
        SnapshotStore(hass, entry.entry_id),
        min_temp=entry.options.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP),
        max_temp=entry.options.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP),
    )


async def async_setup_entry(hass: HomeAssistant, entry: VariProConfigEntry) -> bool:
    """Set up VariPro from a config entry."""
    device = _create_device(hass, entry)
    try:
        await device.async_initialize()
    except VariProConnectionError as err:
        device.bus.close()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    coordinator = VariProCoordinator(hass, entry, device)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        device.bus.close()
        raise
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VariProConfigEntry) -> bool:
    """Unload a config entry. Fan only stays active and continues on the next setup."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.device.bus.close()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: VariProConfigEntry) -> None:
    """Integration removed: end fan only on the device and drop the snapshot."""
    device = _create_device(hass, entry)
    try:
        snapshot = await device.store.load()
        if snapshot:
            await device.async_initialize()
            await device.async_restore_normal()
            await device.store.clear()
    except VariProConnectionError:
        _LOGGER.warning("Could not restore the pre-fan-only state: device not reachable")
    finally:
        device.bus.close()

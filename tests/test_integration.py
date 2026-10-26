"""Home Assistant level tests: config flow, setup, entities, services."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_HVAC_ACTION,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.varipro import device as vd
from custom_components.varipro.const import CONF_MAX_TEMP, CONF_MIN_TEMP, CONF_SLAVE, DOMAIN

from .fakes import FakeBus

HOST = "gateway.example"  # invented
CLIMATE = "climate.varipro"
USER_INPUT = {CONF_HOST: HOST, CONF_PORT: 502, CONF_SLAVE: 1}


@pytest.fixture
def bus():
    """One in-memory controller shared by the integration and the config flow."""
    fake = FakeBus()
    with (
        patch("custom_components.varipro.VariProModbus", return_value=fake),
        patch("custom_components.varipro.config_flow.VariProModbus", return_value=fake),
    ):
        yield fake


async def _setup(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options=options or {}, unique_id=f"{HOST}:502:1")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _climate(hass, service, **data):
    await hass.services.async_call(CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: CLIMATE, **data}, blocking=True)


# --- Config flow ---


async def test_config_flow(hass: HomeAssistant, bus) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == f"{HOST}:502:1"


async def test_config_flow_cannot_connect(hass: HomeAssistant, bus) -> None:
    bus.offline = True
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    bus.offline = False  # recovers in the same flow
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_config_flow_already_configured(hass: HomeAssistant, bus) -> None:
    await _setup(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant, bus) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MIN_TEMP: 25, CONF_MAX_TEMP: 20}
    )
    assert result["errors"] == {"base": "min_above_max"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MIN_TEMP: 17, CONF_MAX_TEMP: 26}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = hass.states.get(CLIMATE)
    assert state.attributes["min_temp"] == 17
    assert state.attributes["max_temp"] == 26


# --- Setup and entities ---


async def test_setup_and_states(hass: HomeAssistant, bus, entity_registry: er.EntityRegistry) -> None:
    bus.input[102] = 1 << 15  # filter reminder
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.COOL
    assert state.attributes[ATTR_TEMPERATURE] == 21.5
    assert state.attributes["current_temperature"] == 22.3
    assert state.attributes[ATTR_PRESET_MODE] == "home"
    assert state.attributes[ATTR_FAN_MODE] == "auto"
    assert state.attributes[ATTR_HVAC_ACTION] == "idle"

    assert hass.states.get("sensor.varipro_room_temperature").state == "22.3"
    assert hass.states.get("sensor.varipro_supply_water_temperature").state == "8.0"
    assert hass.states.get("sensor.varipro_application_state").state == "normal"
    assert hass.states.get("binary_sensor.varipro_filter_cleaning").state == "on"
    assert hass.states.get("binary_sensor.varipro_condensation_alarm").state == "off"
    assert hass.states.get("number.varipro_away_dead_zone").state == "2.0"

    water = entity_registry.async_get("sensor.varipro_supply_water_temperature")
    assert water.entity_category == "diagnostic"
    assert water.unique_id == f"{entry.entry_id}_water_in"


async def test_setup_retries_when_offline(hass: HomeAssistant, bus) -> None:
    bus.offline = True
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_climate_services(hass: HomeAssistant, bus) -> None:
    await _setup(hass)
    await _climate(hass, "set_temperature", temperature=22.5)
    assert bus.holding[vd.REG_TARGET_HOME] == 225
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 22.5

    await _climate(hass, "set_preset_mode", preset_mode="away")
    state = hass.states.get(CLIMATE)
    assert bus.holding[vd.REG_MODE] == vd.MODE_AWAY
    assert state.attributes[ATTR_TEMPERATURE] == 26.0

    await _climate(hass, "set_fan_mode", fan_mode="high")
    assert bus.holding[vd.REG_FAN] == 3

    await _climate(hass, "turn_off")
    assert hass.states.get(CLIMATE).state == HVACMode.OFF
    assert bus.holding[vd.REG_MODE] == vd.MODE_OFF


async def test_fan_only_via_climate(hass: HomeAssistant, bus) -> None:
    await _setup(hass)
    await _climate(hass, "set_hvac_mode", hvac_mode=HVACMode.FAN_ONLY)
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.FAN_ONLY
    assert state.attributes[ATTR_TEMPERATURE] is None
    assert bus.holding[vd.REG_DEADZONE] == vd.FANONLY_DEADZONE_RAW

    with pytest.raises(ServiceValidationError):
        await _climate(hass, "set_temperature", temperature=22)

    await _climate(hass, "set_hvac_mode", hvac_mode=HVACMode.COOL)
    assert bus.holding[vd.REG_DEADZONE] == 20
    assert hass.states.get(CLIMATE).state == HVACMode.COOL


async def test_write_failure_raises(hass: HomeAssistant, bus) -> None:
    await _setup(hass)
    bus.fail_writes.add(vd.REG_FAN)
    with pytest.raises(HomeAssistantError):
        await _climate(hass, "set_fan_mode", fan_mode="high")
    assert hass.states.get(CLIMATE).attributes[ATTR_FAN_MODE] == "auto"


async def test_deadzone_number(hass: HomeAssistant, bus) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.varipro_away_dead_zone", "value": 3.5},
        blocking=True,
    )
    assert bus.holding[vd.REG_DEADZONE] == 35


async def test_fan_only_survives_reload(hass: HomeAssistant, bus) -> None:
    entry = await _setup(hass)
    await _climate(hass, "set_hvac_mode", hvac_mode=HVACMode.FAN_ONLY)
    bus.writes.clear()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state == HVACMode.FAN_ONLY
    assert bus.writes == []


async def test_remove_entry_restores_fan_only(hass: HomeAssistant, bus) -> None:
    entry = await _setup(hass)
    await _climate(hass, "set_hvac_mode", hvac_mode=HVACMode.FAN_ONLY)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert bus.holding[vd.REG_MODE] == vd.MODE_NORMAL
    assert bus.holding[vd.REG_DEADZONE] == 20
    assert bus.holding[vd.REG_FAN] == vd.FAN_TO_RAW["auto"]


async def test_unload_closes_connection(hass: HomeAssistant, bus) -> None:
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert bus.closed


async def test_diagnostics_redacts_host(hass: HomeAssistant, bus) -> None:
    from custom_components.varipro.diagnostics import async_get_config_entry_diagnostics

    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"][CONF_HOST] == "**REDACTED**"
    assert diag["device"]["mode_raw"] == vd.MODE_NORMAL

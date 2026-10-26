"""Config flow for the VariPro integration."""

from __future__ import annotations

import logging
from typing import Any, override

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)
import probatio

from .const import (
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_SLAVE,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_PORT,
    DEFAULT_SLAVE,
    DOMAIN,
)
from .device import REG_MODE
from .modbus import VariProModbus

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): str,
        probatio.Required(CONF_PORT, default=DEFAULT_PORT): probatio.All(
            probatio.Coerce(int), probatio.Range(min=1, max=65535)
        ),
        probatio.Required(CONF_SLAVE, default=DEFAULT_SLAVE): probatio.All(
            probatio.Coerce(int), probatio.Range(min=1, max=247)
        ),
    }
)

_TEMP_SELECTOR = NumberSelector(
    NumberSelectorConfig(min=5, max=35, step=0.5, unit_of_measurement="°C", mode=NumberSelectorMode.BOX)
)


async def _async_can_read(host: str, port: int, slave: int) -> bool:
    """True if the controller answers a read of the application mode."""
    bus = VariProModbus(host, port, slave)
    try:
        return await bus.read("holding", REG_MODE, 1) is not None
    finally:
        bus.close()


class VariProConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VariPro."""

    VERSION = 1

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port, slave = user_input[CONF_PORT], user_input[CONF_SLAVE]
            await self.async_set_unique_id(f"{host}:{port}:{slave}")
            self._abort_if_unique_id_configured()
            try:
                ok = await _async_can_read(host, port, slave)
            except Exception:
                _LOGGER.exception("Unexpected error")
                errors["base"] = "unknown"
            else:
                if ok:
                    return self.async_create_entry(
                        title="VariPro",
                        data={CONF_HOST: host, CONF_PORT: port, CONF_SLAVE: slave},
                    )
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> VariProOptionsFlow:
        return VariProOptionsFlow()


class VariProOptionsFlow(OptionsFlowWithReload):
    """Temperature limits of the climate entity."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_MIN_TEMP] < user_input[CONF_MAX_TEMP]:
                return self.async_create_entry(data=user_input)
            errors["base"] = "min_above_max"

        options = self.config_entry.options
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_MIN_TEMP, default=options.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)
                ): _TEMP_SELECTOR,
                probatio.Required(
                    CONF_MAX_TEMP, default=options.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)
                ): _TEMP_SELECTOR,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

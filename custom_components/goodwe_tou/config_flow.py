"""Config and options flow for the GoodWe Time-of-Use integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_COMM_ADDR,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_COMM_ADDR,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    WORK_MODE_REGISTER,
)
from .goodwe_udp import GoodweError, GoodweUdpClient

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
            int, vol.Range(min=1, max=65535)
        ),
        vol.Required(CONF_COMM_ADDR, default=DEFAULT_COMM_ADDR): vol.All(
            int, vol.Range(min=1, max=255)
        ),
    }
)


class GoodweTouConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            await self.async_set_unique_id(host.lower())
            self._abort_if_unique_id_configured()

            client = GoodweUdpClient(
                host=host,
                port=user_input[CONF_PORT],
                comm_addr=user_input[CONF_COMM_ADDR],
                timeout=DEFAULT_TIMEOUT,
            )
            try:
                await self.hass.async_add_executor_job(
                    client.read_registers, WORK_MODE_REGISTER, 1
                )
            except GoodweError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error while probing the inverter")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"GoodWe Time-of-Use ({host})", data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GoodweTouOptionsFlow()


class GoodweTouOptionsFlow(OptionsFlow):
    """Handle the options flow."""

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_SCAN_INTERVAL, default=current): vol.All(
                    int, vol.Range(min=0, max=3600)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

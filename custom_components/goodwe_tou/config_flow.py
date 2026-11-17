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
    CONF_CAPACITY_KWH,
    CONF_CHARGE_KW,
    CONF_CHARGE_WINDOWS,
    CONF_COMM_ADDR,
    CONF_DISCHARGE_KW,
    CONF_DISCHARGE_WINDOWS,
    CONF_EXPORT_HOURS,
    CONF_EXPORT_LIMIT,
    CONF_HOST,
    CONF_LOG_SIZE,
    CONF_MIN_CELL_VOLTAGE,
    CONF_MIN_SOC,
    CONF_PORT,
    CONF_POWER_KW,
    CONF_PRICE_ENTITY,
    CONF_PV_ENTITY,
    CONF_PV_FACTOR,
    CONF_RECHECK_MINUTES,
    CONF_RTE,
    CONF_SCAN_INTERVAL,
    CONF_SOC_ENTITY,
    CONF_STANDBY_KW,
    CONF_TARGET_SOC,
    CONF_WINDOW_POWER,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_CHARGE_KW,
    DEFAULT_CHARGE_WINDOWS,
    DEFAULT_COMM_ADDR,
    DEFAULT_DISCHARGE_KW,
    DEFAULT_DISCHARGE_WINDOWS,
    DEFAULT_EXPORT_HOURS,
    DEFAULT_EXPORT_LIMIT,
    DEFAULT_HOST,
    DEFAULT_LOG_SIZE,
    DEFAULT_MIN_CELL_VOLTAGE,
    DEFAULT_MIN_SOC,
    DEFAULT_PORT,
    DEFAULT_POWER_KW,
    DEFAULT_PRICE_ENTITY,
    DEFAULT_PV_ENTITY,
    DEFAULT_PV_FACTOR,
    DEFAULT_RECHECK_MINUTES,
    DEFAULT_RTE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOC_ENTITY,
    DEFAULT_STANDBY_KW,
    DEFAULT_TARGET_SOC,
    DEFAULT_TIMEOUT,
    DEFAULT_WINDOW_POWER,
    DOMAIN,
    RECHECK_CHOICES,
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

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
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

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        def default(key: str, fallback: object) -> object:
            return options.get(key, fallback)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=default(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=0, max=3600)),
                vol.Optional(
                    CONF_PRICE_ENTITY,
                    default=default(CONF_PRICE_ENTITY, DEFAULT_PRICE_ENTITY),
                ): str,
                vol.Optional(
                    CONF_SOC_ENTITY,
                    default=default(CONF_SOC_ENTITY, DEFAULT_SOC_ENTITY),
                ): str,
                vol.Optional(
                    CONF_PV_ENTITY,
                    default=default(CONF_PV_ENTITY, DEFAULT_PV_ENTITY),
                ): str,
                vol.Optional(
                    CONF_CAPACITY_KWH,
                    default=default(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH),
                ): vol.All(vol.Coerce(float), vol.Range(min=1, max=200)),
                vol.Optional(
                    CONF_POWER_KW,
                    default=default(CONF_POWER_KW, DEFAULT_POWER_KW),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=100)),
                vol.Optional(
                    CONF_CHARGE_KW,
                    default=default(CONF_CHARGE_KW, DEFAULT_CHARGE_KW),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Optional(
                    CONF_DISCHARGE_KW,
                    default=default(CONF_DISCHARGE_KW, DEFAULT_DISCHARGE_KW),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Optional(
                    CONF_STANDBY_KW,
                    default=default(CONF_STANDBY_KW, DEFAULT_STANDBY_KW),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Optional(
                    CONF_EXPORT_HOURS,
                    default=default(CONF_EXPORT_HOURS, DEFAULT_EXPORT_HOURS),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=24)),
                vol.Optional(
                    CONF_EXPORT_LIMIT,
                    default=default(CONF_EXPORT_LIMIT, DEFAULT_EXPORT_LIMIT),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
                vol.Optional(
                    CONF_TARGET_SOC,
                    default=default(CONF_TARGET_SOC, DEFAULT_TARGET_SOC),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Optional(
                    CONF_MIN_SOC,
                    default=default(CONF_MIN_SOC, DEFAULT_MIN_SOC),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Optional(
                    CONF_PV_FACTOR,
                    default=default(CONF_PV_FACTOR, DEFAULT_PV_FACTOR),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
                vol.Optional(
                    CONF_RTE,
                    default=default(CONF_RTE, DEFAULT_RTE),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=1)),
                vol.Optional(
                    CONF_CHARGE_WINDOWS,
                    default=default(CONF_CHARGE_WINDOWS, DEFAULT_CHARGE_WINDOWS),
                ): vol.All(int, vol.Range(min=0, max=4)),
                vol.Optional(
                    CONF_DISCHARGE_WINDOWS,
                    default=default(CONF_DISCHARGE_WINDOWS, DEFAULT_DISCHARGE_WINDOWS),
                ): vol.All(int, vol.Range(min=0, max=4)),
                vol.Optional(
                    CONF_WINDOW_POWER,
                    default=default(CONF_WINDOW_POWER, DEFAULT_WINDOW_POWER),
                ): vol.All(int, vol.Range(min=1, max=100)),
                vol.Optional(
                    CONF_RECHECK_MINUTES,
                    default=default(CONF_RECHECK_MINUTES, DEFAULT_RECHECK_MINUTES),
                ): vol.In(RECHECK_CHOICES),
                vol.Optional(
                    CONF_LOG_SIZE,
                    default=default(CONF_LOG_SIZE, DEFAULT_LOG_SIZE),
                ): vol.All(int, vol.Range(min=10, max=10000)),
                vol.Optional(
                    CONF_MIN_CELL_VOLTAGE,
                    default=default(CONF_MIN_CELL_VOLTAGE, DEFAULT_MIN_CELL_VOLTAGE),
                ): vol.All(int, vol.Range(min=0, max=4000)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

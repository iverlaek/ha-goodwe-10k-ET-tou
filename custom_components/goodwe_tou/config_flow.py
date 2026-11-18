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
from homeassistant.helpers import selector

from .const import (
    CONF_ADAPTIVE_LOAD,
    CONF_CAPACITY_KWH,
    CONF_CHARGE_KW,
    CONF_CHARGE_WINDOWS,
    CONF_COMM_ADDR,
    CONF_DEGREE_BASE_C,
    CONF_DEVIATION_SIZE,
    CONF_DISCHARGE_KW,
    CONF_DISCHARGE_WINDOWS,
    CONF_EXPORT_HOURS,
    CONF_EXPORT_LIMIT,
    CONF_HOST,
    CONF_LANGUAGE,
    CONF_LOAD_ENTITY,
    CONF_LOG_SIZE,
    CONF_MANUAL_WINDOW,
    CONF_MIN_CELL_VOLTAGE,
    CONF_MIN_SOC,
    CONF_PORT,
    CONF_POWER_KW,
    CONF_PRICE_ENTITY,
    CONF_PRICE_SOURCE,
    CONF_PV_ENTITY,
    CONF_PV_FACTOR,
    CONF_PV_POWER_ENTITY,
    CONF_RECHECK_MINUTES,
    CONF_RTE,
    CONF_SCAN_INTERVAL,
    CONF_SOC_ENTITY,
    CONF_STANDBY_KW,
    CONF_TARGET_SOC,
    CONF_TEMP_COEFF,
    CONF_TIBBER_HOME,
    CONF_TIBBER_PRICES,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_POWER,
    DEFAULT_ADAPTIVE_LOAD,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_CHARGE_KW,
    DEFAULT_CHARGE_WINDOWS,
    DEFAULT_COMM_ADDR,
    DEFAULT_DEGREE_BASE_C,
    DEFAULT_DEVIATION_SIZE,
    DEFAULT_DISCHARGE_KW,
    DEFAULT_DISCHARGE_WINDOWS,
    DEFAULT_EXPORT_HOURS,
    DEFAULT_EXPORT_LIMIT,
    DEFAULT_HOST,
    DEFAULT_LANGUAGE,
    DEFAULT_LOAD_ENTITY,
    DEFAULT_LOG_SIZE,
    DEFAULT_MANUAL_WINDOW,
    DEFAULT_MIN_CELL_VOLTAGE,
    DEFAULT_MIN_SOC,
    DEFAULT_PORT,
    DEFAULT_POWER_KW,
    DEFAULT_PRICE_ENTITY,
    DEFAULT_PRICE_SOURCE,
    DEFAULT_PV_ENTITY,
    DEFAULT_PV_FACTOR,
    DEFAULT_PV_POWER_ENTITY,
    DEFAULT_RECHECK_MINUTES,
    DEFAULT_RTE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOC_ENTITY,
    DEFAULT_STANDBY_KW,
    DEFAULT_TARGET_SOC,
    DEFAULT_TEMP_COEFF,
    DEFAULT_TIBBER_HOME,
    DEFAULT_TIBBER_PRICES,
    DEFAULT_TIMEOUT,
    DEFAULT_WEATHER_ENTITY,
    DEFAULT_WINDOW_POWER,
    DOMAIN,
    LANGUAGE_EN,
    LANGUAGE_NL,
    PRICE_SOURCE_ENTITY,
    PRICE_SOURCE_TIBBER,
    RECHECK_CHOICES,
    WORK_MODE_REGISTER,
)
from .goodwe_udp import GoodweError, GoodweUdpClient
from .i18n import resolve_language

_LOGGER = logging.getLogger(__name__)


def _language_selector() -> selector.SelectSelector:
    """A dropdown with the languages the integration speaks (endonyms)."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=LANGUAGE_NL, label="Nederlands"),
                selector.SelectOptionDict(value=LANGUAGE_EN, label="English"),
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _user_schema(language: str) -> vol.Schema:
    """The install form: how to reach the inverter, and which language to speak."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                int, vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_COMM_ADDR, default=DEFAULT_COMM_ADDR): vol.All(
                int, vol.Range(min=1, max=255)
            ),
            vol.Required(CONF_LANGUAGE, default=language): _language_selector(),
        }
    )


class GoodweTouConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        # Pre-select the Home Assistant language when we speak it.
        default_language = resolve_language(self.hass.config.language)
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
                user_input[CONF_LANGUAGE] = resolve_language(
                    user_input.get(CONF_LANGUAGE, default_language)
                )
                return self.async_create_entry(
                    title=f"GoodWe Time-of-Use ({host})", data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(default_language),
            errors=errors,
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
        data = self.config_entry.data

        def default(key: str, fallback: object) -> object:
            return options.get(key, data.get(key, fallback))

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_LANGUAGE,
                    default=resolve_language(
                        default(CONF_LANGUAGE, DEFAULT_LANGUAGE)
                    ),
                ): _language_selector(),
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=default(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=0, max=3600)),
                vol.Optional(
                    CONF_PRICE_SOURCE,
                    default=default(CONF_PRICE_SOURCE, DEFAULT_PRICE_SOURCE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=PRICE_SOURCE_ENTITY,
                                label="ENTSO-e / price entity",
                            ),
                            selector.SelectOptionDict(
                                value=PRICE_SOURCE_TIBBER,
                                label="Tibber",
                            ),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
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
                    CONF_ADAPTIVE_LOAD,
                    default=default(CONF_ADAPTIVE_LOAD, DEFAULT_ADAPTIVE_LOAD),
                ): bool,
                vol.Optional(
                    CONF_WEATHER_ENTITY,
                    default=default(CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENTITY),
                ): str,
                vol.Optional(
                    CONF_LOAD_ENTITY,
                    default=default(CONF_LOAD_ENTITY, DEFAULT_LOAD_ENTITY),
                ): str,
                vol.Optional(
                    CONF_PV_POWER_ENTITY,
                    default=default(CONF_PV_POWER_ENTITY, DEFAULT_PV_POWER_ENTITY),
                ): str,
                vol.Optional(
                    CONF_TEMP_COEFF,
                    default=default(CONF_TEMP_COEFF, DEFAULT_TEMP_COEFF),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
                vol.Optional(
                    CONF_DEGREE_BASE_C,
                    default=default(CONF_DEGREE_BASE_C, DEFAULT_DEGREE_BASE_C),
                ): vol.All(vol.Coerce(float), vol.Range(min=-20, max=30)),
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
                    CONF_MANUAL_WINDOW,
                    default=default(CONF_MANUAL_WINDOW, DEFAULT_MANUAL_WINDOW),
                ): vol.All(int, vol.Range(min=1, max=4)),
                vol.Optional(
                    CONF_RECHECK_MINUTES,
                    default=default(CONF_RECHECK_MINUTES, DEFAULT_RECHECK_MINUTES),
                ): vol.In(RECHECK_CHOICES),
                vol.Optional(
                    CONF_LOG_SIZE,
                    default=default(CONF_LOG_SIZE, DEFAULT_LOG_SIZE),
                ): vol.All(int, vol.Range(min=10, max=10000)),
                vol.Optional(
                    CONF_DEVIATION_SIZE,
                    default=default(CONF_DEVIATION_SIZE, DEFAULT_DEVIATION_SIZE),
                ): vol.All(int, vol.Range(min=24, max=10000)),
                vol.Optional(
                    CONF_MIN_CELL_VOLTAGE,
                    default=default(CONF_MIN_CELL_VOLTAGE, DEFAULT_MIN_CELL_VOLTAGE),
                ): vol.All(int, vol.Range(min=0, max=4000)),
                vol.Optional(
                    CONF_TIBBER_PRICES,
                    default=default(CONF_TIBBER_PRICES, DEFAULT_TIBBER_PRICES),
                ): bool,
                vol.Optional(
                    CONF_TIBBER_HOME,
                    default=default(CONF_TIBBER_HOME, DEFAULT_TIBBER_HOME),
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

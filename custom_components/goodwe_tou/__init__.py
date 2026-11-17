"""The GoodWe Time-of-Use (Eco Mode) integration."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_CAPACITY_KWH,
    CONF_COMM_ADDR,
    CONF_HOST,
    CONF_MIN_SOC,
    CONF_PORT,
    CONF_POWER_KW,
    CONF_PRICE_ENTITY,
    CONF_PV_ENTITY,
    CONF_PV_FACTOR,
    CONF_SOC_ENTITY,
    CONF_TARGET_SOC,
    DEFAULT_COMM_ADDR,
    DEFAULT_PORT,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .coordinator import GoodweTouCoordinator, window_to_dict
from .goodwe_udp import GoodweUdpClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.TIME,
    Platform.NUMBER,
    Platform.TEXT,
    Platform.BUTTON,
    Platform.SWITCH,
]

SERVICE_SET_CHEAPEST_HOURS = "set_cheapest_hours"
SERVICE_CALCULATE_CHEAPEST_HOURS = "calculate_cheapest_hours"
SERVICE_SET_WINDOWS = "set_windows"

_URL_PATH = "/goodwe_tou"
_CARD_FILE = "goodwe-tou-card.js"
_PLANNER_FILE = "goodwe-tou-planner-card.js"
_FRONTEND_KEY = "__frontend__"

SERVICE_SET_CHEAPEST_HOURS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PRICE_ENTITY): cv.entity_id,
        vol.Optional(CONF_SOC_ENTITY): cv.entity_id,
        vol.Optional(CONF_PV_ENTITY): cv.entity_id,
        vol.Optional(CONF_CAPACITY_KWH): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=200)
        ),
        vol.Optional(CONF_POWER_KW): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=100)
        ),
        vol.Optional(CONF_TARGET_SOC): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_MIN_SOC): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_PV_FACTOR): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=1)
        ),
    }
)

SERVICE_SET_WINDOWS_SCHEMA = vol.Schema(
    {
        vol.Required("windows"): vol.All(
            cv.ensure_list,
            [
                vol.Schema(
                    {
                        vol.Required("start"): cv.time,
                        vol.Required("stop"): cv.time,
                        vol.Required("power"): vol.All(
                            vol.Coerce(int), vol.Range(min=-100, max=100)
                        ),
                        vol.Optional("days"): cv.string,
                    }
                )
            ],
        )
    }
)


def _coordinators(hass: HomeAssistant) -> list[GoodweTouCoordinator]:
    return [
        value
        for value in hass.data.get(DOMAIN, {}).values()
        if isinstance(value, GoodweTouCoordinator)
    ]


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve and auto-load the bundled Lovelace card (once)."""
    store = hass.data.setdefault(DOMAIN, {})
    if store.get(_FRONTEND_KEY):
        return
    store[_FRONTEND_KEY] = True

    http = getattr(hass, "http", None)
    if http is None:
        _LOGGER.debug("HTTP component not available, skipping card registration")
        return

    try:
        www_dir = Path(__file__).parent / "www"
        await http.async_register_static_paths(
            [StaticPathConfig(_URL_PATH, str(www_dir), cache_headers=False)]
        )
        add_extra_js_url(hass, f"{_URL_PATH}/{_CARD_FILE}")
        add_extra_js_url(hass, f"{_URL_PATH}/{_PLANNER_FILE}")
    except Exception:
        _LOGGER.warning(
            "Could not register the GoodWe TOU Lovelace card", exc_info=True
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up GoodWe Time-of-Use from a config entry."""
    client = GoodweUdpClient(
        host=entry.data[CONF_HOST],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        comm_addr=entry.data.get(CONF_COMM_ADDR, DEFAULT_COMM_ADDR),
        timeout=DEFAULT_TIMEOUT,
        retries=DEFAULT_RETRIES,
    )
    coordinator = GoodweTouCoordinator(hass, entry, client)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await _async_register_frontend(hass)
    _async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    if not hass.services.has_service(DOMAIN, SERVICE_SET_CHEAPEST_HOURS):

        async def _handle_set_cheapest_hours(call: ServiceCall) -> None:
            coordinators = _coordinators(hass)
            if not coordinators:
                _LOGGER.warning("No GoodWe Time-of-Use entry is loaded")
                return
            for coordinator in coordinators:
                await coordinator.async_set_cheapest_hours(call.data)

        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_CHEAPEST_HOURS,
            _handle_set_cheapest_hours,
            schema=SERVICE_SET_CHEAPEST_HOURS_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_CALCULATE_CHEAPEST_HOURS):

        async def _handle_calculate_cheapest_hours(
            call: ServiceCall,
        ) -> dict[str, list[dict]]:
            coordinators = _coordinators(hass)
            if not coordinators:
                raise HomeAssistantError("No GoodWe Time-of-Use entry is loaded")
            plan = await coordinators[0].async_calculate_cheapest_hours(call.data)
            return {"windows": [window_to_dict(window) for window in plan]}

        hass.services.async_register(
            DOMAIN,
            SERVICE_CALCULATE_CHEAPEST_HOURS,
            _handle_calculate_cheapest_hours,
            schema=SERVICE_SET_CHEAPEST_HOURS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SET_WINDOWS):

        async def _handle_set_windows(call: ServiceCall) -> None:
            coordinators = _coordinators(hass)
            if not coordinators:
                _LOGGER.warning("No GoodWe Time-of-Use entry is loaded")
                return
            for coordinator in coordinators:
                await coordinator.async_set_windows(call.data["windows"])

        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_WINDOWS,
            _handle_set_windows,
            schema=SERVICE_SET_WINDOWS_SCHEMA,
        )

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
from homeassistant.helpers.event import async_track_time_change

from .activity import MAX_ENTRIES
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
    CONF_PRICE_SOURCE,
    CONF_PV_ENTITY,
    CONF_PV_FACTOR,
    CONF_RECHECK_MINUTES,
    CONF_RTE,
    CONF_SOC_ENTITY,
    CONF_STANDBY_KW,
    CONF_TARGET_SOC,
    CONF_TIBBER_HOME,
    CONF_TIBBER_PRICES,
    CONF_WINDOW_POWER,
    DEFAULT_COMM_ADDR,
    DEFAULT_PORT,
    DEFAULT_PRICE_SOURCE,
    DEFAULT_RECHECK_MINUTES,
    DEFAULT_RETRIES,
    DEFAULT_TIBBER_HOME,
    DEFAULT_TIBBER_PRICES,
    DEFAULT_TIMEOUT,
    DOMAIN,
    PRICE_SOURCES,
    PRICE_SOURCE_TIBBER,
)
from .coordinator import GoodweTouCoordinator, TibberPriceCoordinator, window_to_dict
from .goodwe_udp import GoodweUdpClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.TIME,
    Platform.NUMBER,
    Platform.TEXT,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.SENSOR,
]

SERVICE_SET_CHEAPEST_HOURS = "set_cheapest_hours"
SERVICE_CALCULATE_CHEAPEST_HOURS = "calculate_cheapest_hours"
SERVICE_SET_WINDOWS = "set_windows"
SERVICE_FORECAST_SOC = "forecast_soc"
SERVICE_GET_ACTIVITY = "get_activity"

_URL_PATH = "/goodwe_tou"
_CARD_FILE = "goodwe-tou-card.js"
_PLANNER_FILE = "goodwe-tou-planner-card.js"
_LOG_FILE = "goodwe-tou-log-card.js"
_FRONTEND_KEY = "__frontend__"

SERVICE_SET_CHEAPEST_HOURS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PRICE_ENTITY): cv.entity_id,
        vol.Optional(CONF_PRICE_SOURCE): vol.In(PRICE_SOURCES),
        vol.Optional(CONF_SOC_ENTITY): cv.entity_id,
        vol.Optional(CONF_PV_ENTITY): cv.entity_id,
        vol.Optional(CONF_CAPACITY_KWH): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=200)
        ),
        vol.Optional(CONF_POWER_KW): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=100)
        ),
        vol.Optional(CONF_CHARGE_KW): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_DISCHARGE_KW): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_STANDBY_KW): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_EXPORT_HOURS): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=24)
        ),
        vol.Optional(CONF_EXPORT_LIMIT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=65535)
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
        vol.Optional(CONF_RTE): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=1)
        ),
        vol.Optional(CONF_CHARGE_WINDOWS): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=4)
        ),
        vol.Optional(CONF_DISCHARGE_WINDOWS): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=4)
        ),
        vol.Optional(CONF_WINDOW_POWER): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Optional(CONF_MIN_CELL_VOLTAGE): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=4000)
        ),
    }
)

SERVICE_FORECAST_SOC_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SOC_ENTITY): cv.entity_id,
        vol.Optional(CONF_CAPACITY_KWH): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=200)
        ),
        vol.Optional(CONF_POWER_KW): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=100)
        ),
        vol.Optional(CONF_CHARGE_KW): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_DISCHARGE_KW): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_STANDBY_KW): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_MIN_SOC): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_RTE): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=1)
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

SERVICE_GET_ACTIVITY_SCHEMA = vol.Schema(
    {
        vol.Optional("limit"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_ENTRIES)
        ),
        vol.Optional("window"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
        vol.Optional("result"): vol.In(["ok", "error"]),
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
        add_extra_js_url(hass, f"{_URL_PATH}/{_LOG_FILE}")
    except Exception:
        _LOGGER.warning(
            "Could not register the GoodWe TOU Lovelace card", exc_info=True
        )


def _recheck_minutes(entry: ConfigEntry) -> list[int]:
    """The minutes past the hour on which to re-check the plan.

    The interval is configurable but has to divide an hour so the schedule stays
    aligned with the quarter-hourly prices.
    """
    interval = int(entry.options.get(CONF_RECHECK_MINUTES, DEFAULT_RECHECK_MINUTES))
    if interval <= 0 or 60 % interval:
        interval = DEFAULT_RECHECK_MINUTES
    return list(range(0, 60, interval))


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

    await coordinator.async_load_activity()
    await coordinator.async_load_plan()
    await coordinator.async_config_entry_first_refresh()

    # The Tibber price sensor has its own 15-minute coordinator so the price card
    # keeps working when the inverter is unreachable, and so the cloud is not hit
    # on every poll. It is created when the user asked for the price sensor *or*
    # chose Tibber as the planner's price source; the first fetch never fails
    # setup (a missing Tibber just leaves it empty).
    price_source = entry.options.get(CONF_PRICE_SOURCE, DEFAULT_PRICE_SOURCE)
    if entry.options.get(CONF_TIBBER_PRICES, DEFAULT_TIBBER_PRICES) or (
        price_source == PRICE_SOURCE_TIBBER
    ):
        coordinator.price_coordinator = TibberPriceCoordinator(
            hass,
            home=str(entry.options.get(CONF_TIBBER_HOME, DEFAULT_TIBBER_HOME) or ""),
        )

    # The GoodWe blocks all export when its Feed Power limit is 0, which silently
    # stops the discharge windows. Push the configured limit on every start and
    # options change, but never let a failure keep the integration from loading.
    try:
        await coordinator.async_apply_export_limit()
    except Exception:
        _LOGGER.warning("Could not set the grid export limit", exc_info=True)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if coordinator.price_coordinator is not None:
        try:
            await coordinator.price_coordinator.async_refresh()
        except Exception:  # pragma: no cover - never let prices block setup
            _LOGGER.warning("Could not fetch the Tibber prices", exc_info=True)

    await _async_register_frontend(hass)
    _async_register_services(hass)

    async def _auto_replan(_now: object) -> None:
        """Quarter-hour re-check: keep the plan on the minimum-cost line."""
        try:
            await coordinator.async_auto_replan()
        except Exception:  # pragma: no cover - never let the scheduler die
            _LOGGER.warning("Automatic re-planning failed", exc_info=True)

    entry.async_on_unload(
        async_track_time_change(
            hass, _auto_replan, minute=_recheck_minutes(entry), second=0
        )
    )

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        prices = getattr(coordinator, "price_coordinator", None)
        if prices is not None:
            await prices.async_shutdown()
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
        ) -> dict[str, object]:
            coordinators = _coordinators(hass)
            if not coordinators:
                raise HomeAssistantError("No GoodWe Time-of-Use entry is loaded")
            plan = await coordinators[0].async_calculate_cheapest_hours(call.data)
            return {
                "windows": [window_to_dict(window) for window in plan.windows],
                "reasons": plan.reason_texts(),
                "summary": plan.summary,
                "notes": list(plan.notes),
            }

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

    if not hass.services.has_service(DOMAIN, SERVICE_FORECAST_SOC):

        async def _handle_forecast_soc(
            call: ServiceCall,
        ) -> dict[str, list[dict]]:
            coordinators = _coordinators(hass)
            if not coordinators:
                raise HomeAssistantError("No GoodWe Time-of-Use entry is loaded")
            points = await coordinators[0].async_forecast_soc(call.data)
            return {
                "points": [
                    {"time": moment.isoformat(), "soc": round(value, 1)}
                    for moment, value in points
                ]
            }

        hass.services.async_register(
            DOMAIN,
            SERVICE_FORECAST_SOC,
            _handle_forecast_soc,
            schema=SERVICE_FORECAST_SOC_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_GET_ACTIVITY):

        async def _handle_get_activity(
            call: ServiceCall,
        ) -> dict[str, list[dict]]:
            coordinators = _coordinators(hass)
            if not coordinators:
                raise HomeAssistantError("No GoodWe Time-of-Use entry is loaded")
            entries = coordinators[0].activity(
                window=call.data.get("window"),
                result=call.data.get("result"),
                limit=call.data.get("limit"),
            )
            return {"entries": entries, "count": len(entries)}

        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_ACTIVITY,
            _handle_get_activity,
            schema=SERVICE_GET_ACTIVITY_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

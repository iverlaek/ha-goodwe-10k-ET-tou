"""The GoodWe Time-of-Use (Eco Mode) integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_COMM_ADDR,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_COMM_ADDR,
    DEFAULT_PORT,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .coordinator import GoodweTouCoordinator
from .goodwe_udp import GoodweUdpClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.TIME,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.TEXT,
    Platform.BUTTON,
    Platform.SWITCH,
]


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

"""Select entities: window mode (off / charge / discharge)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODES, WINDOWS
from .coordinator import GoodweTouCoordinator
from .entity import GoodweTouEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoodweTouCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(TouModeEntity(coordinator, number) for number in WINDOWS)


class TouModeEntity(GoodweTouEntity, SelectEntity):
    """Charge/discharge mode of one Time-of-Use window."""

    _attr_options = MODES

    def __init__(self, coordinator: GoodweTouCoordinator, window: int) -> None:
        super().__init__(
            coordinator, f"w{window}_mode", f"Window {window} mode", "mdi:swap-vertical-bold"
        )
        self._window = window

    @property
    def current_option(self) -> str | None:
        window = self.coordinator.data["windows"].get(self._window)
        return window.mode if window else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_window_mode(self._window, option)

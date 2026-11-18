"""Text entities: window day selection."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, WINDOWS
from .coordinator import GoodweTouCoordinator
from .entity import GoodweTouEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoodweTouCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(TouDaysEntity(coordinator, number) for number in WINDOWS)


class TouDaysEntity(GoodweTouEntity, TextEntity):
    """Active days of one Time-of-Use window.

    Accepts ``all``, ``weekdays``, ``weekend``, ``none`` or a comma separated
    list of day names such as ``mon,tue,wed,thu,fri``.
    """

    _attr_native_min = 0
    _attr_native_max = 64
    _attr_mode = TextMode.TEXT
    _attr_pattern = r"^[A-Za-z0-9,\- ]*$"

    def __init__(self, coordinator: GoodweTouCoordinator, window: int) -> None:
        super().__init__(
            coordinator,
            f"w{window}_days",
            coordinator._t("entity.window_days", n=window),
            "mdi:calendar-week",
            domain="text",
            object_id=f"window_{window}_days",
        )
        self._window = window

    @property
    def native_value(self) -> str | None:
        window = self.coordinator.data["windows"].get(self._window)
        return window.days if window else None

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_set_window_days(self._window, value)

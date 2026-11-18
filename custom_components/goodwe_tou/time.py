"""Time entities: window start and stop times."""

from __future__ import annotations

import datetime as dt

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, WINDOWS
from .coordinator import GoodweTouCoordinator
from .entity import GoodweTouEntity

_KINDS = {"start": ("start", "mdi:clock-start"), "stop": ("stop", "mdi:clock-end")}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoodweTouCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TouTimeEntity(coordinator, number, kind)
        for number in WINDOWS
        for kind in _KINDS
    )


class TouTimeEntity(GoodweTouEntity, TimeEntity):
    """Start or stop time of one Time-of-Use window."""

    def __init__(
        self, coordinator: GoodweTouCoordinator, window: int, kind: str
    ) -> None:
        label, icon = _KINDS[kind]
        super().__init__(
            coordinator,
            f"w{window}_{label}",
            coordinator._t(f"entity.window_{label}", n=window),
            icon,
            domain="time",
            object_id=f"window_{window}_{label}",
        )
        self._window = window
        self._kind = kind

    @property
    def native_value(self) -> dt.time | None:
        window = self.coordinator.data["windows"].get(self._window)
        return getattr(window, self._kind) if window else None

    async def async_set_value(self, value: dt.time) -> None:
        await self.coordinator.async_set_window_time(self._window, self._kind, value)

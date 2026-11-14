"""Switch entity: force the inverter into ECO mode."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ECO_WORK_MODE
from .coordinator import GoodweTouCoordinator
from .entity import GoodweTouEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoodweTouCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TouEcoSwitch(coordinator)])


class TouEcoSwitch(GoodweTouEntity, SwitchEntity):
    """Turns ECO work mode (``work_mode = 3``) on or off.

    Time-of-Use only runs while the inverter is in ECO mode. Turning this switch
    off sets the inverter to general mode (``work_mode = 0``).
    """

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(coordinator, "eco_mode", "ECO mode", "mdi:leaf")

    @property
    def is_on(self) -> bool | None:
        work_mode = self.coordinator.data.get("work_mode")
        if work_mode is None:
            return None
        return work_mode == ECO_WORK_MODE

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_eco(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_eco(False)

"""Button entities: read from and apply to the inverter."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GoodweTouCoordinator
from .entity import GoodweTouEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoodweTouCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            TouReadButton(coordinator),
            TouApplyButton(coordinator),
        ]
    )


class TouReadButton(GoodweTouEntity, ButtonEntity):
    """Read the current schedule from the inverter."""

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator, "read", "Read from inverter", "mdi:download"
        )

    async def async_press(self) -> None:
        await self.coordinator.async_read()


class TouApplyButton(GoodweTouEntity, ButtonEntity):
    """Write every window to the inverter."""

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator, "apply", "Apply to inverter", "mdi:upload"
        )

    async def async_press(self) -> None:
        await self.coordinator.async_apply()

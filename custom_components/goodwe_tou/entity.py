"""Shared entity base class for the GoodWe Time-of-Use integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import GoodweTouCoordinator


class GoodweTouEntity(CoordinatorEntity[GoodweTouCoordinator]):
    """Base class for all GoodWe Time-of-Use entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoodweTouCoordinator,
        key: str,
        name: str,
        icon: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"
        self._attr_name = name
        if icon is not None:
            self._attr_icon = icon

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.config_entry.entry_id)},
            name="GoodWe Time-of-Use",
            manufacturer=MANUFACTURER,
            model=MODEL,
            configuration_url=f"http://{self.coordinator.client.host}",
        )

"""Shared entity base class for the GoodWe Time-of-Use integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_NAME, DEVICE_SLUG, DOMAIN, MANUFACTURER, MODEL
from .coordinator import GoodweTouCoordinator


class GoodweTouEntity(CoordinatorEntity[GoodweTouCoordinator]):
    """Base class for all GoodWe Time-of-Use entities.

    The entity's *name* is translated into the language the integration speaks,
    but its *entity id* is pinned so that changing the language (or translating
    the name) never moves an entity the dashboards look up. Every id is built as
    ``<domain>.<device slug>_<object_id>``, which is exactly what Home Assistant
    would have derived from the original, untranslated name.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoodweTouCoordinator,
        key: str,
        name: str,
        icon: str | None = None,
        *,
        domain: str,
        object_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"
        self._attr_name = name
        if icon is not None:
            self._attr_icon = icon
        # Pin the entity id: the translated name would otherwise change it.
        self.entity_id = f"{domain}.{DEVICE_SLUG}_{object_id}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.config_entry.entry_id)},
            name=DEVICE_NAME,
            manufacturer=MANUFACTURER,
            model=MODEL,
            configuration_url=f"http://{self.coordinator.client.host}",
        )

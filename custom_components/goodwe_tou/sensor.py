"""Sensor entity: the activity log of writes sent to the inverter."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GoodweTouCoordinator
from .entity import GoodweTouEntity

# Keep the state attributes comfortably under Home Assistant's 16 KB limit; the
# full log (up to 1000 entries) is available through ``goodwe_tou.get_activity``.
_ATTR_LIMIT = 50


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoodweTouCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [TouActivitySensor(coordinator), TouCellVoltageSensor(coordinator)]
    )


class TouActivitySensor(GoodweTouEntity, SensorEntity):
    """Number of writes to the inverter, with the latest entries as attributes.

    The state is how many entries the log currently holds; ``entries`` holds the
    newest ones (the full log is available through the ``goodwe_tou.get_activity``
    service). ``last_time`` changes on every write, which the log card uses to
    refresh.
    """

    _unrecorded_attributes = frozenset({"entries"})

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(coordinator, "activity_log", "Activity log", "mdi:history")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.get("activity", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entries = self.coordinator.data.get("activity", [])
        latest = entries[0] if entries else None
        return {
            "entries": entries[:_ATTR_LIMIT],
            "total": len(entries),
            "last_time": latest.get("time") if latest else None,
            "last_action": latest.get("action") if latest else None,
            "last_result": latest.get("result") if latest else None,
        }


class TouCellVoltageSensor(GoodweTouEntity, SensorEntity):
    """The lowest battery cell voltage, with the guard threshold as an attribute.

    The state of charge can look healthy while one cell is nearly empty, so this
    is the value the automation watches. When it drops below ``threshold`` the
    re-check stops itself and the activity log points at the SEMS/SolarGo app.
    """

    _attr_native_unit_of_measurement = "mV"

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator, "min_cell_voltage", "Laagste celspanning", "mdi:battery-heart"
        )

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get("min_cell_voltage")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"threshold": self.coordinator.min_cell_voltage_threshold}

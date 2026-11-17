"""Sensor entities: the activity log, the lowest cell voltage and Tibber prices."""

from __future__ import annotations

import datetime as dt
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import GoodweTouCoordinator, TibberPriceCoordinator
from .entity import GoodweTouEntity
from .prices import current_price

# Keep the state attributes comfortably under Home Assistant's 16 KB limit; the
# full log (up to 1000 entries) is available through ``goodwe_tou.get_activity``.
_ATTR_LIMIT = 50


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoodweTouCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        TouActivitySensor(coordinator),
        TouCellVoltageSensor(coordinator),
    ]
    if coordinator.price_coordinator is not None:
        entities.append(TouPricesSensor(coordinator, coordinator.price_coordinator))
    async_add_entities(entities)


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


class TouPricesSensor(CoordinatorEntity[TibberPriceCoordinator], SensorEntity):
    """The Tibber day-ahead prices, with the full list as a ``prices`` attribute.

    The state is the price of the quarter hour that is running now; ``prices``
    holds every slot for today and tomorrow as ``{time, price}`` records, which is
    the same shape the ENTSO-e/Nordpool sensors use and which the price graph
    draws. The list is fetched through the ``tibber.get_prices`` service by the
    dedicated :class:`~.coordinator.TibberPriceCoordinator`.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:currency-eur"
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    # The full two-day list is large and only needed live by the price card, so it
    # is kept out of the recorder database.
    _unrecorded_attributes = frozenset({"prices"})

    def __init__(
        self,
        coordinator: GoodweTouCoordinator,
        prices: TibberPriceCoordinator,
    ) -> None:
        super().__init__(prices)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_tibber_prices"
        self._attr_name = "Stroomprijzen"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name="GoodWe Time-of-Use",
            manufacturer=MANUFACTURER,
            model=MODEL,
            configuration_url=f"http://{coordinator.client.host}",
        )

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        now = dt.datetime.now(dt.UTC).astimezone()
        return current_price(data.get("prices", []), now)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "prices": data.get("prices", []),
            "home": data.get("home"),
            "updated": data.get("updated"),
        }

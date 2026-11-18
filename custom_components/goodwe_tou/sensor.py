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

from .const import (
    CONF_TIBBER_PRICES,
    DEFAULT_TIBBER_PRICES,
    DEVICE_NAME,
    DEVICE_SLUG,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
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
        TouPlanSensor(coordinator),
        TouDeviationSensor(coordinator),
        TouRetrySensor(coordinator),
    ]
    if coordinator.price_coordinator is not None and coordinator.config_entry.options.get(
        CONF_TIBBER_PRICES, DEFAULT_TIBBER_PRICES
    ):
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
        super().__init__(
            coordinator,
            "activity_log",
            coordinator._t("entity.activity_log"),
            "mdi:history",
            domain="sensor",
            object_id="activity_log",
        )

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
            # How often a write had to be re-sent (the flaky dongle); the log
            # card shows this only while it is non-zero.
            "retries": self.coordinator.retry_summary(),
            # The cards read the language from here when they have no override.
            "language": self.coordinator.language,
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
            coordinator,
            "min_cell_voltage",
            coordinator._t("entity.min_cell_voltage"),
            "mdi:battery-heart",
            domain="sensor",
            object_id="laagste_celspanning",
        )

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get("min_cell_voltage")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"threshold": self.coordinator.min_cell_voltage_threshold}


class TouPlanSensor(GoodweTouEntity, SensorEntity):
    """The last computed plan, with the reason behind every window.

    The four windows on the inverter only say *what* runs when; the planner also
    works out *why*. This sensor carries that reasoning (``reasons`` keyed by the
    1-based window number) plus the plan's summary and notes, so the dashboard can
    show the reason next to each window without recomputing the plan. The state is
    how many windows the plan filled in.
    """

    _unrecorded_attributes = frozenset({"windows", "reasons", "notes"})

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator,
            "plan",
            coordinator._t("entity.plan"),
            "mdi:calendar-clock",
            domain="sensor",
            object_id="plan",
        )

    @property
    def native_value(self) -> int:
        plan = self.coordinator.data.get("plan") or {}
        return len(plan.get("windows", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self.coordinator.data.get("plan") or {}
        return {
            "windows": plan.get("windows", []),
            "reasons": plan.get("reasons", []),
            "summary": plan.get("summary"),
            "notes": plan.get("notes", []),
            "planned_at": plan.get("planned_at"),
        }


class TouDeviationSensor(GoodweTouEntity, SensorEntity):
    """The deviation log and the house-load model learned from it.

    The state is how many hourly samples the log holds; the attributes carry the
    learned model, the current weather-adjusted load, today's totals and the
    newest samples (the full log is available through the
    ``goodwe_tou.get_deviations`` service). Only the *derived* samples live here —
    the raw temperature, humidity and load history stays in Home Assistant's
    recorder database.
    """

    _unrecorded_attributes = frozenset({"entries", "today", "learned"})

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator,
            "deviation",
            coordinator._t("entity.deviation"),
            "mdi:chart-timeline-variant-shimmer",
            domain="sensor",
            object_id="afwijking",
        )

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.get("deviation", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entries = self.coordinator.data.get("deviation", [])
        return {
            "entries": entries[:_ATTR_LIMIT],
            "total": len(entries),
            "learned": self.coordinator.data.get("learned"),
            "effective_kw": round(self.coordinator.effective_standby_kw(), 2),
            "today": self.coordinator.deviation_summary(),
            # The cards read the language from here when they have no override.
            "language": self.coordinator.language,
        }


class TouRetrySensor(GoodweTouEntity, SensorEntity):
    """How often a write had to be re-sent because the dongle dropped it.

    The older GoodWe Wi-Fi dongle (not the LAN/Wi-Fi v2.0 module) is easily
    overloaded and sometimes does not confirm a write, so the integration sends
    it again. The state is how many re-sends were needed today; ``total`` is the
    running tally since installation and ``last_attempts`` / ``last_time``
    describe the most recent retried write. A healthy dongle leaves this at 0,
    which is what the dashboard's conditional card keys on.
    """

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator,
            "retries",
            coordinator._t("entity.retries"),
            "mdi:backup-restore",
            domain="sensor",
            object_id="herpogingen",
        )

    @property
    def native_value(self) -> int:
        return int(self.coordinator.retry_summary().get("today") or 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        summary = self.coordinator.retry_summary()
        return {
            "total": summary.get("total", 0),
            "last_attempts": summary.get("last_attempts"),
            "last_time": summary.get("last_time"),
            # The cards read the language from here when they have no override.
            "language": self.coordinator.language,
        }


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
        self._attr_name = coordinator._t("entity.prices")
        # Pin the entity id so translating the name never moves it.
        self.entity_id = f"sensor.{DEVICE_SLUG}_stroomprijzen"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name=DEVICE_NAME,
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

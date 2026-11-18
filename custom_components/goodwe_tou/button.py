"""Button entities: read from, apply to, and optimise the inverter."""

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
            TouCheapestHoursButton(coordinator),
            TouAutoEcoButton(coordinator),
            TouManualOverrideButton(coordinator),
            TouManualChargeButton(coordinator),
            TouManualDischargeButton(coordinator),
        ]
    )


class TouReadButton(GoodweTouEntity, ButtonEntity):
    """Read the current schedule from the inverter."""

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator,
            "read",
            coordinator._t("entity.read"),
            "mdi:download",
            domain="button",
            object_id="read_from_inverter",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_read()


class TouApplyButton(GoodweTouEntity, ButtonEntity):
    """Write every window to the inverter."""

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator,
            "apply",
            coordinator._t("entity.apply"),
            "mdi:upload",
            domain="button",
            object_id="apply_to_inverter",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_apply()


class TouCheapestHoursButton(GoodweTouEntity, ButtonEntity):
    """Plan and apply the cheapest charging / most expensive discharging hours."""

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator,
            "cheapest_hours",
            coordinator._t("entity.cheapest_hours"),
            "mdi:cash-clock",
            domain="button",
            object_id="set_cheapest_hours",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_set_cheapest_hours()


class TouAutoEcoButton(GoodweTouEntity, ButtonEntity):
    """Run the inverter fully automatically: ECO mode plus the re-check."""

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator,
            "auto_eco",
            coordinator._t("entity.auto_eco"),
            "mdi:leaf-circle",
            domain="button",
            object_id="auto_eco_mode",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_set_auto_eco()


class TouManualOverrideButton(GoodweTouEntity, ButtonEntity):
    """Pause the automation and keep the windows that are on the inverter."""

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator,
            "manual_override",
            coordinator._t("entity.manual_override"),
            "mdi:hand-back-right",
            domain="button",
            object_id="manual_override",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_set_manual_override()


class TouManualChargeButton(GoodweTouEntity, ButtonEntity):
    """Force the manual window to charge (press again to switch it off)."""

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator,
            "manual_charge",
            coordinator._t("entity.manual_charge"),
            "mdi:battery-arrow-up",
            domain="button",
            object_id="manual_charge",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_set_manual_mode("charge")


class TouManualDischargeButton(GoodweTouEntity, ButtonEntity):
    """Force the manual window to discharge (press again to switch it off)."""

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator,
            "manual_discharge",
            coordinator._t("entity.manual_discharge"),
            "mdi:battery-arrow-down",
            domain="button",
            object_id="manual_discharge",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_set_manual_mode("discharge")

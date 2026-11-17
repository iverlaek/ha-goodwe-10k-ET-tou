"""Switch entities: ECO mode and the automatic quarter-hour re-check."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, ECO_WORK_MODE
from .coordinator import GoodweTouCoordinator
from .entity import GoodweTouEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoodweTouCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TouEcoSwitch(coordinator), TouAutoReplanSwitch(coordinator)])


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


class TouAutoReplanSwitch(GoodweTouEntity, RestoreEntity, SwitchEntity):
    """Pauses or enables the automatic quarter-hour re-check.

    When it is on, the integration re-checks the plan every quarter hour and
    writes a new plan (with the reason in the activity log) whenever the
    scheduled windows are no longer the cheapest. Turn it off to keep the plan
    that is on the inverter now.
    """

    def __init__(self, coordinator: GoodweTouCoordinator) -> None:
        super().__init__(
            coordinator, "auto_replan", "Auto-herberekening", "mdi:autorenew"
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.auto_replan_enabled

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self.coordinator.auto_replan_enabled = last.state == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.auto_replan_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.auto_replan_enabled = False
        self.async_write_ha_state()

"""Number entities: signed window power percentage."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, WINDOWS
from .coordinator import GoodweTouCoordinator
from .entity import GoodweTouEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoodweTouCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(TouPowerEntity(coordinator, number) for number in WINDOWS)


class TouPowerEntity(GoodweTouEntity, NumberEntity):
    """Signed charge/discharge power of one Time-of-Use window.

    The value is a signed percentage: ``-100`` charges at full power, ``0``
    disables the window and ``+100`` discharges at full power.
    """

    _attr_native_min_value = -100
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: GoodweTouCoordinator, window: int) -> None:
        super().__init__(
            coordinator,
            f"w{window}_power",
            f"Window {window} power",
            "mdi:battery-charging",
        )
        self._window = window

    @property
    def native_value(self) -> float | None:
        window = self.coordinator.data["windows"].get(self._window)
        return float(window.signed_power) if window else None

    @property
    def icon(self) -> str:
        value = self.native_value or 0
        if value < 0:
            return "mdi:battery-arrow-down"
        if value > 0:
            return "mdi:battery-arrow-up"
        return "mdi:battery-off-outline"

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_window_power(self._window, int(value))

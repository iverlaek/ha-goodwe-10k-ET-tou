"""Data update coordinator for the GoodWe Time-of-Use integration."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CAPACITY_KWH,
    CONF_MIN_SOC,
    CONF_POWER_KW,
    CONF_PRICE_ENTITY,
    CONF_PV_ENTITY,
    CONF_PV_FACTOR,
    CONF_SCAN_INTERVAL,
    CONF_SOC_ENTITY,
    CONF_TARGET_SOC,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_MIN_SOC,
    DEFAULT_POWER_KW,
    DEFAULT_PRICE_ENTITY,
    DEFAULT_PV_ENTITY,
    DEFAULT_PV_FACTOR,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOC_ENTITY,
    DEFAULT_TARGET_SOC,
    DOMAIN,
    ECO_WORK_MODE,
    GENERAL_WORK_MODE,
    WINDOWS,
    WORK_MODE_REGISTER,
)
from .goodwe_udp import GoodweError, GoodweUdpClient
from .optimizer import plan_registers, plan_windows, slots_from_prices
from .registers import TouWindow, base_for, decode_window, window_registers

_LOGGER = logging.getLogger(__name__)

_FIRST_BASE = base_for(1)
_REGISTER_COUNT = 4 * len(WINDOWS)
_APPLY_ATTEMPTS = 3


class GoodweTouCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Reads and writes the four Time-of-Use windows of the inverter."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: GoodweUdpClient
    ) -> None:
        interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=dt.timedelta(seconds=interval) if interval else None,
        )
        self.client = client
        self._io_lock = asyncio.Lock()
        self.data = {"windows": {}, "work_mode": None}

    # -- device access -----------------------------------------------------

    async def _call(self, func: Callable[..., Any], *args: Any) -> Any:
        """Run a blocking client call in the executor, one at a time."""
        async with self._io_lock:
            return await self.hass.async_add_executor_job(func, *args)

    def _read_all(self) -> dict[str, Any]:
        registers = self.client.read_registers(_FIRST_BASE, _REGISTER_COUNT)
        windows: dict[int, TouWindow] = {}
        for number in WINDOWS:
            offset = base_for(number) - _FIRST_BASE
            windows[number] = decode_window(
                base_for(number), registers[offset : offset + 4]
            )
        work_mode = self.client.read_registers(WORK_MODE_REGISTER, 1)[0]
        return {"windows": windows, "work_mode": work_mode}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._call(self._read_all)
        except GoodweError as err:
            raise UpdateFailed(str(err)) from err

    async def _write(self, mapping: Mapping[int, int]) -> None:
        try:
            for register, value in mapping.items():
                await self._call(self.client.write_register, register, value)
        except GoodweError as err:
            raise HomeAssistantError(f"Could not write to the inverter: {err}") from err

    def _window(self, number: int) -> TouWindow:
        window = self.data["windows"].get(number)
        if window is None:
            raise HomeAssistantError(f"Window {number} is not available yet")
        return window

    def _registers_for(self, windows: Mapping[int, TouWindow]) -> dict[int, int]:
        mapping: dict[int, int] = {}
        for number, window in windows.items():
            for relative, value in window_registers(window).items():
                mapping[base_for(number) + relative] = value
        return mapping

    # -- public operations -------------------------------------------------

    async def async_read(self) -> None:
        """Read the current schedule from the inverter."""
        try:
            data = await self._call(self._read_all)
        except GoodweError as err:
            raise HomeAssistantError(
                f"Could not read from the inverter: {err}"
            ) from err
        self.async_set_updated_data(data)

    async def async_apply(self) -> None:
        """Write every window to the inverter and verify the result.

        The dongle is flaky and another integration may be polling it at the
        same time, so the write is retried until a read-back matches what we
        intended to write.
        """
        windows = {number: self._window(number) for number in WINDOWS}
        mapping = self._registers_for(windows)

        last: dict[str, Any] | None = None
        for attempt in range(1, _APPLY_ATTEMPTS + 1):
            await self._write(mapping)
            try:
                last = await self._call(self._read_all)
            except GoodweError as err:
                raise HomeAssistantError(f"Could not verify the write: {err}") from err
            if self._registers_for(last["windows"]) == mapping:
                self.async_set_updated_data(last)
                return
            _LOGGER.warning(
                "Apply verification attempt %s/%s did not match, retrying",
                attempt,
                _APPLY_ATTEMPTS,
            )

        if last is not None:
            self.async_set_updated_data(last)
        raise HomeAssistantError(
            "The inverter did not accept the schedule (read-back mismatch after "
            f"{_APPLY_ATTEMPTS} attempts). Another integration may be polling the "
            "dongle at the same time."
        )

    async def async_set_window_time(
        self, number: int, kind: str, value: dt.time
    ) -> None:
        window = self._window(number)
        updated = replace(window, **{kind: value})
        relative = 0 if kind == "start" else 1
        await self._write(
            {base_for(number) + relative: window_registers(updated)[relative]}
        )
        self.data["windows"][number] = updated
        self.async_set_updated_data(self.data)

    async def async_set_window_power(self, number: int, signed_power: int) -> None:
        """Set the signed power (negative charges, positive discharges)."""
        window = self._window(number)
        updated = TouWindow.from_signed_power(
            start=window.start,
            stop=window.stop,
            signed_power=signed_power,
            days=window.days,
        )
        registers = window_registers(updated)
        await self._write(
            {base_for(number) + 2: registers[2], base_for(number) + 3: registers[3]}
        )
        self.data["windows"][number] = updated
        self.async_set_updated_data(self.data)

    async def async_set_window_days(self, number: int, days: str) -> None:
        window = self._window(number)
        updated = replace(window, days=days)
        registers = window_registers(updated)
        await self._write({base_for(number) + 3: registers[3]})
        self.data["windows"][number] = updated
        self.async_set_updated_data(self.data)

    async def async_set_eco(self, enabled: bool) -> None:
        value = ECO_WORK_MODE if enabled else GENERAL_WORK_MODE
        await self._write({WORK_MODE_REGISTER: value})
        self.data["work_mode"] = value
        self.async_set_updated_data(self.data)

    # -- cheapest hours ----------------------------------------------------

    def _option(self, key: str, default: Any) -> Any:
        return self.config_entry.options.get(key, default)

    def _state(self, entity_id: str) -> State | None:
        return self.hass.states.get(entity_id) if entity_id else None

    def _price_slots(self, entity_id: str) -> list:
        state = self._state(entity_id)
        if state is None:
            raise HomeAssistantError(f"Price entity {entity_id} was not found")
        attributes = state.attributes
        raw = attributes.get("prices")
        if not raw:
            raw = list(attributes.get("prices_today") or []) + list(
                attributes.get("prices_tomorrow") or []
            )
        slots = slots_from_prices(raw)
        if not slots:
            raise HomeAssistantError(
                f"Price entity {entity_id} does not expose a 'prices' attribute"
            )
        return slots

    def _float_state(self, entity_id: str, default: float) -> float:
        state = self._state(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return default

    async def async_set_cheapest_hours(
        self, overrides: Mapping[str, Any] | None = None
    ) -> list[TouWindow]:
        """Plan and apply the cheapest charging / most expensive discharging hours."""
        overrides = overrides or {}

        def setting(key: str, default: Any) -> Any:
            if key in overrides and overrides[key] is not None:
                return overrides[key]
            return self._option(key, default)

        price_entity = setting(CONF_PRICE_ENTITY, DEFAULT_PRICE_ENTITY)
        soc_entity = setting(CONF_SOC_ENTITY, DEFAULT_SOC_ENTITY)
        pv_entity = setting(CONF_PV_ENTITY, DEFAULT_PV_ENTITY)
        capacity_kwh = float(setting(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        power_kw = float(setting(CONF_POWER_KW, DEFAULT_POWER_KW))
        target_soc = float(setting(CONF_TARGET_SOC, DEFAULT_TARGET_SOC))
        min_soc = float(setting(CONF_MIN_SOC, DEFAULT_MIN_SOC))
        pv_factor = float(setting(CONF_PV_FACTOR, DEFAULT_PV_FACTOR))

        slots = self._price_slots(price_entity)
        soc = self._float_state(soc_entity, target_soc)
        pv_remaining = self._float_state(pv_entity, 0.0)

        now = dt.datetime.now(dt.UTC).astimezone()
        planned = plan_windows(
            slots,
            now,
            soc=soc,
            capacity_kwh=capacity_kwh,
            power_kw=power_kw,
            target_soc=target_soc,
            min_soc=min_soc,
            pv_credit_kwh=pv_remaining * pv_factor,
        )
        plan = plan_registers(planned)

        for number, window in zip(WINDOWS, plan, strict=True):
            self.data["windows"][number] = window
        self.async_set_updated_data(self.data)

        await self.async_apply()
        return plan

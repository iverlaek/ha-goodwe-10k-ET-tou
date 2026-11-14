"""Data update coordinator for the GoodWe Time-of-Use integration."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ECO_WORK_MODE,
    GENERAL_WORK_MODE,
    WINDOWS,
    WORK_MODE_REGISTER,
)
from .goodwe_udp import GoodweError, GoodweUdpClient
from .registers import TouWindow, base_for, decode_window, window_registers

_LOGGER = logging.getLogger(__name__)

_FIRST_BASE = base_for(1)
_REGISTER_COUNT = 4 * len(WINDOWS)


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
            offset = (base_for(number) - _FIRST_BASE)
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

    async def _write(self, mapping: dict[int, int]) -> None:
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

    # -- public operations -------------------------------------------------

    async def async_read(self) -> None:
        """Read the current schedule from the inverter."""
        try:
            data = await self._call(self._read_all)
        except GoodweError as err:
            raise HomeAssistantError(f"Could not read from the inverter: {err}") from err
        self.async_set_updated_data(data)

    async def async_apply(self) -> None:
        """Write every window to the inverter."""
        mapping: dict[int, int] = {}
        for number in WINDOWS:
            registers = window_registers(self._window(number))
            for relative, value in registers.items():
                mapping[base_for(number) + relative] = value
        await self._write(mapping)
        await self.async_read()

    async def async_set_window_time(
        self, number: int, kind: str, value: dt.time
    ) -> None:
        window = self._window(number)
        updated = replace(window, **{kind: value})
        relative = 0 if kind == "start" else 1
        await self._write({base_for(number) + relative: window_registers(updated)[relative]})
        self.data["windows"][number] = updated
        self.async_set_updated_data(self.data)

    async def async_set_window_mode(self, number: int, mode: str) -> None:
        window = self._window(number)
        updated = replace(window, mode=mode)
        registers = window_registers(updated)
        await self._write(
            {base_for(number) + 2: registers[2], base_for(number) + 3: registers[3]}
        )
        self.data["windows"][number] = updated
        self.async_set_updated_data(self.data)

    async def async_set_window_power(self, number: int, power: int) -> None:
        window = self._window(number)
        updated = replace(window, power=max(0, min(100, int(power))))
        registers = window_registers(updated)
        await self._write({base_for(number) + 2: registers[2]})
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

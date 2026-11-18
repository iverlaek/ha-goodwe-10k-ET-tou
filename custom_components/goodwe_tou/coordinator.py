"""Data update coordinator for the GoodWe Time-of-Use integration."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .activity import (
    filter_entries,
    make_entry,
    trim,
    window_changes,
    window_summary,
)
from .const import (
    CELL_VOLTAGE_REGISTERS,
    CONF_AUTO_REPLAN,
    CONF_CAPACITY_KWH,
    CONF_CHARGE_KW,
    CONF_CHARGE_WINDOWS,
    CONF_DISCHARGE_KW,
    CONF_DISCHARGE_WINDOWS,
    CONF_EXPORT_HOURS,
    CONF_EXPORT_LIMIT,
    CONF_LANGUAGE,
    CONF_LOG_SIZE,
    CONF_MANUAL_WINDOW,
    CONF_MIN_CELL_VOLTAGE,
    CONF_MIN_SOC,
    CONF_POWER_KW,
    CONF_PRICE_ENTITY,
    CONF_PRICE_SOURCE,
    CONF_PV_ENTITY,
    CONF_PV_FACTOR,
    CONF_RTE,
    CONF_SCAN_INTERVAL,
    CONF_SOC_ENTITY,
    CONF_STANDBY_KW,
    CONF_TARGET_SOC,
    CONF_WINDOW_POWER,
    DEFAULT_AUTO_REPLAN,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_CHARGE_KW,
    DEFAULT_CHARGE_WINDOWS,
    DEFAULT_DISCHARGE_KW,
    DEFAULT_DISCHARGE_WINDOWS,
    DEFAULT_EXPORT_HOURS,
    DEFAULT_EXPORT_LIMIT,
    DEFAULT_LANGUAGE,
    DEFAULT_LOG_SIZE,
    DEFAULT_MANUAL_WINDOW,
    DEFAULT_MIN_CELL_VOLTAGE,
    DEFAULT_MIN_SOC,
    DEFAULT_PRICE_ENTITY,
    DEFAULT_PRICE_SOURCE,
    DEFAULT_PV_ENTITY,
    DEFAULT_PV_FACTOR,
    DEFAULT_RTE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOC_ENTITY,
    DEFAULT_STANDBY_KW,
    DEFAULT_TARGET_SOC,
    DEFAULT_WINDOW_POWER,
    DOMAIN,
    ECO_WORK_MODE,
    EXPORT_ENABLE_REGISTER,
    EXPORT_ENABLED,
    EXPORT_LIMIT_REGISTER,
    GENERAL_WORK_MODE,
    MAX_LOG_SIZE,
    PRICE_SOURCE_TIBBER,
    WINDOWS,
    WORK_MODE_REGISTER,
)
from .goodwe_udp import (
    GoodweError,
    GoodweProtocolError,
    GoodweRejectedError,
    GoodweUdpClient,
)
from .i18n import resolve_language, translate
from .optimizer import (
    Plan,
    build_plan,
    forecast_soc,
    plan_registers,
    slots_from_prices,
    windows_cost,
)
from .prices import TIBBER_DOMAIN, TIBBER_SERVICE, tibber_prices
from .registers import (
    TouWindow,
    base_for,
    day_bit,
    decode_window,
    valid_block,
    window_registers,
)

_LOGGER = logging.getLogger(__name__)

_FIRST_BASE = base_for(1)
_REGISTER_COUNT = 4 * len(WINDOWS)
_APPLY_ATTEMPTS = 3
_READ_ATTEMPTS = 3


def window_to_dict(window: TouWindow) -> dict[str, Any]:
    """Serialise a window for a service response (see ``calculate_cheapest_hours``)."""
    return {
        "start": window.start.strftime("%H:%M"),
        "stop": window.stop.strftime("%H:%M"),
        "power": window.signed_power,
        "days": window.days,
        "mode": window.mode,
        "enabled": window.enabled,
    }


class TibberPriceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch the Tibber day-ahead prices for the price card.

    Tibber has no sensor that exposes the full price list, only the current price
    and a few statistics. The ``tibber.get_prices`` service does return every
    quarter-hour price for today and tomorrow, so this small coordinator calls it
    on its own 15-minute schedule. It is deliberately separate from the inverter
    coordinator: the price card must keep working even when the flaky dongle is
    unreachable, and there is no reason to hit the Tibber cloud on every poll.
    """

    def __init__(self, hass: HomeAssistant, home: str = "") -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_prices",
            update_interval=dt.timedelta(minutes=15),
        )
        self.home = home
        self.data = {"prices": [], "home": None, "updated": None}

    @property
    def service_available(self) -> bool:
        """Whether the Tibber integration is loaded and offers its price service."""
        return self.hass.services.has_service(TIBBER_DOMAIN, TIBBER_SERVICE)

    async def _async_update_data(self) -> dict[str, Any]:
        if not self.service_available:
            # No Tibber: keep whatever we have (usually nothing) without errors.
            return self.data
        now = dt.datetime.now(dt.UTC).astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=2)
        try:
            response = await self.hass.services.async_call(
                TIBBER_DOMAIN,
                TIBBER_SERVICE,
                {"start": start.isoformat(), "end": end.isoformat()},
                blocking=True,
                return_response=True,
            )
        except Exception:  # pragma: no cover - cloud hiccups must not break setup
            _LOGGER.warning("Could not fetch the Tibber prices", exc_info=True)
            return self.data
        records, home = tibber_prices(response, self.home)
        if not records:
            _LOGGER.debug("Tibber returned no usable prices")
            return self.data
        return {"prices": records, "home": home, "updated": now.isoformat()}


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
        self.data = {
            "windows": {},
            "work_mode": None,
            "export_enabled": None,
            "export_limit": None,
            "min_cell_voltage": None,
            "activity": [],
            "plan": None,
        }
        self._activity: list[dict[str, Any]] = []
        self._activity_store: Store = Store(
            hass, 1, f"{DOMAIN}_activity_{entry.entry_id}"
        )
        #: The reasoning behind the last computed plan (for the status card).
        self._plan: dict[str, Any] | None = None
        self._plan_store: Store = Store(hass, 1, f"{DOMAIN}_plan_{entry.entry_id}")
        #: Whether the quarter-hour re-check may write a new plan by itself.
        self.auto_replan_enabled: bool = bool(
            entry.options.get(CONF_AUTO_REPLAN, DEFAULT_AUTO_REPLAN)
        )
        #: Optional Tibber price coordinator backing the price sensor.
        self.price_coordinator: TibberPriceCoordinator | None = None

    # -- language ----------------------------------------------------------

    @property
    def language(self) -> str:
        """The language the integration speaks (``nl`` or ``en``).

        Chosen at installation (stored in the entry's data) and changeable in the
        options; the options win when set.
        """
        value = self.config_entry.options.get(
            CONF_LANGUAGE
        ) or self.config_entry.data.get(CONF_LANGUAGE)
        return resolve_language(value or DEFAULT_LANGUAGE)

    def _t(self, key: str, **params: Any) -> str:
        """Translate ``key`` into the configured language."""
        return translate(self.language, key, **params)

    # -- activity log ------------------------------------------------------

    async def async_load_activity(self) -> None:
        """Load the persisted activity log (if any) into memory."""
        try:
            stored = await self._activity_store.async_load()
        except Exception:  # pragma: no cover - corrupt/unreadable store
            _LOGGER.warning("Could not load the activity log", exc_info=True)
            return
        if isinstance(stored, list):
            self._activity = [entry for entry in stored if isinstance(entry, dict)]
            trim(self._activity, self._log_size())
        self._publish(self.data)

    async def async_load_plan(self) -> None:
        """Load the persisted plan reasoning (if any) into memory."""
        try:
            stored = await self._plan_store.async_load()
        except Exception:  # pragma: no cover - corrupt/unreadable store
            _LOGGER.warning("Could not load the plan reasoning", exc_info=True)
            return
        if isinstance(stored, dict):
            self._plan = stored
        self._publish(self.data)

    def _schedule_save(self) -> None:
        self._activity_store.async_delay_save(lambda: list(self._activity), 2.0)

    def _record(
        self,
        action: str,
        *,
        changes: Sequence[Mapping[str, Any]] | None = None,
        windows: Sequence[Mapping[str, Any]] | None = None,
        result: str = "ok",
        error: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Append one entry to the log and persist it."""
        self._activity.insert(
            0,
            make_entry(
                action,
                changes=changes,
                windows=windows,
                result=result,
                error=error,
                reason=reason,
            ),
        )
        trim(self._activity, self._log_size())
        self._schedule_save()
        self._publish(self.data)

    def activity(
        self,
        *,
        window: int | None = None,
        result: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return the activity log, newest first, optionally filtered."""
        return filter_entries(self._activity, window=window, result=result, limit=limit)

    def _publish(self, data: dict[str, Any]) -> None:
        """Push fresh data to the entities, including the current activity log."""
        data["activity"] = list(self._activity)
        data["plan"] = self._plan
        self.async_set_updated_data(data)

    # -- device access -----------------------------------------------------

    async def _call(self, func: Callable[..., Any], *args: Any) -> Any:
        """Run a blocking client call in the executor, one at a time."""
        async with self._io_lock:
            return await self.hass.async_add_executor_job(func, *args)

    def _read_block(
        self,
        offset: int,
        count: int,
        validate: Callable[[list[int]], bool] | None = None,
    ) -> list[int]:
        """Read a block from the shared dongle, ignoring foreign frames.

        The dongle answers several clients on one bus and sometimes delivers a
        frame that answers somebody else's request. A read response does not
        echo the register it answers, so a block is only trusted once two
        consecutive reads agree (and, when given, pass ``validate``). Modbus
        exceptions that another client triggered are retried instead of being
        blamed on us.
        """
        previous: list[int] | None = None
        last_error: GoodweError | None = None
        for _ in range(_READ_ATTEMPTS):
            try:
                registers = self.client.read_registers(offset, count)
            except GoodweRejectedError as err:
                # Usually another client probing an unsupported register.
                last_error = err
                previous = None
                continue
            if registers == previous and (validate is None or validate(registers)):
                return registers
            previous = registers
        if previous is not None and (validate is None or validate(previous)):
            _LOGGER.warning(
                "No two matching reads at %s, using the last valid one", offset
            )
            return previous
        if last_error is not None:
            raise last_error
        raise GoodweProtocolError(
            f"could not read a consistent {count}-register block at {offset}"
        )

    def _read_all(self) -> dict[str, Any]:
        registers = self._read_block(_FIRST_BASE, _REGISTER_COUNT, valid_block)
        windows: dict[int, TouWindow] = {}
        for number in WINDOWS:
            offset = base_for(number) - _FIRST_BASE
            windows[number] = decode_window(
                base_for(number), registers[offset : offset + 4]
            )
        work_mode = self._read_block(WORK_MODE_REGISTER, 1)[0]
        export = self._read_block(EXPORT_ENABLE_REGISTER, 2)
        # The low-cell guard is best effort: an inverter that does not answer the
        # battery block must not take the whole integration down.
        try:
            cells = self._read_block(CELL_VOLTAGE_REGISTERS[0], 2)
            plausible = [value for value in cells if value > 0]
            cell_min = min(plausible) if plausible else None
        except GoodweError:
            cell_min = None
        return {
            "windows": windows,
            "work_mode": work_mode,
            "export_enabled": export[0],
            "export_limit": export[1],
            "min_cell_voltage": cell_min,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self._call(self._read_all)
        except GoodweError as err:
            raise UpdateFailed(str(err)) from err
        data["activity"] = list(self._activity)
        data["plan"] = self._plan
        return data

    async def _write(self, mapping: Mapping[int, int]) -> None:
        try:
            for register, value in mapping.items():
                await self._call(self.client.write_register, register, value)
        except GoodweError as err:
            raise HomeAssistantError(
                self._t("error.write", error=err)
            ) from err

    def _window(self, number: int) -> TouWindow:
        window = self.data["windows"].get(number)
        if window is None:
            raise HomeAssistantError(self._t("error.window_missing", n=number))
        return window

    def _registers_for(self, windows: Mapping[int, TouWindow]) -> dict[int, int]:
        mapping: dict[int, int] = {}
        for number, window in windows.items():
            for relative, value in window_registers(window).items():
                mapping[base_for(number) + relative] = value
        return mapping

    def _export_limit(self, overrides: Mapping[str, Any] | None = None) -> int:
        """The configured grid export limit in watts."""
        overrides = overrides or {}
        value = overrides.get(CONF_EXPORT_LIMIT)
        if value is None:
            value = self._option(CONF_EXPORT_LIMIT, DEFAULT_EXPORT_LIMIT)
        return max(0, min(0xFFFF, int(value)))

    def _export_registers(
        self, overrides: Mapping[str, Any] | None = None
    ) -> dict[int, int]:
        """The Feed Power enable + limit, so the discharge windows can export."""
        return {
            EXPORT_ENABLE_REGISTER: EXPORT_ENABLED,
            EXPORT_LIMIT_REGISTER: self._export_limit(overrides),
        }

    async def _write_verified(self, mapping: Mapping[int, int]) -> None:
        """Write registers, then read the schedule back and confirm them.

        The dongle is flaky and shared, so a write is only believed once the
        inverter reads the intended value back. This is what makes a single
        entity change *stick* instead of silently reverting on the next poll.
        """
        last: dict[str, Any] | None = None
        for attempt in range(1, _APPLY_ATTEMPTS + 1):
            await self._write(mapping)
            try:
                last = await self._call(self._read_all)
            except GoodweError as err:
                raise HomeAssistantError(
                    self._t("error.verify", error=err)
                ) from err
            actual = self._registers_for(last["windows"])
            actual[WORK_MODE_REGISTER] = last["work_mode"]
            actual[EXPORT_ENABLE_REGISTER] = last["export_enabled"]
            actual[EXPORT_LIMIT_REGISTER] = last["export_limit"]
            if all(actual.get(register) == value for register, value in mapping.items()):
                self._publish(last)
                return
            _LOGGER.warning(
                "Write verification attempt %s/%s did not match, retrying",
                attempt,
                _APPLY_ATTEMPTS,
            )

        if last is not None:
            self._publish(last)
        raise HomeAssistantError(self._t("error.readback", attempts=_APPLY_ATTEMPTS))

    # -- public operations -------------------------------------------------

    async def async_read(self) -> None:
        """Read the current schedule from the inverter."""
        try:
            data = await self._call(self._read_all)
        except GoodweError as err:
            raise HomeAssistantError(
                self._t("error.read", error=err)
            ) from err
        self._publish(data)

    async def _logged(
        self,
        action: str,
        operation: Awaitable[None],
        *,
        changes: Sequence[Mapping[str, Any]] | None = None,
        windows: Sequence[Mapping[str, Any]] | None = None,
        reason: str | None = None,
    ) -> None:
        """Run a write operation and record it in the activity log."""
        try:
            await operation
        except HomeAssistantError as err:
            self._record(
                action,
                changes=changes,
                windows=windows,
                result="error",
                error=str(err),
                reason=reason,
            )
            raise
        self._record(action, changes=changes, windows=windows, reason=reason)

    async def async_apply(self) -> None:
        """Write every window to the inverter and verify the result."""
        current = {number: self._window(number) for number in WINDOWS}
        targets = [window_summary(number, current[number]) for number in WINDOWS]
        mapping = self._registers_for(current)
        mapping.update(self._export_registers())
        await self._logged(
            "apply",
            self._write_verified(mapping),
            windows=targets,
        )

    async def async_set_export_limit(self, limit: int) -> None:
        """Set the inverter's grid export limit and enable exporting.

        The GoodWe blocks all export when the limit is 0, which also stops the
        discharge windows: without it the battery only covers the house load.
        """
        mapping = {
            EXPORT_ENABLE_REGISTER: EXPORT_ENABLED,
            EXPORT_LIMIT_REGISTER: max(0, min(0xFFFF, int(limit))),
        }
        old = self.data.get("export_limit")
        await self._logged(
            "export_limit",
            self._write_verified(mapping),
            changes=[
                {
                    "field": "export_limit",
                    "old": old,
                    "new": mapping[EXPORT_LIMIT_REGISTER],
                }
            ],
        )

    async def async_apply_export_limit(self) -> None:
        """Make sure the inverter allows the configured grid export.

        The GoodWe blocks all export when the Feed Power limit is 0, which also
        stops the discharge windows. The limit is only written when the inverter
        differs from the configured value, so an ordinary restart leaves the
        dongle alone.
        """
        target = self._export_registers()
        current = {
            EXPORT_ENABLE_REGISTER: self.data.get("export_enabled"),
            EXPORT_LIMIT_REGISTER: self.data.get("export_limit"),
        }
        if all(current.get(register) == value for register, value in target.items()):
            return
        await self.async_set_export_limit(self._export_limit())

    async def async_set_windows(
        self, windows: Sequence[Mapping[str, Any]]
    ) -> None:
        """Write an explicit set of windows to the inverter.

        Each item is a mapping with ``start`` and ``stop`` (:class:`datetime.time`),
        a signed ``power`` percentage (negative charges) and an optional ``days``
        string. The list is padded to four windows and written with read-back
        verification, so a dashboard can apply a whole plan in one request
        instead of one write per entity.
        """
        plan: list[TouWindow] = []
        for item in windows:
            plan.append(
                TouWindow.from_signed_power(
                    start=item["start"],
                    stop=item["stop"],
                    signed_power=int(item["power"]),
                    days=str(item.get("days") or "all"),
                )
            )
        plan = plan_registers(plan)
        after = dict(zip(WINDOWS, plan, strict=True))
        changes = window_changes(dict(self.data["windows"]), after)
        targets = [window_summary(number, after[number]) for number in WINDOWS]
        await self._logged(
            "set_windows",
            self._write_verified(self._registers_for(after)),
            changes=changes,
            windows=targets,
        )

    async def async_set_window_time(
        self, number: int, kind: str, value: dt.time
    ) -> None:
        window = self._window(number)
        updated = replace(window, **{kind: value})
        relative = 0 if kind == "start" else 1
        await self._logged(
            "window_time",
            self._write_verified(
                {base_for(number) + relative: window_registers(updated)[relative]}
            ),
            changes=window_changes({number: window}, {number: updated}),
        )

    async def async_set_window_power(
        self,
        number: int,
        signed_power: int,
        *,
        action: str = "window_power",
        reason: str | None = None,
    ) -> None:
        """Set the signed power (negative charges, positive discharges)."""
        window = self._window(number)
        updated = TouWindow.from_signed_power(
            start=window.start,
            stop=window.stop,
            signed_power=signed_power,
            days=window.days,
        )
        registers = window_registers(updated)
        await self._logged(
            action,
            self._write_verified(
                {base_for(number) + 2: registers[2], base_for(number) + 3: registers[3]}
            ),
            changes=window_changes({number: window}, {number: updated}),
            reason=reason,
        )

    async def async_set_window_days(self, number: int, days: str) -> None:
        window = self._window(number)
        updated = replace(window, days=days)
        registers = window_registers(updated)
        await self._logged(
            "window_days",
            self._write_verified({base_for(number) + 3: registers[3]}),
            changes=window_changes({number: window}, {number: updated}),
        )

    async def async_set_eco(self, enabled: bool) -> None:
        value = ECO_WORK_MODE if enabled else GENERAL_WORK_MODE
        work_mode = self.data.get("work_mode")
        old = None
        if work_mode is not None:
            old = "eco" if work_mode == ECO_WORK_MODE else "general"
        await self._logged(
            "eco",
            self._write_verified({WORK_MODE_REGISTER: value}),
            changes=[
                {
                    "field": "eco_mode",
                    "old": old,
                    "new": "eco" if enabled else "general",
                }
            ],
        )

    async def async_set_auto_eco(self) -> None:
        """Put the inverter back into fully automatic ECO/Time-of-Use mode.

        Turns ECO work mode on (when it is not already) and re-enables the
        automatic re-check, so the integration keeps the plan optimal on its own.
        """
        self.auto_replan_enabled = True
        if self.data.get("work_mode") != ECO_WORK_MODE:
            await self.async_set_eco(True)
        self._record(
            "mode",
            reason=self._t("log.auto_eco"),
        )

    async def async_set_manual_override(self) -> None:
        """Pause the automatic re-check and leave the windows as they are.

        The windows already on the inverter keep running in ECO mode; only the
        quarter-hour re-check stops, so manual edits are not overwritten. Press
        Auto/ECO mode to resume the automation.
        """
        self.auto_replan_enabled = False
        self._record(
            "mode",
            reason=self._t("log.manual_override"),
        )

    def _manual_window(self) -> int:
        """The window the manual charge/discharge buttons flip."""
        value = int(self._option(CONF_MANUAL_WINDOW, DEFAULT_MANUAL_WINDOW))
        return value if value in WINDOWS else DEFAULT_MANUAL_WINDOW

    def _window_power_percent(self) -> int:
        """The power percentage the planner and the manual buttons write."""
        value = int(self._option(CONF_WINDOW_POWER, DEFAULT_WINDOW_POWER))
        return max(1, min(100, value))

    def manual_mode(self) -> str:
        """The manual window's direction: ``charge``, ``discharge`` or ``off``."""
        window = self.data.get("windows", {}).get(self._manual_window())
        return window.mode if window is not None else "off"

    async def async_set_manual_mode(self, mode: str) -> None:
        """Point the manual window at charge or discharge (or switch it off).

        The manual buttons only flip the direction of one window; its start, stop
        and days are left exactly as the user set them. The automatic re-check is
        paused at the same time, so it cannot undo the manual choice. Pressing the
        button of the mode that is already running turns the window off again.
        """
        number = self._manual_window()
        if mode == self.manual_mode():
            mode = "off"
        power = self._window_power_percent()
        signed = 0 if mode == "off" else (power if mode == "discharge" else -power)
        self.auto_replan_enabled = False
        await self.async_set_window_power(
            number,
            signed,
            action="manual_mode",
            reason=self._t(f"log.manual_{mode}", n=number, power=power),
        )

    # -- cheapest hours ----------------------------------------------------

    def _option(self, key: str, default: Any) -> Any:
        return self.config_entry.options.get(key, default)

    def _log_size(self) -> int:
        """How many activity-log entries to keep (newest first)."""
        value = int(self._option(CONF_LOG_SIZE, DEFAULT_LOG_SIZE))
        return max(0, min(MAX_LOG_SIZE, value))

    def _min_cell_voltage(self) -> int:
        """The cell voltage (mV) below which the automation must stop."""
        value = int(self._option(CONF_MIN_CELL_VOLTAGE, DEFAULT_MIN_CELL_VOLTAGE))
        return max(0, value)

    @property
    def min_cell_voltage_threshold(self) -> int:
        """The configured low-cell stop threshold, in mV."""
        return self._min_cell_voltage()

    def _power_settings(
        self, overrides: Mapping[str, Any]
    ) -> tuple[float, float]:
        """Charge and discharge power, honouring an explicitly set legacy power.

        ``power_kw`` predates the separate ``charge_kw``/``discharge_kw`` and is
        only used as the fallback for both when the user actually set it (in the
        options or in the service call); otherwise the GoodWe's own limits apply.
        """

        def setting(key: str, default: Any) -> Any:
            if key in overrides and overrides[key] is not None:
                return overrides[key]
            return self._option(key, default)

        legacy = setting(CONF_POWER_KW, None)
        charge_default = float(legacy) if legacy is not None else DEFAULT_CHARGE_KW
        discharge_default = (
            float(legacy) if legacy is not None else DEFAULT_DISCHARGE_KW
        )
        return (
            float(setting(CONF_CHARGE_KW, charge_default)),
            float(setting(CONF_DISCHARGE_KW, discharge_default)),
        )

    def _state(self, entity_id: str) -> State | None:
        return self.hass.states.get(entity_id) if entity_id else None

    def _price_slots(self, entity_id: str) -> list:
        state = self._state(entity_id)
        if state is None:
            raise HomeAssistantError(
                self._t("error.price_entity_missing", entity=entity_id)
            )
        attributes = state.attributes
        raw = attributes.get("prices")
        if not raw:
            raw = list(attributes.get("prices_today") or []) + list(
                attributes.get("prices_tomorrow") or []
            )
        if not raw:
            # Nordpool style.
            raw = list(attributes.get("raw_today") or []) + list(
                attributes.get("raw_tomorrow") or []
            )
        slots = slots_from_prices(raw)
        if not slots:
            raise HomeAssistantError(
                self._t("error.price_entity_no_forecast", entity=entity_id)
            )
        return slots

    def _tibber_slots(self) -> list:
        """The Tibber prices this integration fetched itself, as price slots.

        Uses the dedicated price coordinator's data rather than the price sensor,
        so the choice keeps working even when the sensor entity is disabled.
        """
        coordinator = self.price_coordinator
        records = (coordinator.data or {}).get("prices") if coordinator else None
        if not records:
            raise HomeAssistantError(self._t("error.tibber_not_fetched"))
        slots = slots_from_prices(records)
        if not slots:
            raise HomeAssistantError(self._t("error.tibber_no_values"))
        return slots

    def _price_slots_for(self, overrides: Mapping[str, Any]) -> list:
        """The forecast slots for the configured price source.

        ``price_source`` picks between an existing price entity (ENTSO-e/Nordpool
        style, the default) and the Tibber prices the integration fetches itself.
        """
        source = overrides.get(CONF_PRICE_SOURCE) or self._option(
            CONF_PRICE_SOURCE, DEFAULT_PRICE_SOURCE
        )
        if source == PRICE_SOURCE_TIBBER:
            return self._tibber_slots()
        entity = overrides.get(CONF_PRICE_ENTITY) or self._option(
            CONF_PRICE_ENTITY, DEFAULT_PRICE_ENTITY
        )
        return self._price_slots(entity)

    def _float_state(self, entity_id: str, default: float) -> float:
        state = self._state(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return default

    def _plan_cheapest_hours(
        self, overrides: Mapping[str, Any] | None = None
    ) -> Plan:
        """Work out the cheapest charging / most expensive discharging hours.

        Pure computation: it reads the configured price/SoC/PV entities and
        returns the plan (with the reasoning behind every window), but never
        touches the inverter. Both the ``set_cheapest_hours`` and
        ``calculate_cheapest_hours`` services are built on this.
        """
        overrides = overrides or {}

        def setting(key: str, default: Any) -> Any:
            if key in overrides and overrides[key] is not None:
                return overrides[key]
            return self._option(key, default)

        soc_entity = setting(CONF_SOC_ENTITY, DEFAULT_SOC_ENTITY)
        pv_entity = setting(CONF_PV_ENTITY, DEFAULT_PV_ENTITY)
        capacity_kwh = float(setting(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        charge_kw, discharge_kw = self._power_settings(overrides)
        standby_kw = float(setting(CONF_STANDBY_KW, DEFAULT_STANDBY_KW))
        export_hours = setting(CONF_EXPORT_HOURS, DEFAULT_EXPORT_HOURS)
        target_soc = float(setting(CONF_TARGET_SOC, DEFAULT_TARGET_SOC))
        min_soc = float(setting(CONF_MIN_SOC, DEFAULT_MIN_SOC))
        pv_factor = float(setting(CONF_PV_FACTOR, DEFAULT_PV_FACTOR))
        rte = float(setting(CONF_RTE, DEFAULT_RTE))
        charge_windows = int(setting(CONF_CHARGE_WINDOWS, DEFAULT_CHARGE_WINDOWS))
        discharge_windows = int(
            setting(CONF_DISCHARGE_WINDOWS, DEFAULT_DISCHARGE_WINDOWS)
        )
        window_power = int(setting(CONF_WINDOW_POWER, DEFAULT_WINDOW_POWER))

        slots = self._price_slots_for(overrides)
        soc = self._float_state(soc_entity, target_soc)
        pv_remaining = self._float_state(pv_entity, 0.0)

        now = dt.datetime.now(dt.UTC).astimezone()
        return build_plan(
            slots,
            now,
            soc=soc,
            capacity_kwh=capacity_kwh,
            charge_power_kw=charge_kw,
            discharge_power_kw=discharge_kw,
            target_soc=target_soc,
            min_soc=min_soc,
            pv_credit_kwh=pv_remaining * pv_factor,
            standby_kw=standby_kw,
            export_hours=None if export_hours is None else float(export_hours),
            rte=rte,
            charge_windows=charge_windows,
            discharge_windows=discharge_windows,
            power_percent=window_power,
            lang=self.language,
        )

    def _remember_plan(self, plan: Plan) -> None:
        """Remember the reasons behind a computed plan, for the status card.

        The windows on the inverter only carry ``start``/``stop``/``power``/``days``;
        the *why* lives in the planner. Storing the last computed plan here lets
        the dashboard show the reason next to each window without recomputing it.
        """
        self._plan = {
            "windows": [window_to_dict(window) for window in plan.windows],
            "reasons": plan.reason_texts(),
            "summary": plan.summary,
            "notes": list(plan.notes),
            "planned_at": dt.datetime.now(dt.UTC)
            .astimezone()
            .isoformat(timespec="seconds"),
        }
        self._plan_store.async_delay_save(lambda: self._plan, 2.0)
        self._publish(self.data)

    async def async_calculate_cheapest_hours(
        self, overrides: Mapping[str, Any] | None = None
    ) -> Plan:
        """Return the cheapest-hours plan without writing it to the inverter.

        Used by the planner card, which fills its windows with the result so the
        user can review and adjust the plan before applying it.
        """
        return self._plan_cheapest_hours(overrides)

    async def async_forecast_soc(
        self, overrides: Mapping[str, Any] | None = None
    ) -> list[tuple[dt.datetime, float]]:
        """Project the battery state of charge for the *current* windows.

        Starts at the battery's current state of charge and walks forward to the
        end of tomorrow, following the Time-of-Use windows that are configured on
        the inverter right now. The house's standby consumption is drawn from the
        battery too, so the forecast shows the real drain. This is what the week
        card draws after the measured part of its graph: the state of charge you
        get if you leave the plan as it is.
        """
        overrides = overrides or {}

        def setting(key: str, default: Any) -> Any:
            if key in overrides and overrides[key] is not None:
                return overrides[key]
            return self._option(key, default)

        soc_entity = setting(CONF_SOC_ENTITY, DEFAULT_SOC_ENTITY)
        capacity_kwh = float(setting(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        charge_kw, discharge_kw = self._power_settings(overrides)
        standby_kw = float(setting(CONF_STANDBY_KW, DEFAULT_STANDBY_KW))
        target_soc = float(setting(CONF_TARGET_SOC, DEFAULT_TARGET_SOC))
        min_soc = float(setting(CONF_MIN_SOC, DEFAULT_MIN_SOC))
        rte = float(setting(CONF_RTE, DEFAULT_RTE))

        soc = self._float_state(soc_entity, target_soc)
        windows = list(self.data["windows"].values())

        now = dt.datetime.now(dt.UTC).astimezone()
        end = (now + dt.timedelta(days=2)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return forecast_soc(
            windows,
            now,
            end,
            soc=soc,
            capacity_kwh=capacity_kwh,
            charge_power_kw=charge_kw,
            discharge_power_kw=discharge_kw,
            base_load_kw=standby_kw,
            min_soc=min_soc,
            rte=rte,
        )

    async def async_set_cheapest_hours(
        self,
        overrides: Mapping[str, Any] | None = None,
        *,
        action: str = "cheapest_hours",
        reason: str | None = None,
    ) -> Plan:
        """Plan and apply the cheapest charging / most expensive discharging hours."""
        plan = self._plan_cheapest_hours(overrides)
        self._remember_plan(plan)
        after = dict(zip(WINDOWS, plan_registers(plan.windows), strict=True))
        changes = window_changes(dict(self.data["windows"]), after)
        targets = [window_summary(number, after[number]) for number in WINDOWS]

        self.data["windows"].update(after)
        self._publish(self.data)

        mapping = self._registers_for(after)
        mapping.update(self._export_registers(overrides))
        await self._logged(
            action,
            self._write_verified(mapping),
            changes=changes,
            windows=targets,
            reason=reason,
        )
        return plan

    # -- quarter-hour re-check ---------------------------------------------

    def _current_windows(self) -> list[TouWindow] | None:
        """The four windows that are on the inverter right now, or ``None``."""
        windows = self.data.get("windows") or {}
        current = [windows.get(number) for number in WINDOWS]
        if any(window is None for window in current):
            return None
        return current  # type: ignore[return-value]

    def _active_window(self, now: dt.datetime | None = None) -> TouWindow | None:
        """The Time-of-Use window that is running right now, if any.

        A window is active when it is enabled, its day mask covers today and its
        start has passed while its stop has not — the same test the state-of-charge
        forecast uses, so the dashboard and the re-check agree on what is running.
        """
        current = self._current_windows()
        if current is None:
            return None
        if now is None:
            now = dt.datetime.now(dt.UTC).astimezone()
        moment = now.time()
        weekday_bit = day_bit(now.date())
        for window in current:
            if not window.enabled or not (window.mask & weekday_bit):
                continue
            if window.start <= moment < window.stop:
                return window
        return None

    @staticmethod
    def _same_windows(left: Sequence[TouWindow], right: Sequence[TouWindow]) -> bool:
        """Whether two sets of windows are exactly the same."""
        if len(left) != len(right):
            return False
        for a, b in zip(left, right, strict=True):
            if (
                a.start != b.start
                or a.stop != b.stop
                or a.mode != b.mode
                or a.power != b.power
                or a.mask != b.mask
            ):
                return False
        return True

    def _replan_reason(
        self,
        current: Sequence[TouWindow],
        plan: Plan,
        slots: Sequence,
    ) -> str:
        """Explain why the freshly planned windows differ from the current ones."""
        target = plan_registers(plan.windows)
        changed: list[str] = []
        for number, (old, new) in enumerate(
            zip(current, target, strict=True), start=1
        ):
            if self._same_windows([old], [new]):
                continue
            if old.mode != new.mode or old.power != new.power:
                changed.append(
                    self._t(
                        "log.window_change_mode",
                        n=number,
                        old_mode=old.mode,
                        old_power=old.power,
                        new_mode=new.mode,
                        new_power=new.power,
                    )
                )
            else:
                changed.append(
                    self._t(
                        "log.window_change_time",
                        n=number,
                        old_start=f"{old.start:%H:%M}",
                        old_stop=f"{old.stop:%H:%M}",
                        new_start=f"{new.start:%H:%M}",
                        new_stop=f"{new.stop:%H:%M}",
                    )
                )
        parts = [plan.summary]
        if changed:
            parts.append(self._t("log.replan_changed", changes="; ".join(changed)))
        try:
            before = windows_cost(
                current, slots, dt.datetime.now(dt.UTC).astimezone()
            )
            after = windows_cost(
                target, slots, dt.datetime.now(dt.UTC).astimezone()
            )
            if before > after:
                parts.append(self._t("log.replan_benefit", amount=before - after))
        except Exception:  # pragma: no cover - cost estimate is best effort
            pass
        return " ".join(part for part in parts if part)

    def _cell_guard_reason(self) -> str | None:
        """Why the automation must stop because a battery cell is (nearly) flat.

        The state of charge is unreliable, so the lowest cell voltage is the real
        "the battery is empty" signal. When it trips, the automation stops and the
        user is pointed at the SEMS/SolarGo app instead of the integration forcing
        a charge.
        """
        threshold = self._min_cell_voltage()
        if threshold <= 0:
            return None
        voltage = self.data.get("min_cell_voltage")
        if voltage is None or voltage >= threshold:
            return None
        return self._t("log.guard", voltage=voltage, threshold=threshold)

    def _apply_cell_guard(self) -> bool:
        """Stop the automation when the battery is too flat. Returns ``True``."""
        reason = self._cell_guard_reason()
        if reason is None:
            return False
        if self.auto_replan_enabled:
            self.auto_replan_enabled = False
            self._record("guard", result="error", reason=reason)
            self._publish(self.data)
        return True

    async def async_auto_replan(self, *, force: bool = False) -> Plan | None:
        """Re-check the plan and apply it when it is no longer optimal.

        Runs every quarter hour, a minute before the boundary. When the freshly
        planned windows differ from the ones on the inverter, the new plan is
        written and the reason is logged. Returns the applied :class:`Plan`, or
        ``None`` when nothing changed (or auto-replanning is paused).

        A window that is running right now is never re-planned: the inverter only
        switches a window on about a minute after its configured start, and
        re-planning mid-window drops the slots that have already elapsed, which
        slid the window a quarter hour later on every re-check. ``force=True``
        overrides the pause *and* this guard (an explicit "replan now").
        """
        if not self.auto_replan_enabled and not force:
            return None
        if self._apply_cell_guard():
            return None
        if not force and self._active_window() is not None:
            _LOGGER.debug("Auto-replan skipped: a Time-of-Use window is running")
            return None
        current = self._current_windows()
        if current is None:
            return None

        try:
            plan = self._plan_cheapest_hours()
            slots = self._price_slots_for({})
        except HomeAssistantError as err:
            _LOGGER.debug("Auto-replan skipped: %s", err)
            return None

        target = plan_registers(plan.windows)
        if self._same_windows(current, target):
            # Nothing to write, but the plan still explains the windows that are
            # already on the inverter, so keep its reasons for the status card.
            self._remember_plan(plan)
            return None

        reason = self._replan_reason(current, plan, slots)
        return await self.async_set_cheapest_hours(
            action="auto_replan", reason=reason
        )

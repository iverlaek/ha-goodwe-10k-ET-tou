"""Data update coordinator for the GoodWe Time-of-Use integration."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
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
    CONF_ADAPTIVE_LOAD,
    CONF_AUTO_REPLAN,
    CONF_CAPACITY_KWH,
    CONF_CHARGE_KW,
    CONF_CHARGE_WINDOWS,
    CONF_DEGREE_BASE_C,
    CONF_DEVIATION_SIZE,
    CONF_DISCHARGE_KW,
    CONF_DISCHARGE_WINDOWS,
    CONF_EXPORT_HOURS,
    CONF_EXPORT_LIMIT,
    CONF_LANGUAGE,
    CONF_LOAD_ENTITY,
    CONF_LOG_SIZE,
    CONF_MANUAL_WINDOW,
    CONF_MIN_CELL_VOLTAGE,
    CONF_MIN_SOC,
    CONF_POWER_KW,
    CONF_PRICE_ENTITY,
    CONF_PRICE_SOURCE,
    CONF_PV_ENTITY,
    CONF_PV_FACTOR,
    CONF_PV_POWER_ENTITY,
    CONF_PROFILE_DAYS,
    CONF_PROFILE_ENABLED,
    CONF_PROFILE_GRID_ENTITY,
    CONF_PROFILE_GRID_INVERT,
    CONF_PROFILE_LOAD_ENTITY,
    CONF_RTE,
    CONF_SCAN_INTERVAL,
    CONF_SOC_ENTITY,
    CONF_STANDBY_KW,
    CONF_TARGET_SOC,
    CONF_TEMP_COEFF,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_POWER,
    DEFAULT_ADAPTIVE_LOAD,
    DEFAULT_AUTO_REPLAN,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_CHARGE_KW,
    DEFAULT_CHARGE_WINDOWS,
    DEFAULT_DEGREE_BASE_C,
    DEFAULT_DEVIATION_SIZE,
    DEFAULT_DISCHARGE_KW,
    DEFAULT_DISCHARGE_WINDOWS,
    DEFAULT_EXPORT_HOURS,
    DEFAULT_EXPORT_LIMIT,
    DEFAULT_LANGUAGE,
    DEFAULT_LOAD_ENTITY,
    DEFAULT_LOG_SIZE,
    DEFAULT_MANUAL_WINDOW,
    DEFAULT_MIN_CELL_VOLTAGE,
    DEFAULT_MIN_SOC,
    DEFAULT_PRICE_ENTITY,
    DEFAULT_PRICE_SOURCE,
    DEFAULT_PV_ENTITY,
    DEFAULT_PV_FACTOR,
    DEFAULT_PV_POWER_ENTITY,
    DEFAULT_PROFILE_DAYS,
    DEFAULT_PROFILE_ENABLED,
    DEFAULT_PROFILE_GRID_ENTITY,
    DEFAULT_PROFILE_GRID_INVERT,
    DEFAULT_PROFILE_LOAD_ENTITY,
    DEFAULT_RTE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOC_ENTITY,
    DEFAULT_STANDBY_KW,
    DEFAULT_TARGET_SOC,
    DEFAULT_TEMP_COEFF,
    DEFAULT_WEATHER_ENTITY,
    DEFAULT_WINDOW_POWER,
    DOMAIN,
    ECO_WORK_MODE,
    EXPORT_ENABLE_REGISTER,
    EXPORT_ENABLED,
    EXPORT_LIMIT_REGISTER,
    GENERAL_WORK_MODE,
    MAX_DEVIATION_SIZE,
    MAX_LOG_SIZE,
    MAX_PROFILE_DAYS,
    MIN_PROFILE_DAYS,
    PRICE_SOURCE_TIBBER,
    WINDOWS,
    WORK_MODE_REGISTER,
)
from .deviation import (
    apparent_temperature,
    effective_load_kw,
    learn,
    make_sample,
)
from .deviation import trim as trim_deviation
from .goodwe_udp import (
    GoodweError,
    GoodweProtocolError,
    GoodweRejectedError,
    GoodweUdpClient,
)
from .i18n import resolve_language, translate
from .profile import (
    QUARTERS,
    average_by_quarter,
    covered_days,
    daily_kwh,
    split_grid,
)
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
#: Seconds to wait before re-sending a write the inverter did not confirm. The
#: older GoodWe Wi-Fi dongle is easily overloaded, so hammering it back-to-back
#: only makes things worse; a short pause lets it recover.
_RETRY_DELAY = 2.0
#: The delayed re-send of a failed write waits a random time between these bounds
#: (seconds), so several integrations do not hit the dongle in lock-step. Every
#: further re-send waits :data:`_RETRY_STEP` seconds longer than the previous one,
#: backing off gently while never giving up.
_RETRY_MIN = 20.0
_RETRY_MAX = 90.0
#: Seconds added to the wait of every re-send after the first.
_RETRY_STEP = 7.0


def next_retry_delay(previous: float | None = None) -> float:
    """The wait before the next re-send of a failed write.

    The first wait is a random time between :data:`_RETRY_MIN` and
    :data:`_RETRY_MAX` seconds; every further re-send waits :data:`_RETRY_STEP`
    seconds longer than the previous one, so a dongle that stays busy is hit a
    little less often each round.
    """
    if previous is None:
        return random.uniform(_RETRY_MIN, _RETRY_MAX)
    return previous + _RETRY_STEP


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


def _as_float(value: Any) -> float | None:
    """Coerce a state or attribute to a float, or ``None`` when it is not one."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
            "deviation": [],
            "learned": None,
            "profile": None,
            "retries": {"total": 0, "today": 0, "last_attempts": None, "last_time": None},
        }
        #: How often a write had to be re-sent because the flaky dongle did not
        #: confirm it (see :meth:`retry_summary`). Persisted so the tally survives
        #: a restart; ``today`` is reset when the calendar day changes.
        self._retries: dict[str, Any] = {
            "total": 0,
            "today": 0,
            "date": None,
            "last_attempts": None,
            "last_time": None,
        }
        self._retries_store: Store = Store(
            hass, 1, f"{DOMAIN}_retries_{entry.entry_id}"
        )
        self._activity: list[dict[str, Any]] = []
        self._activity_store: Store = Store(
            hass, 1, f"{DOMAIN}_activity_{entry.entry_id}"
        )
        #: The reasoning behind the last computed plan (for the status card).
        self._plan: dict[str, Any] | None = None
        self._plan_store: Store = Store(hass, 1, f"{DOMAIN}_plan_{entry.entry_id}")
        #: The hourly deviation log (plan vs reality) and the model learned from
        #: it. Only the *derived* samples live here; the raw temperature and load
        #: history stays in Home Assistant's recorder database.
        self._deviation: list[dict[str, Any]] = []
        self._deviation_store: Store = Store(
            hass, 1, f"{DOMAIN}_deviation_{entry.entry_id}"
        )
        #: The hour bucket currently being accumulated, if any.
        self._deviation_bucket: dict[str, Any] | None = None
        #: The learned house-load model, or ``None`` while it is still learning.
        self._learned: dict[str, Any] | None = None
        #: The measured quarter-hour consumption profile (load + import + export),
        #: built from the recorder's 5-minute statistics. It replaces the flat
        #: standby the planner used to assume, so the plan follows the real daily
        #: rhythm (the "duck curve"). Persisted so the planner keeps working
        #: right after a restart.
        self._profile: dict[str, Any] | None = None
        self._profile_store: Store = Store(
            hass, 1, f"{DOMAIN}_profile_{entry.entry_id}"
        )
        #: Whether the quarter-hour re-check may write a new plan by itself.
        self.auto_replan_enabled: bool = bool(
            entry.options.get(CONF_AUTO_REPLAN, DEFAULT_AUTO_REPLAN)
        )
        #: Optional Tibber price coordinator backing the price sensor.
        self.price_coordinator: TibberPriceCoordinator | None = None
        #: Writes that failed and are waiting to be re-sent (see
        #: :meth:`_schedule_write_retry`), keyed by the kind of write so a newer
        #: write of the same kind replaces the pending retry.
        self._pending_writes: dict[str, dict[str, Any]] = {}

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

    async def async_load_retries(self) -> None:
        """Load the persisted retry counter (if any) into memory."""
        try:
            stored = await self._retries_store.async_load()
        except Exception:  # pragma: no cover - corrupt/unreadable store
            _LOGGER.warning("Could not load the retry counter", exc_info=True)
            return
        if isinstance(stored, dict):
            for key in self._retries:
                if key in stored:
                    self._retries[key] = stored[key]
        self._publish(self.data)

    def retry_summary(self) -> dict[str, Any]:
        """How often a write had to be re-sent, plus the last retried write.

        ``total`` is the running tally since installation, ``today`` the part of
        it that happened today (reset at midnight), and ``last_attempts`` /
        ``last_time`` describe the most recent write that needed a re-send. The
        retry sensor and the log card show this only while it is non-zero, so a
        healthy dongle stays quiet.
        """
        summary = dict(self._retries)
        today = dt.datetime.now(dt.UTC).astimezone().date().isoformat()
        if summary.get("date") != today:
            summary["today"] = 0
        return summary

    def _count_retries(self, attempts: int | None) -> None:
        """Add a write's extra attempts to the retry tally and persist it."""
        if not attempts or attempts < 2:
            return
        extra = attempts - 1
        now = dt.datetime.now(dt.UTC).astimezone()
        today = now.date().isoformat()
        if self._retries.get("date") != today:
            self._retries["date"] = today
            self._retries["today"] = 0
        self._retries["total"] = int(self._retries.get("total") or 0) + extra
        self._retries["today"] = int(self._retries.get("today") or 0) + extra
        self._retries["last_attempts"] = attempts
        self._retries["last_time"] = now.isoformat(timespec="seconds")
        self._retries_store.async_delay_save(lambda: dict(self._retries), 2.0)

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
        attempts: int | None = None,
        retry: int = 0,
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
                attempts=attempts,
                retry=retry,
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

    # -- deviation log / adaptive load -------------------------------------

    def _deviation_size(self) -> int:
        """How many hourly deviation samples to keep (newest first)."""
        value = int(self._option(CONF_DEVIATION_SIZE, DEFAULT_DEVIATION_SIZE))
        return max(0, min(MAX_DEVIATION_SIZE, value))

    def _adaptive(self) -> bool:
        """Whether the planner may adjust its house load for the weather."""
        return bool(self._option(CONF_ADAPTIVE_LOAD, DEFAULT_ADAPTIVE_LOAD))

    def _degree_base_c(self) -> float:
        """The outdoor temperature below which heating degree days are counted."""
        return float(self._option(CONF_DEGREE_BASE_C, DEFAULT_DEGREE_BASE_C))

    def _weather_now(self) -> tuple[float | None, float | None, float]:
        """(outdoor temp, feels-like temp, °C below the base) from the weather entity.

        Home Assistant's weather entity carries the air temperature, humidity and
        wind, so the feels-like (apparent) temperature is computed here rather than
        read from a separate sensor.
        """
        entity = str(self._option(CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENTITY))
        state = self._state(entity)
        if state is None:
            return (None, None, 0.0)
        attributes = state.attributes
        temp = _as_float(attributes.get("temperature"))
        if temp is not None and str(
            attributes.get("temperature_unit", "°C")
        ).upper().startswith("F"):
            temp = (temp - 32.0) * 5.0 / 9.0
        humidity = _as_float(attributes.get("humidity"))
        wind = _as_float(attributes.get("wind_speed"))
        if wind is not None and str(
            attributes.get("wind_speed_unit", "km/h")
        ).lower().startswith("m"):
            # The apparent-temperature formula wants km/h; the unit is m/s.
            wind = wind * 3.6
        feels = apparent_temperature(temp, humidity, wind)
        below = 0.0 if feels is None else max(0.0, self._degree_base_c() - feels)
        return (temp, feels, below)

    def _load_model(self) -> tuple[float, float]:
        """(base_kw, temp_coeff) the planner should use, learned when possible."""
        configured_base = float(self._option(CONF_STANDBY_KW, DEFAULT_STANDBY_KW))
        configured_coeff = float(self._option(CONF_TEMP_COEFF, DEFAULT_TEMP_COEFF))
        if not self._adaptive():
            return (configured_base, 0.0)
        learned = self._learned or {}
        base = _as_float(learned.get("base_kw"))
        coeff = _as_float(learned.get("temp_coeff"))
        return (
            configured_base if base is None else base,
            configured_coeff if coeff is None else coeff,
        )

    def _standby_setting(self, overrides: Mapping[str, Any]) -> float:
        """The house load for a plan: an explicit override wins, else the model."""
        override = overrides.get(CONF_STANDBY_KW)
        if override is not None:
            return max(0.0, float(override))
        return self.effective_standby_kw()

    def effective_standby_kw(self) -> float:
        """The house load the planner should assume right now, in kW.

        Grows with the heating degree hours from the learned model, so a cold day
        (the air conditioner working harder) makes the planner charge a little
        more and keeps the battery off its floor.
        """
        base, coeff = self._load_model()
        _, feels, below = self._weather_now()
        return effective_load_kw(base, coeff, feels, self._degree_base_c()) if (
            feels is not None
        ) else max(0.0, base)

    def _recompute_learned(self) -> None:
        """Refit the house-load model from the deviation log."""
        if not self._adaptive():
            self._learned = None
            return
        self._learned = learn(
            self._deviation,
            fallback_base_kw=float(self._option(CONF_STANDBY_KW, DEFAULT_STANDBY_KW)),
            fallback_temp_coeff=float(self._option(CONF_TEMP_COEFF, DEFAULT_TEMP_COEFF)),
        )

    async def async_load_deviation(self) -> None:
        """Load the persisted deviation log (if any) and refit the model."""
        try:
            stored = await self._deviation_store.async_load()
        except Exception:  # pragma: no cover - corrupt/unreadable store
            _LOGGER.warning("Could not load the deviation log", exc_info=True)
            return
        if isinstance(stored, list):
            self._deviation = [
                sample for sample in stored if isinstance(sample, dict)
            ]
            trim_deviation(self._deviation, self._deviation_size())
        self._recompute_learned()
        self._publish(self.data)

    def _schedule_save_deviation(self) -> None:
        self._deviation_store.async_delay_save(lambda: list(self._deviation), 2.0)

    def _pv_forecast_remaining(self) -> float | None:
        """The PV forecast that is still ahead for today, in kWh (or ``None``)."""
        entity = str(self._option(CONF_PV_ENTITY, DEFAULT_PV_ENTITY))
        state = self._state(entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        return _as_float(state.state)

    def _sample_deviation(self, now: dt.datetime | None = None) -> None:
        """Accumulate the running hour's deviation; finalise when the hour rolls.

        Called on every coordinator poll. Only the *derived* energy is kept — the
        load and weather sensor history is already in the recorder database.
        """
        now = now or dt.datetime.now(dt.UTC).astimezone()
        hour = now.replace(minute=0, second=0, microsecond=0)
        bucket = self._deviation_bucket
        if bucket is None or bucket["hour"] != hour:
            if bucket is not None:
                self._finalize_deviation(bucket)
            bucket = {
                "hour": hour,
                "last": now,
                "hours": 0.0,
                "actual_kwh": 0.0,
                "planned_kwh": 0.0,
                "pv_kwh": 0.0,
                "temp_hours": 0.0,
                "temp_elapsed": 0.0,
                "feels_hours": 0.0,
                "feels_elapsed": 0.0,
                "degree_hours": 0.0,
                "pv_forecast_start": self._pv_forecast_remaining(),
            }
            self._deviation_bucket = bucket

        elapsed = (now - bucket["last"]).total_seconds() / 3600.0
        bucket["last"] = now
        # Skip a gap (Home Assistant was off, or the poll ran late): integrating
        # across it would invent consumption that never happened.
        if elapsed <= 0 or elapsed > 0.5:
            return

        load_kw = self._float_state(
            str(self._option(CONF_LOAD_ENTITY, DEFAULT_LOAD_ENTITY)), 0.0
        ) / 1000.0
        pv_kw = self._float_state(
            str(self._option(CONF_PV_POWER_ENTITY, DEFAULT_PV_POWER_ENTITY)), 0.0
        ) / 1000.0
        temp, feels, below = self._weather_now()

        bucket["hours"] += elapsed
        bucket["actual_kwh"] += load_kw * elapsed
        bucket["planned_kwh"] += self.effective_standby_kw() * elapsed
        bucket["pv_kwh"] += pv_kw * elapsed
        if temp is not None:
            bucket["temp_hours"] += temp * elapsed
            bucket["temp_elapsed"] += elapsed
        if feels is not None:
            bucket["feels_hours"] += feels * elapsed
            bucket["feels_elapsed"] += elapsed
        bucket["degree_hours"] += below * elapsed

    def _finalize_deviation(self, bucket: Mapping[str, Any]) -> None:
        """Turn a finished hour bucket into one log sample and refit the model."""
        hours = float(bucket["hours"])
        if hours <= 0.01:
            return
        pv_forecast = None
        start = bucket.get("pv_forecast_start")
        end = self._pv_forecast_remaining()
        if start is not None and end is not None:
            pv_forecast = max(0.0, float(start) - float(end))
        temp_elapsed = float(bucket["temp_elapsed"])
        feels_elapsed = float(bucket["feels_elapsed"])
        sample = make_sample(
            bucket["hour"],
            hours=hours,
            actual_kwh=float(bucket["actual_kwh"]),
            planned_kwh=float(bucket["planned_kwh"]),
            outdoor_temp=(
                float(bucket["temp_hours"]) / temp_elapsed if temp_elapsed else None
            ),
            feels_like=(
                float(bucket["feels_hours"]) / feels_elapsed if feels_elapsed else None
            ),
            degree_hour=float(bucket["degree_hours"]),
            pv_kwh=float(bucket["pv_kwh"]),
            pv_forecast_kwh=pv_forecast,
        )
        self._deviation.insert(0, sample)
        trim_deviation(self._deviation, self._deviation_size())
        self._recompute_learned()
        self._schedule_save_deviation()

    def deviation(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Return the deviation log, newest first, optionally capped."""
        entries = self._deviation if limit is None else self._deviation[:limit]
        return [dict(entry) for entry in entries]

    def deviation_summary(self) -> dict[str, Any]:
        """Today's deviation totals, for the sensor and the card."""
        today = dt.datetime.now(dt.UTC).astimezone().date()
        actual = planned = degree = 0.0
        feels_values: list[float] = []
        pv_actual = pv_forecast = 0.0
        pv_seen = False
        count = 0
        for sample in self._deviation:
            try:
                when = dt.datetime.fromisoformat(str(sample.get("time")))
            except (TypeError, ValueError):
                continue
            if when.date() != today:
                continue
            count += 1
            actual += float(sample.get("actual_kwh") or 0.0)
            planned += float(sample.get("planned_kwh") or 0.0)
            degree += float(sample.get("degree_hours") or 0.0)
            feels = sample.get("feels_like")
            if feels is not None:
                feels_values.append(float(feels))
            pv = sample.get("pv_kwh")
            forecast = sample.get("pv_forecast_kwh")
            if pv is not None:
                pv_actual += float(pv)
                pv_seen = True
            if forecast is not None:
                pv_forecast += float(forecast)
        return {
            "samples": count,
            "actual_kwh": round(actual, 2),
            "planned_kwh": round(planned, 2),
            "deviation_kwh": round(actual - planned, 2),
            "degree_days": round(degree / 24.0, 2),
            "feels_like": (
                round(sum(feels_values) / len(feels_values), 1)
                if feels_values
                else None
            ),
            "pv_actual_kwh": round(pv_actual, 2) if pv_seen else None,
            "pv_forecast_kwh": round(pv_forecast, 2) if pv_seen else None,
            "pv_error_kwh": round(pv_actual - pv_forecast, 2) if pv_seen else None,
        }

    # -- quarter-hour consumption profile ----------------------------------

    def _profile_enabled(self) -> bool:
        """Whether the planner should use the measured profile as house load."""
        return bool(self._option(CONF_PROFILE_ENABLED, DEFAULT_PROFILE_ENABLED))

    def _profile_days(self) -> int:
        """How many days of history the profile averages over."""
        value = int(self._option(CONF_PROFILE_DAYS, DEFAULT_PROFILE_DAYS))
        return max(MIN_PROFILE_DAYS, min(MAX_PROFILE_DAYS, value))

    def _profile_load_entity(self) -> str:
        return str(self._option(CONF_PROFILE_LOAD_ENTITY, DEFAULT_PROFILE_LOAD_ENTITY))

    def _profile_grid_entity(self) -> str:
        return str(self._option(CONF_PROFILE_GRID_ENTITY, DEFAULT_PROFILE_GRID_ENTITY))

    def _profile_grid_invert(self) -> bool:
        return bool(self._option(CONF_PROFILE_GRID_INVERT, DEFAULT_PROFILE_GRID_INVERT))

    def _load_profile_sequence(self) -> list[float] | None:
        """The 96 quarter-hour house loads in kW, or ``None`` for the flat load.

        ``None`` means the planner keeps using its flat standby: either the profile
        is switched off or there is not a complete profile yet (fresh install, or
        the recorder has no statistics for the sensor).
        """
        if not self._profile_enabled():
            return None
        load = (self._profile or {}).get("load")
        if not isinstance(load, list) or len(load) != QUARTERS:
            return None
        return [float(value) for value in load]

    async def async_load_profile(self) -> None:
        """Load the persisted profile (if any) into memory."""
        try:
            stored = await self._profile_store.async_load()
        except Exception:  # pragma: no cover - corrupt/unreadable store
            _LOGGER.warning("Could not load the consumption profile", exc_info=True)
            return
        if isinstance(stored, dict):
            self._profile = stored
        self._publish(self.data)

    @staticmethod
    def _profile_points(rows: Any) -> list[tuple[dt.datetime, float]]:
        """Turn recorder statistics rows into ``(local moment, mean)`` points."""
        points: list[tuple[dt.datetime, float]] = []
        for row in rows or []:
            start = row.get("start")
            mean = row.get("mean")
            if start is None or mean is None:
                continue
            moment = dt.datetime.fromtimestamp(float(start), dt.UTC).astimezone()
            points.append((moment, float(mean)))
        return points

    def _collect_profile(self) -> dict[str, Any] | None:
        """Build the profile from the recorder's 5-minute statistics (executor).

        This is blocking (database) work, so it runs in the executor. The power
        statistics are means in watt; they are averaged per quarter of the *local*
        day and turned into kW, because the profile is keyed by the local time of
        day. The GoodWe meter reports a negative value while drawing from the
        grid, so the import/export split honours that (or ``grid_invert``).
        """
        if not self._profile_enabled():
            return None
        load_entity = self._profile_load_entity()
        grid_entity = self._profile_grid_entity()
        days = self._profile_days()
        try:
            from homeassistant.components.recorder import statistics as recorder_stats
        except Exception:  # pragma: no cover - recorder always present in HA
            return None
        now = dt.datetime.now(dt.UTC).astimezone()
        start = now - dt.timedelta(days=days)
        try:
            result = recorder_stats.statistics_during_period(
                self.hass,
                start,
                now,
                statistic_ids={load_entity, grid_entity},
                period="5minute",
                units=None,
                types={"mean"},
            )
        except Exception:
            _LOGGER.debug("Could not read statistics for the profile", exc_info=True)
            return None

        load_points = self._profile_points(result.get(load_entity))
        grid_points = self._profile_points(result.get(grid_entity))
        if not load_points and not grid_points:
            return None

        def _kw(values: list[float]) -> list[float]:
            return [round(value / 1000.0, 4) for value in values]

        # The house-consumption sensor is derived from the PV, battery and meter
        # readings, which the dongle samples a moment apart. While the battery
        # charges hard from the panels the balance briefly tips negative, which is
        # physically impossible for a load. Clamp at zero so the curve is the real
        # *net* house load: the part the panels do not cover, which is exactly the
        # dip in the "duck curve".
        load_kw = [max(0.0, value) for value in _kw(average_by_quarter(load_points))]
        imports, exports = split_grid(
            grid_points, invert=self._profile_grid_invert()
        )
        import_kw = _kw(average_by_quarter(imports))
        export_kw = _kw(average_by_quarter(exports))
        return {
            "load": load_kw,
            "import": import_kw,
            "export": export_kw,
            "load_daily_kwh": daily_kwh(load_kw),
            "import_daily_kwh": daily_kwh(import_kw),
            "export_daily_kwh": daily_kwh(export_kw),
            "days": covered_days(load_points or grid_points),
            "window_days": days,
            "load_entity": load_entity,
            "grid_entity": grid_entity,
            "grid_invert": self._profile_grid_invert(),
            "updated": now.isoformat(timespec="seconds"),
        }

    async def async_refresh_profile(self) -> dict[str, Any] | None:
        """Rebuild the profile from the recorder and publish it.

        Runs the blocking database read in the executor. A failure leaves the
        previous profile in place, so a hiccup never drops the planner back to the
        flat load mid-day.
        """
        try:
            profile = await self.hass.async_add_executor_job(self._collect_profile)
        except Exception:  # pragma: no cover - executor/DB hiccups
            _LOGGER.warning("Could not build the consumption profile", exc_info=True)
            return self._profile
        if profile is not None:
            self._profile = profile
            self._profile_store.async_delay_save(lambda: dict(profile), 2.0)
        self._publish(self.data)
        return self._profile

    def profile_summary(self) -> dict[str, Any]:
        """The measured profile plus its totals, for the sensor and the card."""
        summary = dict(self._profile or {})
        summary["enabled"] = self._profile_enabled()
        summary["window_days"] = self._profile_days()
        summary.setdefault("load", [])
        summary.setdefault("import", [])
        summary.setdefault("export", [])
        return summary

    def _publish(self, data: dict[str, Any]) -> None:
        """Push fresh data to the entities, including the current activity log."""
        data["activity"] = list(self._activity)
        data["plan"] = self._plan
        data["deviation"] = list(self._deviation)
        data["learned"] = self._learned
        data["profile"] = self.profile_summary()
        data["retries"] = self.retry_summary()
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
        # Sample the deviation first, so the log keeps filling even when the
        # inverter itself cannot be reached this round.
        self._sample_deviation()
        try:
            data = await self._call(self._read_all)
        except GoodweError as err:
            raise UpdateFailed(str(err)) from err
        data["activity"] = list(self._activity)
        data["plan"] = self._plan
        data["deviation"] = list(self._deviation)
        data["learned"] = self._learned
        data["profile"] = self.profile_summary()
        data["retries"] = self.retry_summary()
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

    async def _write_verified(self, mapping: Mapping[int, int]) -> int:
        """Write registers, then read the schedule back and confirm them.

        The dongle is flaky and shared, so a write is only believed once the
        inverter reads the intended value back. This is what makes a single
        entity change *stick* instead of silently reverting on the next poll.

        The older GoodWe Wi-Fi dongle (not the LAN/Wi-Fi v2.0 module) is easily
        overloaded, so a transient error — or a read-back that does not match — is
        not fatal: the whole mapping is sent again after a short pause, up to
        :data:`_APPLY_ATTEMPTS` times. Returns how many sends were needed (``1``
        when it stuck immediately); the caller logs that as the retry count.
        """
        last: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(1, _APPLY_ATTEMPTS + 1):
            if attempt > 1:
                await asyncio.sleep(_RETRY_DELAY)
            try:
                await self._write(mapping)
                last = await self._call(self._read_all)
            except (HomeAssistantError, GoodweError) as err:
                # A dongle that is busy drops requests entirely; send it again.
                last_error = err
                _LOGGER.warning(
                    "Write attempt %s/%s failed (%s), re-sending",
                    attempt,
                    _APPLY_ATTEMPTS,
                    err,
                )
                continue
            actual = self._registers_for(last["windows"])
            actual[WORK_MODE_REGISTER] = last["work_mode"]
            actual[EXPORT_ENABLE_REGISTER] = last["export_enabled"]
            actual[EXPORT_LIMIT_REGISTER] = last["export_limit"]
            if all(actual.get(register) == value for register, value in mapping.items()):
                self._publish(last)
                return attempt
            last_error = None
            _LOGGER.warning(
                "Write verification attempt %s/%s did not match, re-sending",
                attempt,
                _APPLY_ATTEMPTS,
            )

        if last is not None:
            self._publish(last)
        error = HomeAssistantError(self._t("error.readback", attempts=_APPLY_ATTEMPTS))
        error.attempts = _APPLY_ATTEMPTS  # type: ignore[attr-defined]
        raise error from last_error

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
        key: str,
        action: str,
        operation: Callable[[], Awaitable[int]],
        *,
        changes: Sequence[Mapping[str, Any]] | None = None,
        windows: Sequence[Mapping[str, Any]] | None = None,
        reason: str | None = None,
        plan: bool = False,
    ) -> None:
        """Run a write operation, log it, and re-send it later if it fails.

        ``operation`` is a zero-argument factory returning a *fresh* awaitable,
        so the same write can be sent again after a random delay (see
        :func:`next_retry_delay`). The flaky dongle sometimes drops a whole
        write; instead of leaving the inverter misprogrammed, the write is
        re-sent until it is confirmed, so the log ends on a success.

        ``operation`` resolves to the number of sends the write needed, which is
        recorded on the entry (so the log can say "3x mislukt") and added to the
        retry counter. ``key`` identifies the write so a newer write of the same
        kind replaces the pending retry; ``plan`` marks the full-plan writes that
        a manual override should cancel.
        """
        try:
            attempts = await operation()
        except HomeAssistantError as err:
            attempts = getattr(err, "attempts", None)
            self._count_retries(attempts)
            self._record(
                action,
                changes=changes,
                windows=windows,
                result="error",
                error=str(err),
                reason=reason,
                attempts=attempts,
            )
            self._schedule_write_retry(
                key,
                action,
                operation,
                changes=changes,
                windows=windows,
                reason=reason,
                plan=plan,
            )
            raise
        self._clear_write_retry(key)
        self._count_retries(attempts)
        self._record(
            action,
            changes=changes,
            windows=windows,
            reason=reason,
            attempts=attempts,
        )

    # -- delayed write retries ---------------------------------------------

    def _schedule_write_retry(
        self,
        key: str,
        action: str,
        operation: Callable[[], Awaitable[int]],
        *,
        changes: Sequence[Mapping[str, Any]] | None = None,
        windows: Sequence[Mapping[str, Any]] | None = None,
        reason: str | None = None,
        plan: bool = False,
        retry: int = 0,
        previous_delay: float | None = None,
    ) -> None:
        """Queue a failed write to be sent again after a delay.

        The first delay is random (see :func:`next_retry_delay`); a re-send of a
        write that keeps failing waits ``previous_delay`` + :data:`_RETRY_STEP`
        seconds, so the wait grows with every round.
        """
        self._clear_write_retry(key)
        delay = next_retry_delay(previous_delay)
        entry: dict[str, Any] = {
            "action": action,
            "operation": operation,
            "changes": changes,
            "windows": windows,
            "reason": reason,
            "plan": plan,
            "retry": retry,
            "delay": delay,
            "unsub": None,
        }
        self._pending_writes[key] = entry
        entry["unsub"] = async_call_later(
            self.hass, delay, partial(self._run_write_retry, key)
        )

    def _clear_write_retry(self, key: str) -> None:
        """Forget a pending retry, cancelling its timer."""
        entry = self._pending_writes.pop(key, None)
        if entry is not None and entry.get("unsub") is not None:
            entry["unsub"]()

    def async_cancel_pending_writes(self) -> None:
        """Drop every pending retry, for example when the entry unloads."""
        for key in list(self._pending_writes):
            self._clear_write_retry(key)

    def _cancel_plan_retries(self) -> None:
        """Drop pending full-plan re-sends, e.g. when a manual override is set.

        A manual choice must win from a plan the automation wanted to push, so
        the queued re-send of that plan is dropped instead of overwriting it.
        """
        for key, entry in list(self._pending_writes.items()):
            if entry.get("plan"):
                self._clear_write_retry(key)

    async def _run_write_retry(self, key: str, _now: dt.datetime | None = None) -> None:
        """Send a previously failed write again, logging and re-queuing it."""
        entry = self._pending_writes.get(key)
        if entry is None:
            return
        # The timer has fired; forget its handle so a reschedule does not try to
        # cancel an already-fired callback.
        entry["unsub"] = None
        entry["retry"] = int(entry.get("retry") or 0) + 1
        action = entry["action"]
        try:
            attempts = await entry["operation"]()
        except HomeAssistantError as err:
            attempts = getattr(err, "attempts", None)
            self._count_retries(attempts)
            self._record(
                action,
                changes=entry["changes"],
                windows=entry["windows"],
                result="error",
                error=str(err),
                reason=entry["reason"],
                attempts=attempts,
                retry=entry["retry"],
            )
            self._schedule_write_retry(
                key,
                action,
                entry["operation"],
                changes=entry["changes"],
                windows=entry["windows"],
                reason=entry["reason"],
                plan=entry["plan"],
                retry=entry["retry"],
                previous_delay=entry["delay"],
            )
            return
        self._clear_write_retry(key)
        self._count_retries(attempts)
        self._record(
            action,
            changes=entry["changes"],
            windows=entry["windows"],
            reason=entry["reason"],
            attempts=attempts,
            retry=entry["retry"],
        )

    async def async_apply(self) -> None:
        """Write every window to the inverter and verify the result."""
        current = {number: self._window(number) for number in WINDOWS}
        targets = [window_summary(number, current[number]) for number in WINDOWS]
        mapping = self._registers_for(current)
        mapping.update(self._export_registers())
        await self._logged(
            "apply",
            "apply",
            lambda: self._write_verified(mapping),
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
            "export_limit",
            lambda: self._write_verified(mapping),
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
            "set_windows",
            lambda: self._write_verified(self._registers_for(after)),
            changes=changes,
            windows=targets,
            plan=True,
        )

    async def async_set_window_time(
        self, number: int, kind: str, value: dt.time
    ) -> None:
        window = self._window(number)
        updated = replace(window, **{kind: value})
        relative = 0 if kind == "start" else 1
        await self._logged(
            f"window_time:{number}:{kind}",
            "window_time",
            lambda: self._write_verified(
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
            f"window_power:{number}",
            action,
            lambda: self._write_verified(
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
            f"window_days:{number}",
            "window_days",
            lambda: self._write_verified({base_for(number) + 3: registers[3]}),
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
            "eco",
            lambda: self._write_verified({WORK_MODE_REGISTER: value}),
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
        self._cancel_plan_retries()
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
        self._cancel_plan_retries()
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
        standby_kw = self._standby_setting(overrides)
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

        # The measured quarter-hour load profile replaces the flat standby when we
        # have one, so the plan follows the real daily rhythm.
        load_profile = self._load_profile_sequence()

        now = dt.datetime.now(dt.UTC).astimezone()
        plan = build_plan(
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
            load_profile=load_profile,
            export_hours=None if export_hours is None else float(export_hours),
            rte=rte,
            charge_windows=charge_windows,
            discharge_windows=discharge_windows,
            power_percent=window_power,
            lang=self.language,
        )
        # Explain the weather adjustment, so the number in the standby note does
        # not look like a mystery. Skipped when the user pinned standby_kw or when
        # the measured profile already carries the real load.
        if (
            self._adaptive()
            and load_profile is None
            and CONF_STANDBY_KW not in overrides
        ):
            _, feels, below = self._weather_now()
            if feels is not None and below > 0:
                base, coeff = self._load_model()
                plan.notes.append(
                    self._t(
                        "plan.load_note",
                        load=standby_kw,
                        base=base,
                        coeff=coeff,
                        degrees=below,
                        feels=feels,
                        base_temp=self._degree_base_c(),
                    )
                )
        return plan

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
        standby_kw = self._standby_setting(overrides)
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
            load_profile=self._load_profile_sequence(),
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

        # The windows are *not* updated optimistically: the coordinator's state is
        # only trusted once the inverter reads the new values back. If every send
        # fails, the state keeps the inverter's real (old) schedule, so the next
        # quarter-hour re-check sees the difference and sends the plan again.
        mapping = self._registers_for(after)
        mapping.update(self._export_registers(overrides))
        # A manual "Set cheapest hours" has no reason of its own, so log the
        # plan's explanation. That is where the battery-cost and export-spread
        # calculation lives, and it makes the activity log show why the windows
        # were chosen (the auto-replan path passes its own, larger reason).
        logged_reason = reason if reason is not None else plan.summary
        await self._logged(
            "set_windows",
            action,
            lambda: self._write_verified(mapping),
            changes=changes,
            windows=targets,
            reason=logged_reason,
            plan=True,
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

"""Pure planning logic for the "cheapest hours" service.

The inverter only has four Time-of-Use windows and each window is a single
contiguous ``start``-``stop`` range on one or more weekdays. This module turns a
list of quarter-hourly prices plus the current battery state into a set of
windows that charge in the cheapest slots and discharge in the most expensive
ones.

Everything here is pure (no Home Assistant imports) so it is easy to test.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .const import (
    DEFAULT_CAPACITY_KWH,
    DEFAULT_MIN_SOC,
    DEFAULT_POWER_KW,
    DEFAULT_TARGET_SOC,
    SLOT_MINUTES,
    WINDOWS,
)
from .registers import TouWindow, mask_to_days


@dataclass(frozen=True)
class PriceSlot:
    """One quarter-hour price slot."""

    time: dt.datetime
    price: float


def slots_from_prices(prices: Iterable[Mapping[str, object]]) -> list[PriceSlot]:
    """Normalise a price forecast into :class:`PriceSlot` objects.

    Understands the shapes used by the common integrations: ENTSO-e exposes
    ``{time, price}`` while Nordpool exposes ``{start, end, value}``. Accepts
    ISO-8601 strings or :class:`datetime.datetime` for the timestamp. Items that
    cannot be parsed are skipped rather than raising, because the attribute comes
    straight from another integration.
    """
    result: list[PriceSlot] = []
    for item in prices or ():
        if not isinstance(item, Mapping):
            continue
        raw_time = item.get("time", item.get("start"))
        raw_price = item.get("price", item.get("value"))
        if raw_time is None or raw_price is None:
            continue
        moment = raw_time
        if isinstance(moment, str):
            try:
                moment = dt.datetime.fromisoformat(moment)
            except ValueError:
                continue
        if not isinstance(moment, dt.datetime):
            continue
        try:
            price = float(raw_price)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        result.append(PriceSlot(moment, price))
    result.sort(key=lambda slot: slot.time)
    return result


def _group_runs(indices: Sequence[int]) -> list[tuple[int, int]]:
    """Group sorted slot indices into inclusive ``(start, end)`` runs."""
    runs: list[tuple[int, int]] = []
    for index in sorted(indices):
        if runs and index == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], index)
        else:
            runs.append((index, index))
    return runs


def _merge_runs(runs: list[tuple[int, int]], max_runs: int) -> list[tuple[int, int]]:
    """Merge the closest runs until at most ``max_runs`` remain."""
    runs = list(runs)
    while len(runs) > max_runs and len(runs) > 1:
        gaps = [runs[i + 1][0] - runs[i][1] for i in range(len(runs) - 1)]
        smallest = min(range(len(gaps)), key=gaps.__getitem__)
        runs[smallest] = (runs[smallest][0], runs[smallest + 1][1])
        del runs[smallest + 1]
    return runs


def _next_midnight(moment: dt.datetime) -> dt.datetime:
    """The next midnight strictly after ``moment``."""
    start_of_day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if start_of_day > moment:
        return start_of_day
    return start_of_day + dt.timedelta(days=1)


def _make_window(
    start_dt: dt.datetime,
    stop_dt: dt.datetime,
    mode: str,
    power_percent: int,
) -> TouWindow | None:
    """Build a single window for a half-open ``[start_dt, stop_dt)`` range."""
    if stop_dt <= start_dt:
        return None
    start_time = start_dt.time()
    stop_time = stop_dt.time()
    # The inverter stores a plain HH:MM and a weekly day mask. Keep every window
    # inside a single day: a block that ends exactly at midnight is clamped to
    # 23:59 so ``start < stop`` always holds.
    if stop_time == dt.time(0, 0):
        stop_time = dt.time(23, 59)
    if start_time >= stop_time:
        return None
    return TouWindow(
        start=start_time,
        stop=stop_time,
        mode=mode,
        power=min(100, max(1, int(power_percent))),
        days=mask_to_days(1 << start_dt.weekday()),
    )


def _run_to_windows(
    run: tuple[int, int],
    slots: Sequence[PriceSlot],
    slot_minutes: int,
    mode: str,
    power_percent: int,
) -> list[TouWindow]:
    """Turn a run of slot indices into one or more day-local windows."""
    start_dt = slots[run[0]].time
    stop_dt = slots[run[1]].time + dt.timedelta(minutes=slot_minutes)
    windows: list[TouWindow] = []
    cursor = start_dt
    while cursor < stop_dt:
        boundary = min(stop_dt, _next_midnight(cursor))
        window = _make_window(cursor, boundary, mode, power_percent)
        if window is not None:
            windows.append(window)
        cursor = boundary
    return windows


def _select_indices(
    indexed: Sequence[tuple[int, PriceSlot]],
    count: int,
    *,
    cheapest: bool,
) -> list[int]:
    """Return the ``count`` cheapest (or most expensive) slot indices."""
    if count <= 0:
        return []
    ordered = sorted(indexed, key=lambda item: item[1].price, reverse=not cheapest)
    return [index for index, _ in ordered[:count]]


def plan_windows(
    prices: Sequence[PriceSlot],
    now: dt.datetime,
    *,
    soc: float,
    capacity_kwh: float = DEFAULT_CAPACITY_KWH,
    power_kw: float = DEFAULT_POWER_KW,
    target_soc: float = DEFAULT_TARGET_SOC,
    min_soc: float = DEFAULT_MIN_SOC,
    pv_credit_kwh: float = 0.0,
    max_windows: int = len(WINDOWS),
    charge_windows: int = 2,
    discharge_windows: int = 2,
    slot_minutes: int = SLOT_MINUTES,
    power_percent: int = 100,
) -> list[TouWindow]:
    """Plan Time-of-Use windows from prices and the current battery state.

    ``prices`` is the full list of quarter-hourly slots (today and, when
    available, tomorrow). Only slots at or after ``now`` are considered.

    ``pv_credit_kwh`` is the energy the forecast expects the battery to receive
    for free; it is subtracted from the amount that has to be bought from the
    grid. The result is sorted by start time and never exceeds ``max_windows``.
    """
    if not prices:
        return []

    future = [slot for slot in prices if slot.time >= now]
    if not future:
        return []

    slot_hours = slot_minutes / 60.0
    per_slot_kwh = max(0.1, float(power_kw) * slot_hours)

    capacity_kwh = max(0.0, float(capacity_kwh))
    target_soc = min(100.0, max(0.0, float(target_soc)))
    min_soc = min(target_soc, max(0.0, float(min_soc)))
    soc = min(100.0, max(0.0, float(soc)))

    charge_energy = max(
        0.0, (target_soc - soc) / 100.0 * capacity_kwh - max(0.0, float(pv_credit_kwh))
    )
    discharge_energy = max(0.0, (target_soc - min_soc) / 100.0 * capacity_kwh)

    n_charge = min(len(future), math.ceil(charge_energy / per_slot_kwh))
    n_discharge = min(len(future), math.ceil(discharge_energy / per_slot_kwh))

    budget = max(1, int(max_windows))
    charge_windows = max(0, min(int(charge_windows), budget))
    discharge_windows = max(0, min(int(discharge_windows), budget))

    indexed = list(enumerate(future))

    charge_idx = _select_indices(indexed, n_charge, cheapest=True)
    charge_runs = _merge_runs(_group_runs(charge_idx), charge_windows)

    used = set(charge_idx)
    remaining = [(index, slot) for index, slot in indexed if index not in used]
    discharge_idx = _select_indices(remaining, n_discharge, cheapest=False)
    discharge_runs = _merge_runs(_group_runs(discharge_idx), discharge_windows)

    windows: list[TouWindow] = []
    for run in charge_runs:
        windows.extend(
            _run_to_windows(run, future, slot_minutes, "charge", power_percent)
        )
    for run in discharge_runs:
        windows.extend(
            _run_to_windows(run, future, slot_minutes, "discharge", power_percent)
        )

    windows.sort(key=lambda window: window.start)
    return windows[:budget]


def plan_registers(
    windows: Sequence[TouWindow], window_count: int = len(WINDOWS)
) -> list[TouWindow]:
    """Pad a plan to exactly ``window_count`` windows, disabling the rest."""
    plan = list(windows[:window_count])
    while len(plan) < window_count:
        plan.append(
            TouWindow(
                start=dt.time(0, 0),
                stop=dt.time(0, 0),
                mode="off",
                power=0,
                days="none",
            )
        )
    return plan

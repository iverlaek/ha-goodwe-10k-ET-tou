"""Pure planning logic for the "cheapest hours" service.

The inverter only has four Time-of-Use windows and each window is a single
contiguous ``start``-``stop`` range on one or more weekdays. This module turns a
list of quarter-hourly prices plus the current battery state into a set of
windows that charge in the cheapest slots and discharge in the most expensive
ones.

The strategy is cost-aware:

* **Charge to the target (usually full), PV as a discount.** The battery is
  charged up to the target state of charge in the cheapest quarter hours. The
  forecast PV is treated as a discount on the energy the house would otherwise
  buy, so it is a reason to charge cheaply at night rather than a reason to
  charge less.
* **Discharge on the most expensive hours**, but only down to a floor and only
  up to an export cap, so the battery is never emptied so early that the house
  has to buy expensive power later. The house's standby consumption is taken
  into account so the plan is honest about what the battery really delivers.
  Purchased electricity is priced a few cents higher to cover battery wear, and
  the battery only exports when the spread over that buy price clears a minimum
  (20 ct/kWh by default).
* Every choice is explained in a human readable reason, which the planner card
  shows next to the plan.

Everything here is pure (no Home Assistant imports) so it is easy to test.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .const import (
    DEFAULT_BATTERY_COST_PER_KWH,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_CHARGE_KW,
    DEFAULT_DISCHARGE_KW,
    DEFAULT_EXPORT_HOURS,
    DEFAULT_EXPORT_MIN_SPREAD,
    DEFAULT_HOLD_WINDOW_PERCENT,
    DEFAULT_LANGUAGE,
    DEFAULT_MIN_SOC,
    DEFAULT_POWER_KW,
    DEFAULT_RTE,
    DEFAULT_STANDBY_KW,
    DEFAULT_TARGET_SOC,
    SLOT_MINUTES,
    WINDOWS,
)
from .i18n import day_label as _i18n_day_label
from .i18n import translate, weekday_name
from .profile import average_kw as _profile_average_kw
from .profile import daily_kwh as _profile_daily_kwh
from .profile import load_kwh as _profile_load_kwh
from .registers import TouWindow, day_bit, mask_to_days


def _day_label(day: dt.date, today: dt.date, lang: str = DEFAULT_LANGUAGE) -> str:
    """A short label for ``day`` relative to ``today`` (``vandaag``/``morgen``).

    The price horizon only spans today and tomorrow, so "today"/"tomorrow" read
    more naturally than a weekday name. Anything further out (or in the past)
    falls back to the weekday.
    """
    return _i18n_day_label(lang, day, today)


@dataclass(frozen=True)
class PriceSlot:
    """One quarter-hour price slot."""

    time: dt.datetime
    price: float


@dataclass(frozen=True)
class PlanReason:
    """Why one planned window was filled in the way it was.

    ``window`` is the 1-based window number in the plan (0 for a general note),
    ``mode`` is ``charge``/``discharge``/``off`` and ``text`` is the explanation
    the planner card shows.
    """

    window: int
    mode: str
    text: str

    def as_dict(self) -> dict[str, object]:
        return {"window": self.window, "mode": self.mode, "text": self.text}


@dataclass
class Plan:
    """A planned set of windows plus the reasoning behind it."""

    windows: list[TouWindow] = field(default_factory=list)
    reasons: list[PlanReason] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    lang: str = DEFAULT_LANGUAGE
    #: The export-spread threshold this plan was computed with, so the dashboard
    #: can show the same value in its selector.
    export_min_spread: float | None = None

    @property
    def summary(self) -> str:
        """A one-line summary suitable for the activity log."""
        return " ".join(self.notes) if self.notes else translate(
            self.lang, "plan.summary_empty"
        )

    def reason_texts(self) -> list[dict[str, object]]:
        return [reason.as_dict() for reason in self.reasons]


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
        days=mask_to_days(day_bit(start_dt)),
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


def _plan_overview(
    planned: Sequence[tuple[str, tuple[int, int], float, int]],
    slots: Sequence[PriceSlot],
    slot_minutes: int,
    today: dt.date,
    lang: str = DEFAULT_LANGUAGE,
) -> str:
    """A per-day summary of the plan, e.g. ``vandaag: ontladen 17:30-18:30``.

    The planner reasons name the day of their own window, but a reader who looks
    at the plan as a whole still has to piece together which day each window runs
    on. This one line does that for them.
    """
    if not planned:
        return ""
    by_day: dict[dt.date, list[str]] = {}
    for mode, run, _power, _percent in planned:
        start_dt = slots[run[0]].time
        stop_dt = slots[run[1]].time + dt.timedelta(minutes=slot_minutes)
        verb = translate(lang, f"mode.{mode}")
        # Split a run that crosses midnight into one item per day, exactly like
        # the windows it turns into.
        cursor = start_dt
        while cursor < stop_dt:
            boundary = min(stop_dt, _next_midnight(cursor))
            day = cursor.date()
            segment_stop = min(
                boundary, cursor.replace(hour=23, minute=59, second=0, microsecond=0)
            )
            by_day.setdefault(day, []).append(
                translate(
                    lang,
                    "plan.overview_item",
                    verb=verb,
                    start=f"{cursor:%H:%M}",
                    stop=f"{segment_stop:%H:%M}",
                )
            )
            cursor = boundary
    parts = [
        translate(
            lang,
            "plan.overview_day",
            day=_day_label(day, today, lang),
            items=", ".join(items),
        )
        for day, items in sorted(by_day.items())
    ]
    return translate(lang, "plan.overview", parts="; ".join(parts))


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


def _best_contiguous_block(
    day_slots: Sequence[tuple[int, PriceSlot]], count: int
) -> list[int]:
    """Indices of the ``count`` consecutive slots with the highest total price.

    Slots are consecutive only when their horizon indices are adjacent, so a gap
    left by a charge slot is never filled in, and only one day's slots are passed
    in, so the block never crosses midnight.
    """
    if count <= 0 or not day_slots:
        return []
    groups: list[list[tuple[int, PriceSlot]]] = []
    for item in day_slots:
        if groups and item[0] == groups[-1][-1][0] + 1:
            groups[-1].append(item)
        else:
            groups.append([item])
    best: list[int] = []
    best_sum = float("-inf")
    for group in groups:
        size = min(count, len(group))
        prices = [slot.price for _, slot in group]
        window = sum(prices[:size])
        best_start = 0
        best_window = window
        for start in range(1, len(prices) - size + 1):
            window += prices[start + size - 1] - prices[start - 1]
            if window > best_window:
                best_window = window
                best_start = start
        if best_window > best_sum:
            best_sum = best_window
            best = [group[i][0] for i in range(best_start, best_start + size)]
    return best


def _discharge_runs_per_day(
    remaining: Sequence[tuple[int, PriceSlot]],
    *,
    discharge_per_slot: float,
    total_energy_kwh: float,
    per_day_energy_kwh: float,
    max_runs: int,
) -> list[tuple[int, int]]:
    """One capped, contiguous discharge run per calendar day (today first).

    Each day's budget is the export cap, so the rest of today gets its own
    discharge window instead of the whole budget being spent on tomorrow's peak.
    Picking a contiguous block keeps the window at the cap instead of stretching
    it over the cheap hours between two peaks. Days are filled chronologically and
    the total stays capped by ``total_energy_kwh``.
    """
    if total_energy_kwh <= 0 or discharge_per_slot <= 0 or max_runs <= 0:
        return []
    by_day: dict[dt.date, list[tuple[int, PriceSlot]]] = {}
    for index, slot in remaining:
        by_day.setdefault(slot.time.date(), []).append((index, slot))
    runs: list[tuple[int, int]] = []
    left = total_energy_kwh
    for day in sorted(by_day):
        if left <= 0 or len(runs) >= max_runs:
            break
        budget = min(per_day_energy_kwh, left)
        count = min(len(by_day[day]), math.ceil(budget / discharge_per_slot))
        block = _best_contiguous_block(by_day[day], count)
        if not block:
            continue
        runs.append((block[0], block[-1]))
        left -= len(block) * discharge_per_slot
    return runs


def _run_stats(
    run: tuple[int, int],
    slots: Sequence[PriceSlot],
    slot_minutes: int,
    power_kw: float,
) -> dict[str, float]:
    """Average price, duration and energy for one run of slot indices."""
    count = run[1] - run[0] + 1
    prices = [slots[index].price for index in range(run[0], run[1] + 1)]
    return {
        "count": float(count),
        "minutes": float(count * slot_minutes),
        "hours": count * slot_minutes / 60.0,
        "avg_price": sum(prices) / len(prices) if prices else 0.0,
        "min_price": min(prices) if prices else 0.0,
        "max_price": max(prices) if prices else 0.0,
        "energy_kwh": count * max(0.0, float(power_kw)) * slot_minutes / 60.0,
    }


@dataclass(frozen=True)
class _ChargeChoice:
    """The outcome of the cost simulation: what to do and what it costs."""

    baseline: float
    cost: float
    charge: tuple[int, ...] = ()
    hold: tuple[int, int] | None = None
    charge_cost: float | None = None
    hold_cost: float | None = None


def _slot_load_kwh(
    profile: Sequence[float] | None,
    standby_kw: float,
    moment: dt.datetime,
    slot_minutes: int,
) -> float:
    """Energy the house draws in one slot, from the measured profile if we have it."""
    if profile is not None:
        return _profile_load_kwh(
            profile,
            moment,
            moment + dt.timedelta(minutes=slot_minutes),
            slot_minutes=slot_minutes,
            fallback_kw=standby_kw,
        )
    return max(0.0, standby_kw) * slot_minutes / 60.0


def _simulate_cost(
    future: Sequence[PriceSlot],
    profile: Sequence[float] | None,
    standby_kw: float,
    slot_minutes: int,
    *,
    soc_kwh: float,
    usable_kwh: float,
    charge_kw: float,
    discharge_kw: float,
    rte: float,
    battery_cost_per_kwh: float,
    charge_idx: Iterable[int] = (),
    hold_idx: Iterable[int] = (),
    hold_kw: float = 0.0,
) -> float:
    """Predicted grid cost (EUR) of running the battery over the whole horizon.

    This follows what the inverter actually does in general mode: outside a
    window the battery covers the house load (up to its power limit and its
    charge) and the rest is bought. A charge window buys the full charge power
    *plus* the house load and only the charged kWh carry the battery wear. A hold
    window buys the house load at the full house load and keeps the battery in
    place with a small trickle, so its energy survives until the expensive hour.

    Everything is expressed in kWh above ``min_soc``, which is the energy the
    battery can actually use.
    """
    slot_hours = slot_minutes / 60.0
    soc = max(0.0, min(usable_kwh, soc_kwh))
    charge = set(charge_idx)
    hold = set(hold_idx)
    cost = 0.0
    for index, slot in enumerate(future):
        load = _slot_load_kwh(profile, standby_kw, slot.time, slot_minutes)
        if index in charge and soc < usable_kwh - 1e-9:
            drawn = max(0.0, charge_kw) * slot_hours
            soc = min(usable_kwh, soc + drawn * rte)
            cost += drawn * (slot.price + battery_cost_per_kwh)
            cost += load * slot.price
        elif index in hold:
            room = max(0.0, usable_kwh - soc)
            drawn = min(max(0.0, hold_kw) * slot_hours, room / max(rte, 1e-9))
            soc = min(usable_kwh, soc + drawn * rte)
            cost += drawn * slot.price
            cost += load * slot.price
        else:
            discharge = min(load, soc, max(0.0, discharge_kw) * slot_hours)
            soc = max(0.0, soc - discharge)
            cost += (load - discharge) * slot.price
    return cost


def _hold_starts(
    future: Sequence[PriceSlot],
    now: dt.datetime,
    slot_minutes: int,
    limit: int = 3,
) -> list[int]:
    """Candidate slots to hold the battery until: the start of a price peak.

    The battery is held from now until just before one of these slots. Holding
    until the single most expensive slot is not always best (the peak is a whole
    block, and the battery may be better spent on an earlier one), so the three
    most expensive, well-separated slots are offered to the simulation.
    """
    if len(future) < 9:
        return []
    minimum = max(4, int(round(60 / max(1, slot_minutes))))
    starts: list[int] = []
    by_price = sorted(
        range(len(future)), key=lambda i: future[i].price, reverse=True
    )
    for index in by_price:
        if index < minimum or future[index].time - now < dt.timedelta(hours=2):
            continue
        if any(abs(index - other) < 4 for other in starts):
            continue
        starts.append(index)
        if len(starts) >= limit:
            break
    return sorted(starts)


def _choose_charge(
    indexed: Sequence[tuple[int, PriceSlot]],
    future: Sequence[PriceSlot],
    profile: Sequence[float] | None,
    standby_kw: float,
    slot_minutes: int,
    *,
    now: dt.datetime,
    soc_kwh: float,
    usable_kwh: float,
    charge_kw: float,
    discharge_kw: float,
    rte: float,
    battery_cost_per_kwh: float,
    hold_kw: float,
) -> _ChargeChoice:
    """The cheapest of: leave the battery alone, charge N cheap slots, or hold.

    ``cost`` is the predicted cost of the winning option; ``baseline`` is the
    cost of doing nothing, so the note can show the euro saving. The baseline is
    always a candidate, so charging or holding is only chosen when it is
    genuinely cheaper over the whole horizon.
    """
    slot_hours = slot_minutes / 60.0
    common = dict(
        soc_kwh=soc_kwh,
        usable_kwh=usable_kwh,
        charge_kw=charge_kw,
        discharge_kw=discharge_kw,
        rte=rte,
        battery_cost_per_kwh=battery_cost_per_kwh,
    )
    baseline = _simulate_cost(
        future, profile, standby_kw, slot_minutes, **common
    )
    best_cost = baseline
    best_idx: list[int] = []
    best_hold: tuple[int, int] | None = None
    best_charge_cost: float | None = None
    best_hold_cost: float | None = None
    if usable_kwh - soc_kwh > 1e-9:
        per_slot = max(1e-6, charge_kw * rte * slot_hours)
        max_slots = min(
            len(future), max(1, math.ceil((usable_kwh - soc_kwh) / per_slot))
        )
        for count in range(1, max_slots + 1):
            chosen = frozenset(_select_indices(indexed, count, cheapest=True))
            cost = _simulate_cost(
                future,
                profile,
                standby_kw,
                slot_minutes,
                charge_idx=chosen,
                **common,
            )
            if best_charge_cost is None or cost < best_charge_cost:
                best_charge_cost = cost
            if cost < best_cost - 1e-9:
                best_cost = cost
                best_idx = sorted(chosen)
                best_hold = None
        if hold_kw > 0:
            for start in _hold_starts(future, now, slot_minutes):
                hold = frozenset(range(0, start))
                cost = _simulate_cost(
                    future,
                    profile,
                    standby_kw,
                    slot_minutes,
                    hold_idx=hold,
                    hold_kw=hold_kw,
                    **common,
                )
                if best_hold_cost is None or cost < best_hold_cost:
                    best_hold_cost = cost
                if cost < best_cost - 1e-9:
                    best_cost = cost
                    best_idx = []
                    best_hold = (0, start - 1)
    return _ChargeChoice(
        baseline=baseline,
        cost=best_cost,
        charge=tuple(best_idx),
        hold=best_hold,
        charge_cost=best_charge_cost,
        hold_cost=best_hold_cost,
    )


def build_plan(
    prices: Sequence[PriceSlot],
    now: dt.datetime,
    *,
    soc: float,
    capacity_kwh: float = DEFAULT_CAPACITY_KWH,
    charge_power_kw: float = DEFAULT_CHARGE_KW,
    discharge_power_kw: float = DEFAULT_DISCHARGE_KW,
    target_soc: float = DEFAULT_TARGET_SOC,
    min_soc: float = DEFAULT_MIN_SOC,
    pv_credit_kwh: float = 0.0,
    standby_kw: float = DEFAULT_STANDBY_KW,
    load_profile: Sequence[float] | None = None,
    export_hours: float | None = DEFAULT_EXPORT_HOURS,
    rte: float = DEFAULT_RTE,
    battery_cost_per_kwh: float = DEFAULT_BATTERY_COST_PER_KWH,
    export_min_spread: float = DEFAULT_EXPORT_MIN_SPREAD,
    max_windows: int = len(WINDOWS),
    charge_windows: int = 2,
    discharge_windows: int = 2,
    slot_minutes: int = SLOT_MINUTES,
    power_percent: int = 100,
    lang: str = DEFAULT_LANGUAGE,
) -> Plan:
    """Plan Time-of-Use windows from prices and the battery state, with reasons.

    ``prices`` is the full list of quarter-hourly slots (today and, when
    available, tomorrow). Only slots at or after ``now`` are considered.

    ``pv_credit_kwh`` is the forecast PV that will be produced; it is reported as
    a discount on the energy the house would otherwise buy, so it explains why
    charging cheaply now is worth it. ``standby_kw`` is the house load the battery
    has to cover in ECO mode; it drains the battery and has to be charged back
    too. When ``load_profile`` is given (a 96-value quarter-hour profile in kW) it
    replaces the flat ``standby_kw``: the drain before the charge window and the
    load during charging are read from the profile, so the plan follows the real
    daily rhythm. ``export_hours`` caps the energy that may be exported at full
    discharge power so a bad solar period cannot drain the battery too early; pass
    ``None`` to disable the cap. ``rte`` is the round-trip efficiency of the
    battery: because it loses energy, a cheap hour is only worth using when
    ``charge_price / rte < later_buy_price``. When the spread does not cover the
    losses the plan leaves the battery alone instead of charging it.

    ``battery_cost_per_kwh`` is added to the price of every purchased kWh, so
    the planner accounts for battery wear when it decides whether charging pays
    off. ``export_min_spread`` is the minimum difference between the export
    price and that battery-cost-adjusted purchase price: the battery only
    exports in hours that clear it. Both are reported in the plan notes (and
    therefore in the activity log) so the calculation is visible.

    The result is sorted by start time, never exceeds ``max_windows`` and carries
    one :class:`PlanReason` per window plus general ``notes``.
    """
    if not prices:
        return Plan(notes=[translate(lang, "plan.no_prices")], lang=lang)

    future = [slot for slot in prices if slot.time >= now]
    if not future:
        return Plan(notes=[translate(lang, "plan.no_future_prices")], lang=lang)

    slot_hours = slot_minutes / 60.0
    charge_power_kw = max(0.0, float(charge_power_kw))
    discharge_power_kw = max(0.0, float(discharge_power_kw))
    discharge_per_slot = max(0.1, discharge_power_kw * slot_hours)

    capacity_kwh = max(0.0, float(capacity_kwh))
    target_soc = min(100.0, max(0.0, float(target_soc)))
    min_soc = min(target_soc, max(0.0, float(min_soc)))
    soc = min(100.0, max(0.0, float(soc)))
    pv_credit_kwh = max(0.0, float(pv_credit_kwh))
    standby_kw = max(0.0, float(standby_kw))
    rte = min(1.0, max(0.1, float(rte)))
    battery_cost_per_kwh = max(0.0, float(battery_cost_per_kwh))
    export_min_spread = max(0.0, float(export_min_spread))

    # The measured quarter-hour house load, when we have one. It replaces the flat
    # standby: the drain before charging and the load during charging come from the
    # profile, so the plan follows the real daily rhythm instead of one number. A
    # zero quarter is a real zero (the recorder had no draw then); a quarter the
    # profile does not cover falls back to the configured standby.
    profile: list[float] | None = (
        [max(0.0, float(value)) for value in load_profile] if load_profile else None
    )

    def _drain_kwh(begin: dt.datetime, finish: dt.datetime) -> float:
        if profile is not None:
            return _profile_load_kwh(
                profile,
                begin,
                finish,
                slot_minutes=slot_minutes,
                fallback_kw=standby_kw,
            )
        return standby_kw * max(0.0, (finish - begin).total_seconds() / 3600.0)

    def _load_kw(begin: dt.datetime, finish: dt.datetime) -> float:
        if profile is not None:
            return _profile_average_kw(
                profile,
                begin,
                finish,
                slot_minutes=slot_minutes,
                fallback_kw=standby_kw,
            )
        return standby_kw

    available_kwh = max(0.0, (soc - min_soc) / 100.0 * capacity_kwh)
    export_cap_kwh: float | None = None
    if export_hours is not None and float(export_hours) > 0:
        export_cap_kwh = discharge_power_kw * float(export_hours)

    budget = max(1, int(max_windows))
    charge_windows = max(0, min(int(charge_windows), budget))
    discharge_windows = max(0, min(int(discharge_windows), budget))
    per_day_discharge = export_cap_kwh is not None

    indexed = list(enumerate(future))

    # -- charge, hold or leave alone: pick the cheapest of the three.
    #
    # The old gate compared the average charge price with the most expensive
    # hour. That ignores the measured load and the way the inverter drains the
    # battery to cover the house when no window is active. With a load forecast
    # we simulate the whole horizon instead: leave the battery alone, charge the
    # cheapest quarter hours, or hold it back for a peak with a small charge
    # window (without one the inverter would self-consume the energy away).
    charge_idx: list[int] = []
    charge_avg = 0.0
    reference_avg = 0.0
    skipped_charge = False
    hold_run: tuple[int, int] | None = None
    choice: _ChargeChoice | None = None
    usable_kwh = max(0.0, (target_soc - min_soc) / 100.0 * capacity_kwh)
    soc_above_min = max(0.0, (soc - min_soc) / 100.0 * capacity_kwh)
    hold_kw = charge_power_kw * DEFAULT_HOLD_WINDOW_PERCENT / 100.0

    simulate = (profile is not None or standby_kw > 0.0) and capacity_kwh > 0
    if simulate and soc < target_soc:
        choice = _choose_charge(
            indexed,
            future,
            profile,
            standby_kw,
            slot_minutes,
            now=now,
            soc_kwh=soc_above_min,
            usable_kwh=usable_kwh,
            charge_kw=charge_power_kw,
            discharge_kw=discharge_power_kw,
            rte=rte,
            battery_cost_per_kwh=battery_cost_per_kwh,
            hold_kw=hold_kw if soc_above_min > 0 else 0.0,
        )
        charge_idx = list(choice.charge)
        hold_run = choice.hold
        if charge_idx:
            charge_avg = sum(future[i].price for i in charge_idx) / len(charge_idx)
    elif soc < target_soc and capacity_kwh > 0:
        # No load forecast: keep the historical price-average gate. It cannot
        # model self-consumption, so it only compares the charge price with the
        # expensive hours, and existing behaviour is preserved.
        n_charge = min(
            len(future),
            math.ceil(
                (target_soc - soc)
                / 100.0
                * capacity_kwh
                / max(0.1, charge_power_kw * rte * slot_hours)
            ),
        )
        for _ in range(16):
            if n_charge <= 0:
                break
            candidate = _select_indices(indexed, n_charge, cheapest=True)
            start_slot = future[min(candidate)].time
            stop_slot = future[max(candidate)].time + dt.timedelta(
                minutes=slot_minutes
            )
            # The battery is drained by the house load between now and the first
            # charge slot, so it has to start lower than the current state of
            # charge would suggest.
            soc_start = min(
                target_soc,
                max(
                    min_soc,
                    soc - _drain_kwh(now, start_slot) / capacity_kwh * 100.0,
                ),
            )
            stored_needed = (target_soc - soc_start) / 100.0 * capacity_kwh
            # The load during the charge window eats into the net charge rate, so
            # the window is sized from the real load there, not the flat standby.
            net_charge_kw = charge_power_kw * rte - _load_kw(start_slot, stop_slot)
            n_new = (
                min(
                    len(future),
                    max(0, math.ceil(stored_needed / net_charge_kw / slot_hours)),
                )
                if net_charge_kw > 0
                else 0
            )
            if n_new == n_charge:
                break
            n_charge = n_new
        if n_charge > 0:
            charge_idx = _select_indices(indexed, n_charge, cheapest=True)
            charge_avg = sum(future[i].price for i in charge_idx) / len(charge_idx)
            used = set(charge_idx)
            rest = [(i, slot) for i, slot in indexed if i not in used]
            reference = _select_indices(rest, len(charge_idx), cheapest=False)
            reference_avg = (
                sum(future[i].price for i in reference) / len(reference)
                if reference
                else charge_avg
            )
            # A kWh from the grid is about 1/RTE more expensive once the losses
            # are counted, and battery wear adds ``battery_cost_per_kwh`` on top.
            # Only charge when the stored kWh still beats the price we would pay
            # later (a later purchase does not wear the battery). Otherwise the
            # battery stays as it is. With no losses and no battery cost,
            # charging is never harmful, so the gate is skipped and the
            # historical behaviour is kept.
            if (rte < 1.0 or battery_cost_per_kwh > 0) and (
                (charge_avg + battery_cost_per_kwh) / rte >= reference_avg
            ):
                charge_idx = []
                skipped_charge = True

    charge_runs = (
        _merge_runs(_group_runs(charge_idx), charge_windows)
        if charge_idx and charge_windows
        else []
    )

    used = set(charge_idx)
    remaining = [(index, slot) for index, slot in indexed if index not in used]

    # Exporting only pays off when the expensive hour clears the price the energy
    # cost to buy, plus the battery wear, by at least the required spread. When
    # nothing was charged, the cheapest remaining slot is the opportunity cost of
    # the stored energy (what a replacement kWh would cost).
    if charge_idx:
        reference_buy = charge_avg + battery_cost_per_kwh
    else:
        reference_buy = (
            min((slot.price for _, slot in remaining), default=0.0)
            + battery_cost_per_kwh
        )
    export_floor = (
        reference_buy + export_min_spread if export_min_spread > 0 else None
    )
    candidates = remaining
    export_skipped = False
    if export_floor is not None:
        # The tolerance keeps an exact threshold (e.g. 0.30) from being lost to
        # floating-point rounding.
        candidates = [
            (index, slot)
            for index, slot in remaining
            if slot.price + 1e-9 >= export_floor
        ]
        export_skipped = available_kwh > 0 and bool(remaining) and not candidates
    if hold_run is not None:
        # The energy is being held for the peak at the end of the hold, so do
        # not export it on an earlier peak that the hold window covers.
        candidates = [
            (index, slot) for index, slot in candidates if index > hold_run[1]
        ]

    if export_cap_kwh is not None:
        # One capped, contiguous window per day so the export budget is spread
        # over the days instead of being spent on a single peak.
        discharge_runs = (
            _discharge_runs_per_day(
                candidates,
                discharge_per_slot=discharge_per_slot,
                total_energy_kwh=available_kwh,
                per_day_energy_kwh=export_cap_kwh,
                max_runs=discharge_windows,
            )
            if discharge_windows
            else []
        )
    else:
        count = (
            min(len(candidates), math.ceil(available_kwh / discharge_per_slot))
            if available_kwh > 0
            else 0
        )
        discharge_idx = _select_indices(candidates, count, cheapest=False)
        discharge_runs = (
            _merge_runs(_group_runs(discharge_idx), discharge_windows)
            if discharge_idx and discharge_windows
            else []
        )

    # -- reasoning ---------------------------------------------------------
    notes: list[str] = []
    if skipped_charge:
        notes.append(
            translate(
                lang,
                "plan.skipped_charge",
                charge=charge_avg,
                rte=rte * 100,
                reference=reference_avg,
            )
        )
    elif soc >= target_soc:
        notes.append(translate(lang, "plan.already_target", soc=soc))
    elif choice is not None:
        if hold_run is not None:
            hold_stop = future[hold_run[1]].time + dt.timedelta(
                minutes=slot_minutes
            )
            notes.append(
                translate(
                    lang,
                    "plan.hold_worth_it",
                    stop=f"{hold_stop:%H:%M}",
                    cost=choice.cost,
                    baseline=choice.baseline,
                    saving=choice.baseline - choice.cost,
                )
            )
        elif charge_idx:
            charged_kwh = len(charge_idx) * charge_power_kw * slot_hours
            charge_stop = future[max(charge_idx)].time + dt.timedelta(
                minutes=slot_minutes
            )
            notes.append(
                translate(
                    lang,
                    "plan.charge_note",
                    soc=soc,
                    target=target_soc,
                    standby=_load_kw(now, charge_stop),
                    rte=rte * 100,
                    kwh=charged_kwh,
                )
            )
            notes.append(
                translate(
                    lang,
                    "plan.charge_worth_it",
                    cost=choice.cost,
                    baseline=choice.baseline,
                    saving=choice.baseline - choice.cost,
                )
            )
        else:
            best_charge = (
                choice.charge_cost
                if choice.charge_cost is not None
                else choice.baseline
            )
            notes.append(
                translate(
                    lang,
                    "plan.charge_not_worth_it",
                    cost=best_charge,
                    baseline=choice.baseline,
                )
            )
    elif charge_idx:
        charged_kwh = len(charge_idx) * charge_power_kw * slot_hours
        charge_stop = future[max(charge_idx)].time + dt.timedelta(minutes=slot_minutes)
        notes.append(
            translate(
                lang,
                "plan.charge_note",
                soc=soc,
                target=target_soc,
                standby=_load_kw(now, charge_stop),
                rte=rte * 100,
                kwh=charged_kwh,
            )
        )
    else:
        notes.append(
            translate(lang, "plan.no_cheap_hours", soc=soc, target=target_soc)
        )
    if pv_credit_kwh > 0:
        notes.append(translate(lang, "plan.pv_note", pv=pv_credit_kwh))
    if export_cap_kwh is not None and available_kwh > export_cap_kwh:
        notes.append(
            translate(
                lang,
                "plan.export_cap_note",
                cap=export_cap_kwh,
                hours=float(export_hours),
                avail=available_kwh,
                min_soc=min_soc,
            )
        )
    elif available_kwh > 0:
        notes.append(
            translate(
                lang, "plan.available_note", avail=available_kwh, min_soc=min_soc
            )
        )
    # Make the pricing calculation visible. These notes travel with the plan to
    # the planner card and into the activity log, so the user can see why the
    # battery did (or did not) export.
    if battery_cost_per_kwh > 0 or export_min_spread > 0:
        notes.append(
            translate(
                lang,
                "plan.battery_cost_note",
                cost=battery_cost_per_kwh,
                spread=export_min_spread,
            )
        )
    if export_floor is not None:
        if export_skipped:
            best_available = max(
                (slot.price for _, slot in remaining), default=0.0
            )
            notes.append(
                translate(
                    lang,
                    "plan.export_skipped",
                    best=best_available,
                    actual=best_available - reference_buy,
                    buy=reference_buy,
                    spread=export_min_spread,
                )
            )
        elif discharge_runs:
            best_export = max(
                (
                    future[index].price
                    for run in discharge_runs
                    for index in range(run[0], run[1] + 1)
                ),
                default=0.0,
            )
            notes.append(
                translate(
                    lang,
                    "plan.export_spread_note",
                    price=best_export,
                    actual=best_export - reference_buy,
                    buy=reference_buy,
                    spread=export_min_spread,
                )
            )
    if profile is not None:
        daily = _profile_daily_kwh(profile, slot_minutes=slot_minutes)
        notes.append(
            translate(
                lang,
                "plan.profile_note",
                load=daily / 24.0,
                kwh=daily,
            )
        )
    elif standby_kw > 0:
        notes.append(translate(lang, "plan.standby_note", standby=standby_kw))

    planned: list[tuple[str, tuple[int, int], float, int]] = []
    for run in charge_runs:
        planned.append(("charge", run, charge_power_kw, power_percent))
    if hold_run is not None:
        planned.append(
            ("hold", hold_run, hold_kw, DEFAULT_HOLD_WINDOW_PERCENT)
        )
    for run in discharge_runs:
        planned.append(("discharge", run, discharge_power_kw, power_percent))
    planned.sort(key=lambda item: item[1][0])

    windows: list[TouWindow] = []
    reasons: list[PlanReason] = []
    for mode, run, power_kw, percent in planned:
        stats = _run_stats(run, future, slot_minutes, power_kw)
        # A hold is a physical charge window at a trickle; only the explanation
        # and the per-day overview call it "hold".
        physical_mode = "charge" if mode == "hold" else mode
        first_number = len(windows) + 1
        for window in _run_to_windows(
            run, future, slot_minutes, physical_mode, percent
        ):
            windows.append(window)
        if len(windows) < first_number:
            continue
        if mode == "hold":
            peak = max(
                (slot.price for slot in future[run[1] + 1 :]), default=0.0
            )
            run_stop = future[run[1]].time + dt.timedelta(minutes=slot_minutes)
            text = translate(
                lang,
                "plan.hold_reason",
                start=f"{future[run[0]].time:%H:%M}",
                stop=f"{run_stop:%H:%M}",
                percent=percent,
                kwh=soc_above_min,
                peak=peak,
            )
        elif mode == "charge":
            text = translate(
                lang,
                "plan.charge_reason",
                minutes=stats["minutes"],
                avg=stats["avg_price"],
                power=power_kw,
                kwh=stats["energy_kwh"] * rte,
                rte=rte * 100,
            )
        else:
            revenue = stats["energy_kwh"] * stats["avg_price"]
            when = (
                weekday_name(lang, future[run[0]].time.weekday())
                if per_day_discharge
                else translate(lang, "plan.coming_period")
            )
            text = translate(
                lang,
                "plan.discharge_reason",
                minutes=stats["minutes"],
                when=when,
                avg=stats["avg_price"],
                power=power_kw,
                kwh=stats["energy_kwh"],
                revenue=revenue,
            )
        # A run that crosses midnight becomes one window per day; explain each
        # of them so the reason list stays aligned with the windows.
        for number in range(first_number, len(windows) + 1):
            reasons.append(PlanReason(window=number, mode=mode, text=text))

    if not windows:
        # Do not say "no windows" as if the planner gave up: name the real
        # reason, which is either a full battery or ECO self-consumption.
        notes.append(
            translate(
                lang,
                "plan.no_windows_target"
                if soc >= target_soc
                else "plan.no_windows_selfuse",
            )
        )

    # Lead with a per-day overview so the plan as a whole names which day each
    # window runs on ("vandaag ... en morgen ..."), not just each window's own reason.
    overview = _plan_overview(planned, future, slot_minutes, now.date(), lang)
    if overview:
        notes.insert(0, overview)

    windows = windows[:budget]
    reasons = [reason for reason in reasons if reason.window <= budget]
    return Plan(
        windows=windows,
        reasons=reasons,
        notes=notes,
        lang=lang,
        export_min_spread=export_min_spread,
    )


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
    """Backwards-compatible wrapper around :func:`build_plan`.

    Uses one combined power for charging and discharging and no export cap, so
    the historical behaviour (and its tests) is unchanged.
    """
    plan = build_plan(
        prices,
        now,
        soc=soc,
        capacity_kwh=capacity_kwh,
        charge_power_kw=power_kw,
        discharge_power_kw=power_kw,
        target_soc=target_soc,
        min_soc=min_soc,
        pv_credit_kwh=pv_credit_kwh,
        standby_kw=0.0,
        export_hours=None,
        rte=1.0,
        battery_cost_per_kwh=0.0,
        export_min_spread=0.0,
        max_windows=max_windows,
        charge_windows=charge_windows,
        discharge_windows=discharge_windows,
        slot_minutes=slot_minutes,
        power_percent=power_percent,
    )
    return plan.windows


def windows_cost(
    windows: Sequence[TouWindow],
    prices: Sequence[PriceSlot],
    now: dt.datetime,
    *,
    charge_power_kw: float = DEFAULT_CHARGE_KW,
    discharge_power_kw: float = DEFAULT_DISCHARGE_KW,
    slot_minutes: int = SLOT_MINUTES,
    battery_cost_per_kwh: float = DEFAULT_BATTERY_COST_PER_KWH,
) -> float:
    """Net grid cost of a set of windows over the slots from ``now`` on.

    Charging costs money (positive), discharging earns it (negative). The
    purchase price includes ``battery_cost_per_kwh`` so the estimate accounts for
    battery wear, exactly as the planner does. Used to decide whether a
    re-planned schedule is actually cheaper than the one that is on the inverter
    right now.
    """
    slot_hours = slot_minutes / 60.0
    total = 0.0
    for slot in prices:
        if slot.time < now:
            continue
        moment = slot.time.time()
        weekday_bit = day_bit(slot.time)
        for window in windows:
            if not window.enabled or not (window.mask & weekday_bit):
                continue
            if not (window.start <= moment < window.stop):
                continue
            if window.mode == "charge":
                total += (
                    (slot.price + max(0.0, float(battery_cost_per_kwh)))
                    * max(0.0, float(charge_power_kw))
                    * max(0.0, window.power) / 100.0
                    * slot_hours
                )
            elif window.mode == "discharge":
                total -= (
                    slot.price
                    * max(0.0, float(discharge_power_kw))
                    * max(0.0, window.power) / 100.0
                    * slot_hours
                )
            break
    return total


def forecast_soc(
    windows: Sequence[TouWindow],
    start: dt.datetime,
    end: dt.datetime,
    *,
    soc: float,
    capacity_kwh: float = DEFAULT_CAPACITY_KWH,
    power_kw: float = DEFAULT_POWER_KW,
    charge_power_kw: float | None = None,
    discharge_power_kw: float | None = None,
    base_load_kw: float = 0.0,
    load_profile: Sequence[float] | None = None,
    step_minutes: int = SLOT_MINUTES,
    min_soc: float = 0.0,
    max_soc: float = 100.0,
    rte: float = DEFAULT_RTE,
) -> list[tuple[dt.datetime, float]]:
    """Project the battery state of charge if ``windows`` are followed.

    Walks from ``start`` to ``end`` in ``step_minutes`` steps. For every step the
    energy each active window moves is added: a charge window raises the state of
    charge, a discharge window lowers it (the power percentage is a fraction of
    the matching power). ``rte`` is the round-trip efficiency, applied when
    charging, so only ``rte`` of the energy taken from the grid lands in the
    battery. ``base_load_kw`` is the house's standby consumption, which the
    battery also covers, so it lowers the state of charge in every step. The
    result is clamped to ``[min_soc, max_soc]``. Returns ``(moment, soc)`` pairs,
    including the value at ``start``.

    Pure (no Home Assistant imports), so the card's forecast is easy to test.
    Windows are day-local, so a window never crosses midnight.
    """
    capacity_kwh = max(0.1, float(capacity_kwh))
    power_kw = max(0.0, float(power_kw))
    charge_kw = power_kw if charge_power_kw is None else max(0.0, float(charge_power_kw))
    discharge_kw = (
        power_kw if discharge_power_kw is None else max(0.0, float(discharge_power_kw))
    )
    base_load_kw = max(0.0, float(base_load_kw))
    profile: list[float] | None = (
        [max(0.0, float(value)) for value in load_profile] if load_profile else None
    )
    rte = min(1.0, max(0.1, float(rte)))
    step = dt.timedelta(minutes=max(1, int(step_minutes)))

    current = min(max_soc, max(min_soc, float(soc)))
    points: list[tuple[dt.datetime, float]] = [(start, current)]

    moment = start
    while moment < end:
        boundary = min(moment + step, end)
        hours = (boundary - moment).total_seconds() / 3600.0
        # The house load drains the battery every step. With a measured profile
        # the load follows the time of day; without one the flat standby is used.
        load_kw = (
            _profile_average_kw(
                profile,
                moment,
                boundary,
                slot_minutes=step_minutes,
                fallback_kw=base_load_kw,
            )
            if profile is not None
            else base_load_kw
        )
        delta = -load_kw * hours / capacity_kwh * 100.0
        day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        weekday_bit = day_bit(day)
        for window in windows:
            if not window.enabled or not (window.mask & weekday_bit):
                continue
            w_start = day.replace(hour=window.start.hour, minute=window.start.minute)
            w_stop = day.replace(hour=window.stop.hour, minute=window.stop.minute)
            overlap_start = max(moment, w_start)
            overlap_stop = min(boundary, w_stop)
            if overlap_stop <= overlap_start:
                continue
            overlap_hours = (overlap_stop - overlap_start).total_seconds() / 3600.0
            if window.mode == "charge":
                energy_kwh = window.power / 100.0 * charge_kw * rte * overlap_hours
            elif window.mode == "discharge":
                energy_kwh = -window.power / 100.0 * discharge_kw * overlap_hours
            else:
                energy_kwh = 0.0
            delta += energy_kwh / capacity_kwh * 100.0
        current = min(max_soc, max(min_soc, current + delta))
        moment = boundary
        points.append((moment, current))

    return points


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

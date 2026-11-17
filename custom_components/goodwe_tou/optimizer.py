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
    DEFAULT_CAPACITY_KWH,
    DEFAULT_CHARGE_KW,
    DEFAULT_DISCHARGE_KW,
    DEFAULT_EXPORT_HOURS,
    DEFAULT_MIN_SOC,
    DEFAULT_POWER_KW,
    DEFAULT_RTE,
    DEFAULT_STANDBY_KW,
    DEFAULT_TARGET_SOC,
    SLOT_MINUTES,
    WINDOWS,
)
from .registers import TouWindow, mask_to_days

_NL_WEEKDAYS = (
    "maandag",
    "dinsdag",
    "woensdag",
    "donderdag",
    "vrijdag",
    "zaterdag",
    "zondag",
)


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

    @property
    def summary(self) -> str:
        """A one-line summary suitable for the activity log."""
        return " ".join(self.notes) if self.notes else "Geen vensters nodig."

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
    export_hours: float | None = DEFAULT_EXPORT_HOURS,
    rte: float = DEFAULT_RTE,
    max_windows: int = len(WINDOWS),
    charge_windows: int = 2,
    discharge_windows: int = 2,
    slot_minutes: int = SLOT_MINUTES,
    power_percent: int = 100,
) -> Plan:
    """Plan Time-of-Use windows from prices and the battery state, with reasons.

    ``prices`` is the full list of quarter-hourly slots (today and, when
    available, tomorrow). Only slots at or after ``now`` are considered.

    ``pv_credit_kwh`` is the forecast PV that will be produced; it is reported as
    a discount on the energy the house would otherwise buy, so it explains why
    charging cheaply now is worth it. ``standby_kw`` is the house load the battery
    has to cover in ECO mode; it drains the battery and has to be charged back
    too. ``export_hours`` caps the energy that may be exported at full discharge
    power so a bad solar period cannot drain the battery too early; pass ``None``
    to disable the cap. ``rte`` is the round-trip efficiency of the battery:
    because it loses energy, a cheap hour is only worth using when
    ``charge_price / rte < later_buy_price``. When the spread does not cover the
    losses the plan leaves the battery alone instead of charging it.

    The result is sorted by start time, never exceeds ``max_windows`` and carries
    one :class:`PlanReason` per window plus general ``notes``.
    """
    if not prices:
        return Plan(notes=["Geen prijzen beschikbaar."])

    future = [slot for slot in prices if slot.time >= now]
    if not future:
        return Plan(notes=["Geen toekomstige prijzen beschikbaar."])

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

    available_kwh = max(0.0, (soc - min_soc) / 100.0 * capacity_kwh)
    export_cap_kwh: float | None = None
    if export_hours is not None and float(export_hours) > 0:
        export_cap_kwh = discharge_power_kw * float(export_hours)

    budget = max(1, int(max_windows))
    charge_windows = max(0, min(int(charge_windows), budget))
    discharge_windows = max(0, min(int(discharge_windows), budget))
    per_day_discharge = export_cap_kwh is not None

    indexed = list(enumerate(future))

    # -- charge: the cheapest quarter hours, sized so the battery really reaches
    # the target. The drain between now and the charge window, the drain during
    # charging and the round-trip losses are charged back too, so a battery that
    # is low in the early morning is filled all the way up before the day starts.
    charge_idx: list[int] = []
    charge_avg = 0.0
    reference_avg = 0.0
    skipped_charge = False
    net_charge_kw = charge_power_kw * rte - standby_kw
    if soc < target_soc and net_charge_kw > 0 and capacity_kwh > 0:
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
            pre_hours = max(0.0, (start_slot - now).total_seconds() / 3600.0)
            soc_start = min(
                target_soc,
                max(min_soc, soc - standby_kw * pre_hours / capacity_kwh * 100.0),
            )
            stored_needed = (target_soc - soc_start) / 100.0 * capacity_kwh
            n_new = min(
                len(future),
                max(0, math.ceil(stored_needed / net_charge_kw / slot_hours)),
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
            # are counted, so only charge when the cheap hours still beat the
            # price we would pay later. Otherwise the battery stays as it is.
            # With no losses (RTE 100%) charging is never harmful, so the gate
            # is skipped and the historical behaviour is kept.
            if rte < 1.0 and charge_avg >= rte * reference_avg:
                charge_idx = []
                skipped_charge = True

    charge_runs = (
        _merge_runs(_group_runs(charge_idx), charge_windows)
        if charge_idx and charge_windows
        else []
    )

    used = set(charge_idx)
    remaining = [(index, slot) for index, slot in indexed if index not in used]
    if export_cap_kwh is not None:
        # One capped, contiguous window per day so the export budget is spread
        # over the days instead of being spent on a single peak.
        discharge_runs = (
            _discharge_runs_per_day(
                remaining,
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
            min(len(remaining), math.ceil(available_kwh / discharge_per_slot))
            if available_kwh > 0
            else 0
        )
        discharge_idx = _select_indices(remaining, count, cheapest=False)
        discharge_runs = (
            _merge_runs(_group_runs(discharge_idx), discharge_windows)
            if discharge_idx and discharge_windows
            else []
        )

    # -- reasoning ---------------------------------------------------------
    notes: list[str] = []
    if skipped_charge:
        notes.append(
            f"Overnacht laden overgeslagen: de goedkoopste uren (gemiddeld "
            f"\u20ac{charge_avg:.3f}/kWh) zijn, met de laad-/ontlaadverliezen "
            f"(RTE {rte * 100:.0f}%), niet goedkoper dan het duurste uur "
            f"(gemiddeld \u20ac{reference_avg:.3f}/kWh)."
        )
    elif soc >= target_soc:
        notes.append(f"Batterij staat al op het doel ({soc:.0f}%).")
    elif charge_idx:
        charged_kwh = len(charge_idx) * charge_power_kw * slot_hours
        notes.append(
            f"Batterij {soc:.0f}% \u2192 doel {target_soc:.0f}%. Met het verbruik "
            f"van {standby_kw:.1f} kW tot en tijdens de laaduren en de "
            f"laadverliezen (RTE {rte * 100:.0f}%) is ongeveer "
            f"{charged_kwh:.1f} kWh nodig."
        )
    else:
        notes.append(
            f"Batterij {soc:.0f}%; er zijn nu geen goedkope uren om naar het doel "
            f"{target_soc:.0f}% te laden."
        )
    if pv_credit_kwh > 0:
        notes.append(
            f"PV-prognose levert nog {pv_credit_kwh:.1f} kWh voor niets. Dat verlaagt "
            "je inkoop overdag, dus laden we de batterij 's nachts goedkoop vol in "
            "plaats van later duur in te kopen."
        )
    if export_cap_kwh is not None and available_kwh > export_cap_kwh:
        notes.append(
            f"Ontladen is per dag beperkt tot {export_cap_kwh:.1f} kWh (max "
            f"{float(export_hours):.1f} uur terugleveren op vol vermogen per dag) "
            f"zodat de batterij niet te vroeg leegloopt; er is {available_kwh:.1f} kWh "
            f"beschikbaar boven {min_soc:.0f}%."
        )
    elif available_kwh > 0:
        notes.append(
            f"Er is {available_kwh:.1f} kWh beschikbaar om te ontladen boven "
            f"{min_soc:.0f}%."
        )
    if standby_kw and float(standby_kw) > 0:
        notes.append(
            f"Het huis gebruikt ongeveer {float(standby_kw):.1f} kW standby; dat "
            "neemt de batterij in ECO-modus zelf voor haar rekening."
        )

    planned: list[tuple[str, tuple[int, int], float]] = []
    for run in charge_runs:
        planned.append(("charge", run, charge_power_kw))
    for run in discharge_runs:
        planned.append(("discharge", run, discharge_power_kw))
    planned.sort(key=lambda item: item[1][0])

    windows: list[TouWindow] = []
    reasons: list[PlanReason] = []
    for mode, run, power_kw in planned:
        stats = _run_stats(run, future, slot_minutes, power_kw)
        for window in _run_to_windows(run, future, slot_minutes, mode, power_percent):
            windows.append(window)
        number = len(windows)
        if mode == "charge":
            reasons.append(
                PlanReason(
                    window=number,
                    mode=mode,
                    text=(
                        f"Goedkoopste {stats['minutes']:.0f} min van de komende periode "
                        f"(gemiddeld \u20ac{stats['avg_price']:.3f}/kWh). Met "
                        f"{power_kw:.0f} kW laad je hier "
                        f"{stats['energy_kwh'] * rte:.1f} kWh in de batterij "
                        f"(RTE {rte * 100:.0f}%)."
                    ),
                )
            )
        else:
            revenue = stats["energy_kwh"] * stats["avg_price"]
            when = (
                _NL_WEEKDAYS[future[run[0]].time.weekday()]
                if per_day_discharge
                else "de komende periode"
            )
            reasons.append(
                PlanReason(
                    window=number,
                    mode=mode,
                    text=(
                        f"Duurste {stats['minutes']:.0f} min van {when} "
                        f"(gemiddeld \u20ac{stats['avg_price']:.3f}/kWh). Met "
                        f"{power_kw:.0f} kW ontlaad je hier {stats['energy_kwh']:.1f} kWh; "
                        f"dat levert ongeveer \u20ac{revenue:.2f} op."
                    ),
                )
            )

    if not windows:
        notes.append("Geen vensters nodig: de batterij is op doel en er valt niets te ontladen.")

    windows = windows[:budget]
    reasons = [reason for reason in reasons if reason.window <= budget]
    return Plan(windows=windows, reasons=reasons, notes=notes)


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
) -> float:
    """Net grid cost of a set of windows over the slots from ``now`` on.

    Charging costs money (positive), discharging earns it (negative). Used to
    decide whether a re-planned schedule is actually cheaper than the one that is
    on the inverter right now.
    """
    slot_hours = slot_minutes / 60.0
    total = 0.0
    for slot in prices:
        if slot.time < now:
            continue
        moment = slot.time.time()
        weekday_bit = 1 << slot.time.weekday()
        for window in windows:
            if not window.enabled or not (window.mask & weekday_bit):
                continue
            if not (window.start <= moment < window.stop):
                continue
            if window.mode == "charge":
                total += slot.price * max(0.0, float(charge_power_kw)) * slot_hours
            elif window.mode == "discharge":
                total -= slot.price * max(0.0, float(discharge_power_kw)) * slot_hours
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
    rte = min(1.0, max(0.1, float(rte)))
    step = dt.timedelta(minutes=max(1, int(step_minutes)))

    current = min(max_soc, max(min_soc, float(soc)))
    points: list[tuple[dt.datetime, float]] = [(start, current)]

    moment = start
    while moment < end:
        boundary = min(moment + step, end)
        hours = (boundary - moment).total_seconds() / 3600.0
        delta = -base_load_kw * hours / capacity_kwh * 100.0
        day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        weekday_bit = 1 << day.weekday()
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

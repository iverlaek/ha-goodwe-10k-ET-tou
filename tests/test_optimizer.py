"""Unit tests for the cheapest-hours planner (pure logic)."""

from __future__ import annotations

import datetime as dt

import pytest

from conftest import load

optimizer = load("optimizer")


def _slots(start: dt.datetime, count: int, step_minutes: int = 15):
    return [
        optimizer.PriceSlot(start + dt.timedelta(minutes=step_minutes * i), i / 100.0)
        for i in range(count)
    ]


def test_slots_from_prices_parses_and_skips_bad_items():
    slots = optimizer.slots_from_prices(
        [
            {"time": "2026-11-16T00:00:00+01:00", "price": 0.12},
            {"time": "2026-11-16T00:15:00+01:00", "price": "0.10"},
            {"time": "not-a-date", "price": 0.1},
            {"time": "2026-11-16T00:30:00+01:00"},
            {},
        ]
    )
    assert len(slots) == 2
    assert slots[0].price == 0.12
    assert slots[1].price == 0.10


def test_slots_from_prices_accepts_nordpool_shape():
    slots = optimizer.slots_from_prices(
        [
            {
                "start": "2026-11-16T00:00:00+01:00",
                "end": "2026-11-16T00:15:00+01:00",
                "value": 0.176,
            },
            {
                "start": "2026-11-16T00:15:00+01:00",
                "end": "2026-11-16T00:30:00+01:00",
                "value": 0.167,
            },
            0.15,  # bare float (Nordpool's ``today`` list) -> skipped
        ]
    )
    assert len(slots) == 2
    assert slots[0].price == 0.176
    assert slots[1].time.minute == 15


def test_plan_charges_cheap_and_discharges_expensive():
    start = dt.datetime(2026, 11, 16, 0, 0)
    slots = _slots(start, 96)
    windows = optimizer.plan_windows(
        slots,
        start,
        soc=50,
        capacity_kwh=24,
        power_kw=7,
        target_soc=100,
        min_soc=20,
    )
    assert windows, "expected a plan"
    modes = [w.mode for w in windows]
    assert "charge" in modes
    assert "discharge" in modes

    charge = [w for w in windows if w.mode == "charge"]
    discharge = [w for w in windows if w.mode == "discharge"]
    # the cheapest slots are first, the most expensive last
    assert min(w.start for w in charge) <= dt.time(2, 0)
    assert max(w.stop for w in discharge) >= dt.time(22, 0)

    # every window is day-local
    for window in windows:
        assert window.start < window.stop


def test_pv_credit_is_a_discount_not_a_reduction():
    """PV is credited against the energy the house buys, not the charge target.

    The battery must be full in the evening, when the sun is gone, so the plan
    charges to the target either way; the PV forecast only shows up as a note.
    """
    start = dt.datetime(2026, 11, 16, 0, 0)
    slots = _slots(start, 96)
    without = optimizer.plan_windows(
        slots, start, soc=20, capacity_kwh=24, power_kw=7, pv_credit_kwh=0
    )
    with_pv = optimizer.plan_windows(
        slots, start, soc=20, capacity_kwh=24, power_kw=7, pv_credit_kwh=10
    )

    def charge_minutes(plan):
        return sum(
            (w.stop.hour * 60 + w.stop.minute) - (w.start.hour * 60 + w.start.minute)
            for w in plan
            if w.mode == "charge"
        )

    assert charge_minutes(with_pv) == charge_minutes(without)

    plan = optimizer.build_plan(
        slots, start, soc=20, capacity_kwh=24, pv_credit_kwh=10, standby_kw=0
    )
    assert any("PV-prognose" in note for note in plan.notes)


def test_full_battery_only_discharges():
    start = dt.datetime(2026, 11, 16, 0, 0)
    slots = _slots(start, 96)
    windows = optimizer.plan_windows(
        slots, start, soc=100, capacity_kwh=24, power_kw=7, target_soc=100, min_soc=20
    )
    assert windows
    assert all(w.mode == "discharge" for w in windows)


def test_empty_prices_returns_nothing():
    assert optimizer.plan_windows([], dt.datetime(2026, 11, 16), soc=50) == []


def test_plan_registers_pads_to_four():
    start = dt.datetime(2026, 11, 16, 0, 0)
    slots = _slots(start, 96)
    windows = optimizer.plan_windows(slots, start, soc=50, capacity_kwh=24, power_kw=7)
    padded = optimizer.plan_registers(windows)
    assert len(padded) == 4
    assert padded[0].mode != "off"
    for window in padded[len(windows) :]:
        assert window.mode == "off"
        assert window.days == "none"


def test_forecast_soc_follows_windows():
    day = dt.datetime(2026, 11, 16, 0, 0)  # a Monday
    windows = [
        optimizer.TouWindow(
            start=dt.time(2, 0), stop=dt.time(4, 0), mode="charge", power=100, days="mon"
        ),
        optimizer.TouWindow(
            start=dt.time(18, 0),
            stop=dt.time(20, 0),
            mode="discharge",
            power=100,
            days="mon",
        ),
    ]
    points = optimizer.forecast_soc(
        windows, day, day + dt.timedelta(days=1), soc=50, capacity_kwh=10, power_kw=5
    )
    values = dict(points)
    assert values[day] == 50
    assert values[dt.datetime(2026, 11, 16, 2, 0)] == 50
    assert values[dt.datetime(2026, 11, 16, 4, 0)] == 100  # charged to full
    assert values[dt.datetime(2026, 11, 16, 18, 0)] == 100
    assert values[dt.datetime(2026, 11, 16, 20, 0)] == 0  # discharged empty


def test_forecast_soc_respects_days():
    day = dt.datetime(2026, 11, 16, 0, 0)  # a Monday
    window = optimizer.TouWindow(
        start=dt.time(0, 0),
        stop=dt.time(23, 59),
        mode="discharge",
        power=100,
        days="tue",
    )
    points = optimizer.forecast_soc(
        [window], day, day + dt.timedelta(days=1), soc=80, capacity_kwh=10, power_kw=5
    )
    assert all(value == 80 for _, value in points)


def test_forecast_soc_clamps_to_min_soc():
    day = dt.datetime(2026, 11, 16, 0, 0)
    window = optimizer.TouWindow(
        start=dt.time(1, 0), stop=dt.time(5, 0), mode="discharge", power=100, days="mon"
    )
    points = optimizer.forecast_soc(
        [window],
        day,
        day + dt.timedelta(hours=6),
        soc=30,
        capacity_kwh=10,
        power_kw=5,
        min_soc=20,
    )
    assert min(value for _, value in points) == 20


def test_forecast_soc_base_load_drains_the_battery():
    day = dt.datetime(2026, 11, 16, 0, 0)  # a Monday
    points = optimizer.forecast_soc(
        [],
        day,
        day + dt.timedelta(hours=2),
        soc=100,
        capacity_kwh=10,
        base_load_kw=1.0,
    )
    values = dict(points)
    # 1 kW for 2 hours on a 10 kWh battery = 20 percentage points.
    assert values[day + dt.timedelta(hours=2)] == 80


def test_build_plan_explains_every_window():
    start = dt.datetime(2026, 11, 16, 0, 0)
    slots = _slots(start, 96)
    plan = optimizer.build_plan(
        slots,
        start,
        soc=40,
        capacity_kwh=24,
        charge_power_kw=6,
        discharge_power_kw=7,
        target_soc=100,
        min_soc=20,
        export_hours=1.0,
    )
    assert plan.windows
    # One reason per planned window, in the same order as the windows.
    assert len(plan.reasons) == len(plan.windows)
    for reason in plan.reasons:
        assert reason.window >= 1
        assert reason.mode in ("charge", "discharge")
        assert reason.text
    # Charging and discharging both carry a human readable explanation.
    modes = {reason.mode for reason in plan.reasons}
    assert "charge" in modes
    assert "discharge" in modes
    assert plan.summary


def test_build_plan_export_cap_limits_discharge():
    start = dt.datetime(2026, 11, 16, 0, 0)
    slots = _slots(start, 96)
    capped = optimizer.build_plan(
        slots,
        start,
        soc=100,
        capacity_kwh=24,
        discharge_power_kw=7,
        min_soc=20,
        export_hours=1.0,
    )
    uncapped = optimizer.build_plan(
        slots,
        start,
        soc=100,
        capacity_kwh=24,
        discharge_power_kw=7,
        min_soc=20,
        export_hours=None,
    )

    def discharge_minutes(plan):
        return sum(
            (w.stop.hour * 60 + w.stop.minute) - (w.start.hour * 60 + w.start.minute)
            for w in plan.windows
            if w.mode == "discharge"
        )

    assert discharge_minutes(capped) <= 60
    assert discharge_minutes(capped) < discharge_minutes(uncapped)


def test_build_plan_discharges_on_today_and_tomorrow():
    """The export cap is per day, so the rest of today is planned as well.

    With a single shared budget the four most expensive quarter hours all fall on
    the expensive day, leaving today grey in the planner. A per-day budget gives
    both days their own discharge window.
    """
    start = dt.datetime(2026, 11, 16, 0, 0)  # a Monday
    prices = [0.05] * 96 + [0.30] * 96  # today cheap, tomorrow expensive
    slots = _slots_from_prices(start, prices)
    now = start + dt.timedelta(hours=12)
    plan = optimizer.build_plan(
        slots,
        now,
        soc=100,
        capacity_kwh=24,
        discharge_power_kw=6,
        min_soc=20,
        export_hours=1.0,
    )
    discharge_days = {w.days for w in plan.windows if w.mode == "discharge"}
    assert discharge_days == {"mon", "tue"}
    # Each day is capped at the one-hour export limit.
    for day in ("mon", "tue"):
        minutes = sum(
            (w.stop.hour * 60 + w.stop.minute) - (w.start.hour * 60 + w.start.minute)
            for w in plan.windows
            if w.mode == "discharge" and w.days == day
        )
        assert minutes <= 60
    # The reason names the day it discharges on.
    assert any("dinsdag" in reason.text for reason in plan.reasons)


def test_discharge_runs_do_not_fill_the_gap_between_peaks():
    """Two peaks with a cheap gap must stay one capped block, not be merged.

    Merging the two runs would fill the cheap hours in between and stretch the
    window past the one-hour export cap (the released 0.7.1 produced a 90-minute,
    9 kWh window this way).
    """
    start = dt.datetime(2026, 11, 16, 0, 0)  # a Monday
    prices = [0.10] * 96
    for i in range(20, 24):  # peak A: 05:00-06:00
        prices[i] = 0.40
    for i in range(28, 32):  # peak B: 07:00-08:00, a cheap hour in between
        prices[i] = 0.40
    slots = _slots_from_prices(start, prices)

    runs = optimizer._discharge_runs_per_day(
        list(enumerate(slots)),
        discharge_per_slot=1.5,
        total_energy_kwh=6.0,
        per_day_energy_kwh=6.0,
        max_runs=2,
    )

    assert len(runs) == 1
    first, last = runs[0]
    assert last - first + 1 == 4  # 60 minutes at the cap, not 12 slots


def _slots_from_prices(start: dt.datetime, prices, step_minutes: int = 15):
    return [
        optimizer.PriceSlot(start + dt.timedelta(minutes=step_minutes * i), price)
        for i, price in enumerate(prices)
    ]


def test_charging_is_skipped_when_the_spread_does_not_cover_the_losses():
    """With flat prices the round-trip losses make charging a losing deal."""
    start = dt.datetime(2026, 11, 16, 0, 0)
    slots = _slots_from_prices(start, [0.20] * 96)
    plan = optimizer.build_plan(
        slots,
        start,
        soc=40,
        capacity_kwh=24,
        charge_power_kw=6,
        discharge_power_kw=6,
        target_soc=100,
        min_soc=20,
        standby_kw=0.0,
        rte=0.85,
    )
    assert plan.windows, "the battery still discharges its existing charge"
    assert all(w.mode != "charge" for w in plan.windows)
    assert any("overgeslagen" in note for note in plan.notes)


def test_charging_is_kept_when_the_spread_beats_the_losses():
    start = dt.datetime(2026, 11, 16, 0, 0)
    prices = [0.05] * 20 + [0.30] * 76
    slots = _slots_from_prices(start, prices)
    plan = optimizer.build_plan(
        slots,
        start,
        soc=20,
        capacity_kwh=24,
        charge_power_kw=6,
        discharge_power_kw=6,
        target_soc=100,
        min_soc=20,
        standby_kw=0.0,
        rte=0.85,
    )
    assert any(w.mode == "charge" for w in plan.windows)
    assert not any("overgeslagen" in note for note in plan.notes)


def test_rte_sizes_the_charge_window():
    """A lossy battery needs more cheap quarter hours to reach the target."""
    start = dt.datetime(2026, 11, 16, 0, 0)
    prices = [0.05] * 20 + [0.30] * 76
    slots = _slots_from_prices(start, prices)

    def charge_minutes(rte):
        plan = optimizer.build_plan(
            slots,
            start,
            soc=20,
            capacity_kwh=24,
            charge_power_kw=6,
            discharge_power_kw=6,
            target_soc=100,
            min_soc=20,
            standby_kw=0.0,
            rte=rte,
        )
        return sum(
            (w.stop.hour * 60 + w.stop.minute) - (w.start.hour * 60 + w.start.minute)
            for w in plan.windows
            if w.mode == "charge"
        )

    assert charge_minutes(0.85) > charge_minutes(1.0)


def test_forecast_soc_applies_round_trip_efficiency():
    day = dt.datetime(2026, 11, 16, 0, 0)  # a Monday
    window = optimizer.TouWindow(
        start=dt.time(2, 0), stop=dt.time(3, 0), mode="charge", power=100, days="mon"
    )
    lossless = optimizer.forecast_soc(
        [window],
        day,
        day + dt.timedelta(hours=3),
        soc=50,
        capacity_kwh=10,
        power_kw=5,
        rte=1.0,
    )
    lossy = optimizer.forecast_soc(
        [window],
        day,
        day + dt.timedelta(hours=3),
        soc=50,
        capacity_kwh=10,
        power_kw=5,
        rte=0.5,
    )
    # 5 kW for 1 h = 5 kWh; half of it lands in a 10 kWh battery = +25%.
    assert dict(lossless)[day + dt.timedelta(hours=3)] == 100
    assert dict(lossy)[day + dt.timedelta(hours=3)] == 75


def test_windows_cost_counts_charge_and_discharge():
    day = dt.datetime(2026, 11, 16, 0, 0)  # a Monday
    slots = [
        optimizer.PriceSlot(day + dt.timedelta(hours=1), 0.10),
        optimizer.PriceSlot(day + dt.timedelta(hours=2), 0.30),
    ]
    windows = [
        optimizer.TouWindow(
            start=dt.time(1, 0), stop=dt.time(1, 15), mode="charge", power=100, days="mon"
        ),
        optimizer.TouWindow(
            start=dt.time(2, 0),
            stop=dt.time(2, 15),
            mode="discharge",
            power=100,
            days="mon",
        ),
    ]
    cost = optimizer.windows_cost(
        windows, slots, day, charge_power_kw=6, discharge_power_kw=7
    )
    # Charging 6 kW * 0.25 h at 0.10 = +0.15; discharging 7 kW * 0.25 h at 0.30
    # = -0.525; net = -0.375.
    assert cost == pytest.approx(-0.375)


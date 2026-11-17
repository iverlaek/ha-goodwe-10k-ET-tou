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


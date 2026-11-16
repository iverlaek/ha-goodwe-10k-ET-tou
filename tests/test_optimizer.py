"""Unit tests for the cheapest-hours planner (pure logic)."""

from __future__ import annotations

import datetime as dt

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


def test_pv_credit_reduces_charging():
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

    assert charge_minutes(with_pv) < charge_minutes(without)


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

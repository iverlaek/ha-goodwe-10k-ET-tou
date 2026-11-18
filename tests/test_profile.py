"""Unit tests for the quarter-hour consumption profile.

Pure logic only (no Home Assistant), loaded through the ``conftest`` helper. The
profile values are power in kW (the coordinator converts the recorder's watt
statistics), so a flat 1.0 kW profile is 24 kWh per day.
"""

from __future__ import annotations

import datetime as dt

from conftest import load

profile = load("profile")


def _points(day: dt.date, values, step_minutes: int = 15):
    """Build ``(moment, value)`` points across ``day`` from ``values``."""
    return [
        (
            dt.datetime.combine(day, dt.time(0, 0))
            + dt.timedelta(minutes=step_minutes * i),
            v,
        )
        for i, v in enumerate(values)
    ]


# -- quarter_index ---------------------------------------------------------


def test_quarter_index_maps_the_day():
    assert profile.quarter_index(dt.datetime(2026, 11, 16, 0, 0)) == 0
    assert profile.quarter_index(dt.datetime(2026, 11, 16, 0, 15)) == 1
    assert profile.quarter_index(dt.datetime(2026, 11, 16, 23, 45)) == 95
    assert profile.quarter_index(dt.datetime(2026, 11, 16, 12, 0)) == 48


# -- average_by_quarter ----------------------------------------------------


def test_average_by_quarter_averages_over_days():
    day1 = dt.date(2026, 11, 16)
    day2 = dt.date(2026, 11, 17)
    # Two days, both with 1.0 / 2.0 kW in the first quarter.
    points = _points(day1, [1.0]) + _points(day2, [2.0])
    out = profile.average_by_quarter(points)
    assert len(out) == profile.QUARTERS
    assert out[0] == 1.5
    assert out[1] == 0.0


def test_average_by_quarter_ignores_missing_values():
    day = dt.date(2026, 11, 16)
    points = _points(day, [1.0, None, 3.0])
    out = profile.average_by_quarter(points)
    assert out[0] == 1.0
    assert out[1] == 0.0
    assert out[2] == 3.0


def test_average_by_quarter_puts_samples_in_their_quarter():
    day = dt.date(2026, 11, 16)
    # 5-minute samples: the first three fall in quarter 0, the fourth in quarter 1.
    points = _points(day, [1.0, 2.0, 3.0, 4.0], step_minutes=5)
    out = profile.average_by_quarter(points)
    assert out[0] == 2.0
    assert out[1] == 4.0


# -- split_grid ------------------------------------------------------------


def test_split_grid_negative_is_import():
    day = dt.date(2026, 11, 16)
    points = _points(day, [-500.0, 800.0, 0.0])
    imports, exports = profile.split_grid(points)
    assert [v for _, v in imports] == [500.0, 0.0, 0.0]
    assert [v for _, v in exports] == [0.0, 800.0, 0.0]


def test_split_grid_invert_flips_the_sign():
    day = dt.date(2026, 11, 16)
    points = _points(day, [-500.0, 800.0])
    imports, exports = profile.split_grid(points, invert=True)
    assert [v for _, v in imports] == [0.0, 800.0]
    assert [v for _, v in exports] == [500.0, 0.0]


# -- daily_kwh / load_kwh / average_kw -------------------------------------


def test_daily_kwh_sums_the_quarters():
    flat = [1.0] * profile.QUARTERS  # 1 kW in every quarter
    assert profile.daily_kwh(flat) == 24.0


def test_load_kwh_walks_a_partial_quarter():
    flat = [2.0] * profile.QUARTERS  # 2 kW everywhere
    start = dt.datetime(2026, 11, 16, 0, 0)
    # Half an hour at 2 kW is 1 kWh.
    assert profile.load_kwh(flat, start, start + dt.timedelta(minutes=30)) == 1.0


def test_load_kwh_falls_back_to_standby_out_of_range():
    short = [2.0] * 4  # only the first hour is covered
    start = dt.datetime(2026, 11, 16, 0, 0)
    # The covered hour uses the profile, the rest falls back to the standby.
    assert profile.load_kwh(short, start, start + dt.timedelta(hours=2)) == 2.0
    assert (
        profile.load_kwh(
            short, start, start + dt.timedelta(hours=2), fallback_kw=1.5
        )
        == 3.5
    )


def test_average_kw_is_the_mean_over_the_span():
    flat = [3.0] * profile.QUARTERS
    start = dt.datetime(2026, 11, 16, 6, 0)
    assert profile.average_kw(flat, start, start + dt.timedelta(hours=2)) == 3.0
    assert profile.average_kw(flat, start, start) == 0.0


def test_load_kwh_follows_the_day_shape():
    shaped = [0.0] * profile.QUARTERS
    shaped[profile.quarter_index(dt.datetime(2026, 11, 16, 18, 0))] = 4.0
    start = dt.datetime(2026, 11, 16, 18, 0)
    # One quarter at 4 kW is 1 kWh.
    assert profile.load_kwh(shaped, start, start + dt.timedelta(minutes=15)) == 1.0


# -- covered_days ----------------------------------------------------------


def test_covered_days_counts_distinct_days():
    day1 = dt.date(2026, 11, 16)
    day2 = dt.date(2026, 11, 17)
    points = _points(day1, [1.0, 2.0]) + _points(day2, [3.0])
    assert profile.covered_days(points) == 2
    assert profile.covered_days([]) == 0

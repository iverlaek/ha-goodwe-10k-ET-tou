"""Unit tests for the deviation log and the adaptive house-load model.

Pure logic only (no Home Assistant), loaded through the ``conftest`` helper.
"""

from __future__ import annotations

import datetime as dt

from conftest import load

deviation = load("deviation")


# -- apparent temperature --------------------------------------------------


def test_apparent_temperature_falls_back_to_air_temp():
    assert deviation.apparent_temperature(12.5, None, None) == 12.5
    assert deviation.apparent_temperature(12.5, 80, None) == 12.5
    assert deviation.apparent_temperature(12.5, None, 10) == 12.5
    assert deviation.apparent_temperature(None, 80, 10) is None


def test_apparent_temperature_wind_makes_it_colder():
    calm = deviation.apparent_temperature(10.0, 80.0, 0.0)
    windy = deviation.apparent_temperature(10.0, 80.0, 40.0)
    assert windy < calm


def test_apparent_temperature_humidity_makes_it_warmer():
    dry = deviation.apparent_temperature(25.0, 20.0, 5.0)
    humid = deviation.apparent_temperature(25.0, 95.0, 5.0)
    assert humid > dry


# -- degree hours ----------------------------------------------------------


def test_degree_hours_counts_only_below_the_base():
    assert deviation.degree_hours(10.0, 15.5, 1.0) == 5.5
    assert deviation.degree_hours(15.5, 15.5, 1.0) == 0.0
    assert deviation.degree_hours(22.0, 15.5, 1.0) == 0.0


def test_degree_hours_scales_with_the_period_and_handles_unknown():
    assert deviation.degree_hours(10.0, 15.5, 2.0) == 11.0
    assert deviation.degree_hours(None, 15.5, 1.0) == 0.0
    assert deviation.degree_hours(10.0, 15.5, 0.0) == 0.0


# -- samples ---------------------------------------------------------------


def test_make_sample_computes_deviation_and_pv_error():
    hour = dt.datetime(2026, 11, 17, 3, 0, tzinfo=dt.UTC)
    sample = deviation.make_sample(
        hour,
        hours=1.0,
        actual_kwh=1.4,
        planned_kwh=1.1,
        outdoor_temp=4.2,
        feels_like=1.8,
        degree_hour=13.7,
        pv_kwh=0.4,
        pv_forecast_kwh=0.9,
    )
    assert sample["time"] == "2026-11-17T03:00+00:00"
    assert sample["deviation_kwh"] == 0.3
    assert sample["pv_error_kwh"] == -0.5
    assert sample["outdoor_temp"] == 4.2
    assert sample["feels_like"] == 1.8
    assert sample["degree_hours"] == 13.7


def test_make_sample_pv_error_none_without_forecast():
    sample = deviation.make_sample(
        dt.datetime(2026, 11, 17, 3, 0, tzinfo=dt.UTC),
        hours=1.0,
        actual_kwh=1.0,
        planned_kwh=1.0,
    )
    assert sample["pv_error_kwh"] is None
    assert sample["outdoor_temp"] is None
    assert sample["feels_like"] is None


def test_trim_keeps_newest():
    samples = [{"i": i} for i in range(5)]
    assert deviation.trim(samples, 3) == [{"i": 0}, {"i": 1}, {"i": 2}]
    assert deviation.trim(samples, 0) == []


# -- learning --------------------------------------------------------------


def _hourly(degrees, actual_kwh, hours=1.0):
    return {
        "hours": hours,
        "degree_hours": degrees * hours,
        "actual_kwh": actual_kwh * hours,
    }


def test_learn_needs_enough_samples():
    few = [_hourly(0, 1.0), _hourly(10, 2.0)]
    assert deviation.learn(
        few, fallback_base_kw=1.2, fallback_temp_coeff=0.05
    ) is None


def test_learn_fits_a_clean_line():
    samples = [
        _hourly(0, 1.0),
        _hourly(5, 1.5),
        _hourly(10, 2.0),
        _hourly(15, 2.5),
    ]
    model = deviation.learn(
        samples,
        fallback_base_kw=1.2,
        fallback_temp_coeff=0.05,
        min_samples=2,
    )
    assert model is not None
    assert model["samples"] == 4
    # 4 samples over a 4-sample warm-up: the fit stands on its own.
    assert model["base_kw"] == 1.0
    assert model["temp_coeff"] == 0.1
    assert model["r2"] == 1.0


def test_learn_clamps_a_negative_slope_to_zero():
    samples = [
        _hourly(0, 2.5),
        _hourly(5, 2.0),
        _hourly(10, 1.5),
        _hourly(15, 1.0),
    ]
    model = deviation.learn(
        samples,
        fallback_base_kw=1.2,
        fallback_temp_coeff=0.05,
        min_samples=2,
    )
    assert model is not None
    # Cold can only add load, so the slope never goes negative.
    assert model["temp_coeff"] == 0.0
    assert model["base_kw"] == 1.75


def test_learn_clamps_the_base_into_range():
    samples = [_hourly(0, 20.0) for _ in range(4)]
    model = deviation.learn(
        samples,
        fallback_base_kw=1.2,
        fallback_temp_coeff=0.05,
        min_samples=2,
    )
    assert model is not None
    assert model["base_kw"] == 10.0  # clamped to max_base_kw


def test_learn_clamps_the_coefficient():
    samples = [
        _hourly(0, 0.0),
        _hourly(5, 5.0),
        _hourly(10, 10.0),
        _hourly(15, 15.0),
    ]
    model = deviation.learn(
        samples,
        fallback_base_kw=1.2,
        fallback_temp_coeff=0.05,
        min_samples=2,
    )
    assert model is not None
    assert model["temp_coeff"] == 0.5  # clamped to max_temp_coeff


def test_learn_blends_with_the_defaults_while_learning():
    samples = [_hourly(0, 1.0), _hourly(10, 2.0)]
    model = deviation.learn(
        samples,
        fallback_base_kw=5.0,
        fallback_temp_coeff=0.2,
        min_samples=2,
    )
    assert model is not None
    # n = 2 over a 4-sample warm-up -> half the fitted line, half the defaults.
    assert model["base_kw"] == 3.0
    assert model["temp_coeff"] == 0.15


def test_learn_skips_unusable_samples():
    samples = [
        {"hours": 0.0, "degree_hours": 0.0, "actual_kwh": 1.0},
        {"hours": 1.0, "degree_hours": 0.0, "actual_kwh": None},
        {"hours": 1.0, "degree_hours": None, "actual_kwh": 1.0},
        _hourly(0, 1.0),
        _hourly(10, 2.0),
    ]
    model = deviation.learn(
        samples,
        fallback_base_kw=1.2,
        fallback_temp_coeff=0.05,
        min_samples=2,
    )
    assert model is not None
    assert model["samples"] == 2


# -- effective load --------------------------------------------------------


def test_effective_load_adds_degrees_below_the_base():
    assert deviation.effective_load_kw(1.2, 0.05, 10.0, 15.5) == 1.475


def test_effective_load_is_flat_when_warm_or_unknown():
    assert deviation.effective_load_kw(1.2, 0.05, 20.0, 15.5) == 1.2
    assert deviation.effective_load_kw(1.2, 0.05, None, 15.5) == 1.2


def test_effective_load_ignores_a_negative_coefficient():
    assert deviation.effective_load_kw(1.2, -0.5, 10.0, 15.5) == 1.2


def test_effective_load_clamps_a_negative_base_then_adds_degrees():
    # A negative base is clamped to zero; the weather term still applies.
    assert deviation.effective_load_kw(-3.0, 0.05, 10.0, 15.5) == 0.275
    assert deviation.effective_load_kw(-3.0, 0.05, 20.0, 15.5) == 0.0

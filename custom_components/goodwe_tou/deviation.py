"""Deviation tracking and an adaptive house-load model.

The planner assumes the house draws a constant standby load. In reality that
load moves with the weather: a cold day makes the air conditioner work harder, a
wrong weather forecast means the real load drifts away from the plan, and the
battery then runs flat earlier than planned — the low-cell guard fires and the
inverter drops to standby.

This module keeps a compact, hourly log of the *deviation* between what the
planner assumed and what the house really used, together with the variables that
influence it: the outdoor temperature, the feels-like (apparent) temperature,
the heating degree hours and the PV forecast error. The raw temperature,
humidity, wind and load history already lives in Home Assistant's recorder
database, so only these *derived* samples are stored here; they are what the
house-load model is fitted from, and they can be kept much longer than the raw
sensor history.

Everything here is pure (no Home Assistant imports) so the model is easy to
test.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: Default number of hourly samples to keep (newest first). 2160 hours is 90
#: days, enough to learn a seasonal temperature response.
DEFAULT_KEEP = 2160

#: Below this many samples the fitted model is blended heavily with the
#: configured defaults, so a handful of odd hours never move the plan on their
#: own.
MIN_SAMPLES = 24


def apparent_temperature(
    temperature_c: float | None,
    humidity_pct: float | None,
    wind_kmh: float | None,
) -> float | None:
    """The Australian apparent temperature ("feels like"), in °C.

    Combines the air temperature with the cooling effect of wind and the
    humid-heat effect of moisture. Falls back to the plain air temperature when
    the humidity or wind is missing, and to ``None`` when even that is unknown.
    """
    if temperature_c is None:
        return None
    temp = float(temperature_c)
    if humidity_pct is None or wind_kmh is None:
        return temp
    humidity = min(100.0, max(1.0, float(humidity_pct)))
    wind_ms = max(0.0, float(wind_kmh) / 3.6)
    vapour = (humidity / 100.0) * 6.105 * math.exp(17.27 * temp / (237.7 + temp))
    return temp + 0.33 * vapour - 0.70 * wind_ms - 4.00


def degree_hours(feels_like_c: float | None, base_c: float, hours: float) -> float:
    """Heating degree hours: how far the feels-like temp sits below ``base_c``.

    ``hours`` is the length of the period the temperature applies to, so the
    result is in °C·h. Divide by 24 for the familiar degree-day figure.
    """
    if feels_like_c is None or hours <= 0:
        return 0.0
    return max(0.0, float(base_c) - float(feels_like_c)) * float(hours)


def make_sample(
    hour: dt.datetime,
    *,
    hours: float,
    actual_kwh: float,
    planned_kwh: float,
    outdoor_temp: float | None = None,
    feels_like: float | None = None,
    degree_hour: float = 0.0,
    pv_kwh: float | None = None,
    pv_forecast_kwh: float | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Build one hourly deviation sample (a JSON-friendly dict).

    ``actual_kwh`` is what the house really used, ``planned_kwh`` what the model
    assumed, so ``deviation_kwh`` is the model's error. ``pv_error_kwh`` is the
    produced PV minus the forecast for that hour — a large negative value means
    the weather came out worse than predicted.
    """
    deviation = float(actual_kwh) - float(planned_kwh)
    pv_error = None
    if pv_kwh is not None and pv_forecast_kwh is not None:
        pv_error = float(pv_kwh) - float(pv_forecast_kwh)
    return {
        "time": hour.isoformat(timespec="minutes"),
        "hours": round(float(hours), 4),
        "actual_kwh": round(float(actual_kwh), 4),
        "planned_kwh": round(float(planned_kwh), 4),
        "deviation_kwh": round(deviation, 4),
        "outdoor_temp": None if outdoor_temp is None else round(float(outdoor_temp), 2),
        "feels_like": None if feels_like is None else round(float(feels_like), 2),
        "degree_hours": round(float(degree_hour), 4),
        "pv_kwh": None if pv_kwh is None else round(float(pv_kwh), 4),
        "pv_forecast_kwh": (
            None if pv_forecast_kwh is None else round(float(pv_forecast_kwh), 4)
        ),
        "pv_error_kwh": None if pv_error is None else round(pv_error, 4),
        "recorded": (now or dt.datetime.now(dt.UTC).astimezone()).isoformat(
            timespec="seconds"
        ),
    }


def trim(samples: list[dict[str, Any]], keep: int = DEFAULT_KEEP) -> list[dict[str, Any]]:
    """Drop the oldest samples so at most ``keep`` remain (newest first)."""
    if keep <= 0:
        del samples[:]
    else:
        del samples[keep:]
    return samples


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def learn(
    samples: Iterable[Mapping[str, Any]],
    *,
    fallback_base_kw: float,
    fallback_temp_coeff: float,
    min_samples: int = MIN_SAMPLES,
    min_base_kw: float = 0.1,
    max_base_kw: float = 10.0,
    max_temp_coeff: float = 0.5,
) -> dict[str, Any] | None:
    """Fit ``actual_kw = base + temp_coeff × (degrees below the base temp)``.

    A plain least-squares line through the hourly samples, with the slope clamped
    to be non-negative (cold can only ever *raise* the load) and both
    coefficients kept inside sane bounds. The fitted line is blended with the
    configured defaults by ``min(1, n / (2·min_samples))``, so the model only
    takes over once enough hours have been logged. Returns ``None`` when there is
    too little usable data.
    """
    xs: list[float] = []
    ys: list[float] = []
    for sample in samples:
        hours = float(sample.get("hours") or 0.0)
        if hours <= 0:
            continue
        actual = sample.get("actual_kwh")
        degrees = sample.get("degree_hours")
        if actual is None or degrees is None:
            continue
        xs.append(float(degrees) / hours)
        ys.append(float(actual) / hours)
    n = len(xs)
    if n < max(2, int(min_samples)):
        return None

    mean_x = _mean(xs)
    mean_y = _mean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    if sxx <= 1e-9:
        slope = 0.0
        intercept = mean_y
    else:
        slope = sxy / sxx
        if slope < 0.0:
            # Cold can only add load; a negative slope is noise, not a signal.
            slope = 0.0
            intercept = mean_y
        else:
            intercept = mean_y - slope * mean_x

    base = min(max_base_kw, max(min_base_kw, intercept))
    coeff = min(max_temp_coeff, max(0.0, slope))
    weight = min(1.0, n / (2.0 * max(2, int(min_samples))))
    blended_base = weight * base + (1.0 - weight) * float(fallback_base_kw)
    blended_coeff = weight * coeff + (1.0 - weight) * float(fallback_temp_coeff)

    # Coefficient of determination, for the diagnostics in the card.
    total = sum((y - mean_y) ** 2 for y in ys)
    if total > 1e-9 and sxx > 1e-9:
        residual = sum(
            (y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys, strict=True)
        )
        r2 = max(0.0, min(1.0, 1.0 - residual / total))
    else:
        r2 = 0.0

    return {
        "base_kw": round(blended_base, 4),
        "temp_coeff": round(blended_coeff, 4),
        "samples": n,
        "r2": round(r2, 3),
        "fitted_base_kw": round(base, 4),
        "fitted_temp_coeff": round(coeff, 4),
    }


def effective_load_kw(
    base_kw: float,
    temp_coeff: float,
    feels_like_c: float | None,
    base_c: float,
) -> float:
    """The house load (kW) the planner should assume right now.

    ``base_kw`` is the mild-weather load; every degree the feels-like temperature
    sits below ``base_c`` adds ``temp_coeff`` kW (the air conditioner / heating).
    """
    load = max(0.0, float(base_kw))
    if feels_like_c is not None:
        load += max(0.0, float(temp_coeff)) * max(
            0.0, float(base_c) - float(feels_like_c)
        )
    return load

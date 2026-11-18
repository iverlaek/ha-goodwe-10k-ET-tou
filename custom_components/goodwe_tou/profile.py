"""Quarter-hour consumption profiles built from the recorder history.

The planner used to assume one flat house load (a configured standby power plus
a temperature correction). That is a single number for the whole day, while a
real house has a clear daily rhythm: a quiet night, a morning peak, a low
midday (the "duck curve" of a home with solar) and an evening peak.

This module turns a history of *power* samples into a **day profile**: the
average power in each of the 96 quarter hours of the day. Two profiles are built
from the grid meter — how much the house *imported* and how much it *exported* —
and a third from the house-consumption sensor, which is the load the battery
really has to cover.

Everything here is pure (no Home Assistant imports) so it is easy to test. The
coordinator reads the recorder's 5-minute statistics and feeds the
``(moment, value)`` points in; the times must already be in the local timezone,
because the profile is keyed by the local time of day.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence

#: Quarter hours in a day.
QUARTERS = 96
#: The slot length the price horizon (and therefore the profile) is built on.
DEFAULT_SLOT_MINUTES = 15

#: One point of a power series: the local moment and the power in watt.
Point = tuple[dt.datetime, float | None]


def quarter_index(moment: dt.datetime, slot_minutes: int = DEFAULT_SLOT_MINUTES) -> int:
    """The 0-based quarter-of-day (0 = 00:00, 95 = 23:45) for ``moment``."""
    return (moment.hour * 60 + moment.minute) // max(1, int(slot_minutes))


def average_by_quarter(
    points: Iterable[Point],
    *,
    slot_minutes: int = DEFAULT_SLOT_MINUTES,
    quarters: int = QUARTERS,
) -> list[float]:
    """Average power (W) per quarter-of-day over every day in ``points``.

    Each sample counts for the quarter it falls in, so the result is the mean
    over all occurrences of that quarter in the window. Quarters with no sample
    come out as ``0.0``. The samples must already be in local time.
    """
    step = max(1, int(slot_minutes))
    sums = [0.0] * quarters
    counts = [0] * quarters
    for moment, value in points:
        if moment is None or value is None:
            continue
        index = quarter_index(moment, step)
        if 0 <= index < quarters:
            sums[index] += float(value)
            counts[index] += 1
    return [
        round(sums[i] / counts[i], 4) if counts[i] else 0.0 for i in range(quarters)
    ]


def split_grid(
    points: Iterable[Point], *, invert: bool = False
) -> tuple[list[Point], list[Point]]:
    """Split a signed grid-power series into import and export series.

    The GoodWe meter reports a **negative** value while the house draws from the
    grid, so ``invert=False`` keeps that convention: the import series is
    ``max(0, -value)`` and the export series is ``max(0, value)``. Pass
    ``invert=True`` for a meter that reports the other way round.
    """
    imports: list[Point] = []
    exports: list[Point] = []
    for moment, value in points:
        if value is None:
            continue
        draw = float(value) if invert else -float(value)
        imports.append((moment, max(0.0, draw)))
        exports.append((moment, max(0.0, -draw)))
    return imports, exports


def daily_kwh(
    profile: Sequence[float], *, slot_minutes: int = DEFAULT_SLOT_MINUTES
) -> float:
    """The energy (kWh) a profile moves in one full day (sum of the 96 quarters)."""
    hours = max(1, int(slot_minutes)) / 60.0
    return round(sum(float(value) for value in profile) * hours, 3)


def load_kwh(
    profile: Sequence[float],
    start: dt.datetime,
    end: dt.datetime,
    *,
    slot_minutes: int = DEFAULT_SLOT_MINUTES,
    fallback_kw: float = 0.0,
) -> float:
    """Energy (kWh) the profile draws between ``start`` and ``end``.

    Walks the range quarter by quarter in local time, so a partial quarter counts
    for the fraction that is covered. A quarter the profile does not cover (an
    out-of-range index) falls back to ``fallback_kw`` (the configured standby), so
    a short profile never silently drops the house load to zero. A zero *inside*
    the profile is a real zero, not a gap.
    """
    if end <= start:
        return 0.0
    step = max(1, int(slot_minutes))
    total = 0.0
    cursor = start
    while cursor < end:
        index = quarter_index(cursor, step)
        quarter_start = cursor.replace(
            minute=(cursor.minute // step) * step, second=0, microsecond=0
        )
        quarter_end = quarter_start + dt.timedelta(minutes=step)
        segment_end = min(end, quarter_end)
        hours = (segment_end - cursor).total_seconds() / 3600.0
        value = profile[index] if 0 <= index < len(profile) else None
        kw = float(value) if value is not None else float(fallback_kw)
        total += kw * hours
        cursor = segment_end
    return total


def average_kw(
    profile: Sequence[float],
    start: dt.datetime,
    end: dt.datetime,
    *,
    slot_minutes: int = DEFAULT_SLOT_MINUTES,
    fallback_kw: float = 0.0,
) -> float:
    """Average power (kW) the profile draws between ``start`` and ``end``."""
    hours = (end - start).total_seconds() / 3600.0
    if hours <= 0:
        return float(fallback_kw)
    return load_kwh(
        profile, start, end, slot_minutes=slot_minutes, fallback_kw=fallback_kw
    ) / hours


def covered_days(points: Iterable[Point]) -> int:
    """How many distinct local calendar days the points span."""
    days = set()
    for moment, value in points:
        if moment is None or value is None:
            continue
        days.add(moment.date())
    return len(days)

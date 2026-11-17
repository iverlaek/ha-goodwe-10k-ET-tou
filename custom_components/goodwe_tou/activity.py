"""Activity log for writes sent to the inverter.

Every modification the integration sends to the inverter is recorded as an
:func:`make_entry` dictionary: when it happened, what changed, whether the
inverter confirmed it, and — when it did not — why. The log is kept newest
first and is capped at :data:`MAX_ENTRIES`.

Everything here is pure (no Home Assistant imports) so the shape of the log is
easy to test.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from typing import Any

from .const import MAX_LOG_SIZE, WINDOWS
from .registers import TouWindow

#: Hard cap on how many entries the log keeps (newest first).
MAX_ENTRIES = MAX_LOG_SIZE

#: Known action keys (used by the cards for their labels).
ACTIONS = (
    "apply",
    "set_windows",
    "cheapest_hours",
    "auto_replan",
    "window_time",
    "window_power",
    "window_days",
    "eco",
    "export_limit",
    "guard",
)


def _fmt_time(value: dt.time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def window_summary(number: int, window: TouWindow) -> dict[str, Any]:
    """A compact, JSON-friendly snapshot of one window."""
    return {
        "n": number,
        "start": _fmt_time(window.start),
        "stop": _fmt_time(window.stop),
        "power": window.signed_power,
        "days": window.days,
    }


def window_changes(
    before: Mapping[int, TouWindow], after: Mapping[int, TouWindow]
) -> list[dict[str, Any]]:
    """Field-level differences between two sets of windows."""
    changes: list[dict[str, Any]] = []
    for number in WINDOWS:
        old = before.get(number)
        new = after.get(number)
        if old is None or new is None:
            continue
        if old.start != new.start:
            changes.append(
                {
                    "field": f"window_{number}_start",
                    "old": _fmt_time(old.start),
                    "new": _fmt_time(new.start),
                }
            )
        if old.stop != new.stop:
            changes.append(
                {
                    "field": f"window_{number}_stop",
                    "old": _fmt_time(old.stop),
                    "new": _fmt_time(new.stop),
                }
            )
        if old.signed_power != new.signed_power:
            changes.append(
                {
                    "field": f"window_{number}_power",
                    "old": old.signed_power,
                    "new": new.signed_power,
                }
            )
        if old.days != new.days:
            changes.append(
                {
                    "field": f"window_{number}_days",
                    "old": old.days,
                    "new": new.days,
                }
            )
    return changes


def make_entry(
    action: str,
    *,
    changes: Iterable[Mapping[str, Any]] | None = None,
    windows: Iterable[Mapping[str, Any]] | None = None,
    result: str = "ok",
    error: str | None = None,
    reason: str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Build one log entry.

    ``changes`` holds field-level differences (``field``/``old``/``new``);
    ``windows`` holds the full set of windows that was written, which is useful
    when nothing changed (for example a manual *Apply*). ``reason`` explains a
    change the integration decided on itself (the quarter-hour re-check).
    """
    moment = now or dt.datetime.now(dt.UTC).astimezone()
    return {
        "time": moment.isoformat(timespec="seconds"),
        "action": action,
        "result": "error" if result == "error" else "ok",
        "error": error,
        "reason": reason,
        "changes": [dict(change) for change in (changes or [])],
        "windows": [dict(window) for window in (windows or [])],
    }


def trim(entries: list[dict[str, Any]], limit: int = MAX_ENTRIES) -> list[dict[str, Any]]:
    """Drop the oldest entries so at most ``limit`` remain (newest first)."""
    if limit <= 0:
        del entries[:]
    else:
        del entries[limit:]
    return entries


def touches_window(entry: Mapping[str, Any], number: int) -> bool:
    """Whether an entry concerns window ``number``."""
    prefix = f"window_{number}_"
    for change in entry.get("changes") or []:
        if str(change.get("field", "")).startswith(prefix):
            return True
    for window in entry.get("windows") or []:
        try:
            if int(window.get("n", 0)) == number:
                return True
        except (TypeError, ValueError):
            continue
    return False


def filter_entries(
    entries: Iterable[Mapping[str, Any]],
    *,
    window: int | None = None,
    result: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Filter a log by window and/or result, then cap it to ``limit``."""
    selected: list[dict[str, Any]] = []
    for entry in entries:
        if window is not None and not touches_window(entry, window):
            continue
        if result is not None and entry.get("result") != result:
            continue
        selected.append(dict(entry))
        if limit is not None and len(selected) >= limit:
            break
    return selected

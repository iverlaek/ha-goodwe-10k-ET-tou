"""Unit tests for the inverter activity log (pure logic)."""

from __future__ import annotations

import datetime as dt

from conftest import load

activity = load("activity")
registers = load("registers")


def _window(**overrides):
    values = {
        "start": dt.time(2, 0),
        "stop": dt.time(4, 0),
        "mode": "charge",
        "power": 100,
        "days": "all",
    }
    values.update(overrides)
    return registers.TouWindow(**values)


def test_make_entry_shape():
    moment = dt.datetime(2026, 11, 17, 14, 5, 0, tzinfo=dt.UTC)
    entry = activity.make_entry(
        "window_power",
        changes=[{"field": "window_1_power", "old": 0, "new": -100}],
        now=moment,
    )
    assert entry["time"] == "2026-11-17T14:05:00+00:00"
    assert entry["action"] == "window_power"
    assert entry["result"] == "ok"
    assert entry["error"] is None
    assert entry["changes"] == [{"field": "window_1_power", "old": 0, "new": -100}]
    assert entry["windows"] == []


def test_make_entry_marks_errors():
    entry = activity.make_entry("apply", result="error", error="read-back mismatch")
    assert entry["result"] == "error"
    assert entry["error"] == "read-back mismatch"


def test_make_entry_defaults_to_now():
    entry = activity.make_entry("eco")
    parsed = dt.datetime.fromisoformat(entry["time"])
    assert parsed.tzinfo is not None


def test_window_changes_reports_each_field():
    before = {1: _window()}
    after = {
        1: _window(start=dt.time(3, 0), power=40, days="mon")
    }
    changes = activity.window_changes(before, after)
    fields = {change["field"]: (change["old"], change["new"]) for change in changes}
    assert fields["window_1_start"] == ("02:00", "03:00")
    # a charging window has a negative signed power
    assert fields["window_1_power"] == (-100, -40)
    assert fields["window_1_days"] == ("all", "mon")
    # stop did not change
    assert "window_1_stop" not in fields


def test_window_changes_ignores_missing_windows():
    assert activity.window_changes({1: _window()}, {}) == []
    assert activity.window_changes({}, {1: _window()}) == []


def test_window_summary_is_json_friendly():
    summary = activity.window_summary(2, _window(mode="discharge", power=80, days="sat"))
    assert summary == {
        "n": 2,
        "start": "02:00",
        "stop": "04:00",
        "power": 80,
        "days": "sat",
    }


def test_trim_keeps_newest():
    entries = [{"i": i} for i in range(5)]
    assert activity.trim(entries, 3) == [{"i": 0}, {"i": 1}, {"i": 2}]
    assert activity.trim(entries, 0) == []


def test_touches_window():
    change_entry = {"changes": [{"field": "window_2_power"}], "windows": []}
    plan_entry = {"changes": [], "windows": [{"n": 3}]}
    assert activity.touches_window(change_entry, 2)
    assert not activity.touches_window(change_entry, 3)
    assert activity.touches_window(plan_entry, 3)
    assert not activity.touches_window(plan_entry, 1)


def test_filter_entries_by_result_and_window():
    entries = [
        {
            "action": "window_power",
            "result": "error",
            "changes": [{"field": "window_1_power"}],
            "windows": [],
        },
        {
            "action": "apply",
            "result": "ok",
            "changes": [],
            "windows": [{"n": 2}],
        },
        {
            "action": "window_days",
            "result": "ok",
            "changes": [{"field": "window_1_days"}],
            "windows": [],
        },
    ]
    assert activity.filter_entries(entries, result="ok") == entries[1:]
    assert activity.filter_entries(entries, window=1) == [entries[0], entries[2]]
    assert activity.filter_entries(entries, window=1, result="error") == [entries[0]]
    assert activity.filter_entries(entries, limit=2) == entries[:2]

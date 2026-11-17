"""Unit tests for the pure Tibber price helpers."""

from __future__ import annotations

import datetime as dt

from conftest import load

prices = load("prices")


def _response():
    return {
        "prices": {
            "akkerv5": [
                {"start_time": "2026-11-17T00:00:00.000+01:00", "price": 0.3714},
                {"start_time": "2026-11-17T00:15:00.000+01:00", "price": 0.3721},
                {"start_time": "2026-11-17T00:30:00.000+01:00", "price": 0.3739},
            ]
        }
    }


def test_tibber_prices_normalises_the_first_home():
    records, home = prices.tibber_prices(_response())
    assert home == "akkerv5"
    assert records == [
        {"time": "2026-11-17T00:00:00.000+01:00", "price": 0.3714},
        {"time": "2026-11-17T00:15:00.000+01:00", "price": 0.3721},
        {"time": "2026-11-17T00:30:00.000+01:00", "price": 0.3739},
    ]


def test_tibber_prices_picks_the_named_home():
    response = {
        "prices": {
            "home_a": [{"start_time": "2026-11-17T00:00:00+01:00", "price": 1.0}],
            "home_b": [{"start_time": "2026-11-17T00:00:00+01:00", "price": 2.0}],
        }
    }
    records, home = prices.tibber_prices(response, "home_b")
    assert home == "home_b"
    assert records[0]["price"] == 2.0

    # An unknown home falls back to the first one rather than returning nothing.
    records, home = prices.tibber_prices(response, "missing")
    assert home == "home_a"
    assert records[0]["price"] == 1.0


def test_tibber_prices_skips_bad_records_and_sorts():
    response = {
        "prices": {
            "akkerv5": [
                {"start_time": "2026-11-17T00:30:00+01:00", "price": "0.30"},
                {"start_time": "not-a-date", "price": 0.1},
                {"start_time": "2026-11-17T00:00:00+01:00", "price": 0.10},
                {"start_time": "2026-11-17T00:15:00+01:00"},
                {},
                "nonsense",
            ]
        }
    }
    records, _ = prices.tibber_prices(response)
    assert [record["time"] for record in records] == [
        "2026-11-17T00:00:00+01:00",
        "2026-11-17T00:30:00+01:00",
    ]
    assert records[1]["price"] == 0.30


def test_tibber_prices_handles_unexpected_shapes():
    assert prices.tibber_prices(None) == ([], None)
    assert prices.tibber_prices({}) == ([], None)
    assert prices.tibber_prices({"prices": {}}) == ([], None)
    assert prices.tibber_prices({"prices": {"home": "not-a-list"}}) == ([], None)


def test_current_price_uses_the_running_slot():
    records, _ = prices.tibber_prices(_response())
    # 00:20 falls in the 00:15 slot.
    now = dt.datetime.fromisoformat("2026-11-17T00:20:00.000+01:00")
    assert prices.current_price(records, now) == 0.3721


def test_current_price_falls_back_to_the_first_slot():
    records, _ = prices.tibber_prices(_response())
    before = dt.datetime.fromisoformat("2026-11-16T23:00:00.000+01:00")
    assert prices.current_price(records, before) == 0.3714


def test_current_price_is_none_without_records():
    assert prices.current_price([], dt.datetime.now(dt.UTC)) is None

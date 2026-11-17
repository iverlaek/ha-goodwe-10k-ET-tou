"""Read Tibber day-ahead prices from the ``tibber.get_prices`` service.

Tibber's own price entity only exposes the *current* price plus a few statistics;
the full hourly/quarter-hourly list comes from the ``tibber.get_prices`` service,
which answers with a per-home list of ``{start_time, price}`` records for today
and tomorrow. This module turns that response into the ``{time, price}`` shape the
rest of the integration already understands, so the price sensor and the planner
can share one format.

Everything here is pure (no Home Assistant imports) so it is easy to test.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

#: The integration that owns the price service.
TIBBER_DOMAIN = "tibber"
#: The service that returns the full price list for today and tomorrow.
TIBBER_SERVICE = "get_prices"


def tibber_prices(
    response: object, home: str = ""
) -> tuple[list[dict[str, Any]], str | None]:
    """Normalise a ``tibber.get_prices`` response into ``{time, price}`` records.

    The response looks like ``{"prices": {"<home>": [{"start_time": ...,
    "price": ...}, ...]}}``. ``home`` picks one home by its nickname; when it is
    empty (or not present) the first home is used, which is what a single-home
    account needs. Returns ``(records, home_name)``; records are sorted by time
    and unusable entries are skipped rather than raising, because the payload
    comes straight from another integration.
    """
    if not isinstance(response, Mapping):
        return [], None
    homes = response.get("prices", response)
    if not isinstance(homes, Mapping) or not homes:
        return [], None
    if home and home in homes:
        name = home
    else:
        name = next(iter(homes))
    raw = homes.get(name)
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return [], None

    records: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        moment = item.get("start_time", item.get("time", item.get("start")))
        price = item.get("price", item.get("value"))
        if moment is None or price is None:
            continue
        if isinstance(moment, dt.datetime):
            moment = moment.isoformat()
        if not isinstance(moment, str):
            continue
        try:
            dt.datetime.fromisoformat(moment)
        except ValueError:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        records.append({"time": moment, "price": price})
    records.sort(key=lambda record: record["time"])
    return records, name


def current_price(
    records: Sequence[Mapping[str, Any]], now: dt.datetime
) -> float | None:
    """The price of the slot covering ``now`` (the latest slot that has started).

    Falls back to the first slot when ``now`` is before every record, so a sensor
    always shows something useful. Returns ``None`` when there is nothing to show.
    """
    if not records:
        return None
    chosen: Any = None
    for record in records:
        moment = record.get("time")
        if not isinstance(moment, str):
            continue
        try:
            moment = dt.datetime.fromisoformat(moment)
        except ValueError:
            continue
        try:
            started = moment <= now
        except TypeError:  # naive vs aware; treat unparsable comparisons as no-match
            started = False
        if started:
            chosen = record.get("price")
        else:
            break
    if chosen is None:
        chosen = records[0].get("price")
    try:
        return float(chosen)
    except (TypeError, ValueError):
        return None

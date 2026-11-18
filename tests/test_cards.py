"""Consistency checks for the bundled Lovelace cards.

These are plain file checks (no Home Assistant needed): every card's
``CARD_VERSION`` must match the integration version, and the power card must
keep the defaults the README documents.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_WWW = _ROOT / "custom_components" / "goodwe_tou" / "www"
_MANIFEST = _ROOT / "custom_components" / "goodwe_tou" / "manifest.json"

_CARDS = {
    "goodwe-tou-card.js": "goodwe-tou-card",
    "goodwe-tou-planner-card.js": "goodwe-tou-planner-card",
    "goodwe-tou-log-card.js": "goodwe-tou-log-card",
    "goodwe-tou-power-card.js": "goodwe-tou-power-card",
    "goodwe-tou-deviation-card.js": "goodwe-tou-deviation-card",
    "goodwe-tou-profile-card.js": "goodwe-tou-profile-card",
}


def _version() -> str:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))["version"]


@pytest.mark.parametrize("filename", sorted(_CARDS))
def test_card_version_matches_manifest(filename: str) -> None:
    text = (_WWW / filename).read_text(encoding="utf-8")
    match = re.search(r'CARD_VERSION\s*=\s*"([^"]+)"', text)
    assert match, f"{filename} does not declare CARD_VERSION"
    assert match.group(1) == _version(), (
        f"{filename} is at {match.group(1)} but the manifest says {_version()}"
    )


@pytest.mark.parametrize("filename,element", sorted(_CARDS.items()))
def test_card_registers_its_element(filename: str, element: str) -> None:
    text = (_WWW / filename).read_text(encoding="utf-8")
    assert f'customElements.define("{element}"' in text


def test_overview_card_has_4h_gridlines_and_hover() -> None:
    text = (_WWW / "goodwe-tou-card.js").read_text(encoding="utf-8")
    # A vertical gridline every four hours on the SoC graph.
    assert "GRID_HOURS = 4" in text
    assert "_gridMoments" in text
    # Hovering shows the time and value of the nearest point.
    assert "mousemove" in text
    assert "mouseleave" in text
    assert "_updateHover" in text
    assert "tooltip" in text


def test_power_card_defaults() -> None:
    text = (_WWW / "goodwe-tou-power-card.js").read_text(encoding="utf-8")
    # The GoodWe meter reports a negative value while drawing from the grid.
    assert 'GRID_DEFAULT = "sensor.meter_active_power_total"' in text
    assert "invert_grid" in text
    # SOC ring, and the two smaller circles for temperature and voltage.
    assert "sensor.battery_state_of_charge" in text
    assert "sensor.battery_min_cell_temperature" in text
    assert "sensor.battery_max_cell_voltage" in text
    # The big circle is 50% larger than the small ones.
    small = re.search(r"const SMALL = (\d+);", text)
    big = re.search(r"const BIG = (\d+);", text)
    assert small and big
    assert int(big.group(1)) == int(small.group(1)) * 3 // 2


def test_deviation_card_reads_the_deviation_service() -> None:
    text = (_WWW / "goodwe-tou-deviation-card.js").read_text(encoding="utf-8")
    # The full log is fetched through the integration's own service.
    assert '"get_deviations"' in text
    # The refresh trigger is the deviation sensor (`<prefix>_afwijking`).
    assert "_afwijking" in text
    # It shows the learned model and today's totals.
    assert "effective_kw" in text
    assert "temp_coeff" in text


def test_profile_card_reads_the_profile_service() -> None:
    text = (_WWW / "goodwe-tou-profile-card.js").read_text(encoding="utf-8")
    # The profile is fetched through the integration's own service.
    assert '"get_profile"' in text
    # The refresh trigger is the profile sensor (`<prefix>_verbruiksprofiel`).
    assert "_verbruiksprofiel" in text
    # All three curves are drawn (the duck curve).
    assert "loadline" in text
    assert "importline" in text
    assert "exportline" in text
    # Hovering shows the three values at the nearest quarter hour.
    assert "mousemove" in text
    assert "_updateHover" in text
    assert "tooltip" in text


def test_planner_card_has_price_graph_hover() -> None:
    text = (_WWW / "goodwe-tou-planner-card.js").read_text(encoding="utf-8")
    # The price graph is drawn per quarter hour.
    assert "pbar" in text
    assert "_renderPrices" in text
    # Hovering shows the price at the nearest quarter hour, with a crosshair.
    assert "mousemove" in text
    assert "mouseleave" in text
    assert "_updatePriceHover" in text
    assert "crosshair" in text
    assert "tooltip" in text


def test_planner_card_logs_the_calculation_when_applying() -> None:
    """Toepassen passes the plan summary so the activity log shows the maths."""
    text = (_WWW / "goodwe-tou-planner-card.js").read_text(encoding="utf-8")
    assert "reason: this._summary" in text


def test_planner_card_offers_a_selectable_export_spread() -> None:
    """The minimum export spread is a field on the card, sent with the plan."""
    text = (_WWW / "goodwe-tou-planner-card.js").read_text(encoding="utf-8")
    assert 'id="spread"' in text
    assert "export_min_spread: this._spreadCt / 100" in text
    assert "spread_ct" in text

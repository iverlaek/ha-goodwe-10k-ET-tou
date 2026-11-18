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

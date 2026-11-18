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
    "goodwe-tou-cost-card.js": "goodwe-tou-cost-card",
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


def test_cost_card_prices_the_p1_meter_and_listens_for_planner_edits() -> None:
    """The cost card reads the P1 totals and reacts to the planner's edits."""
    text = (_WWW / "goodwe-tou-cost-card.js").read_text(encoding="utf-8")
    assert 'IMPORT_DEFAULT = "sensor.p1_meter_energy_import"' in text
    assert 'EXPORT_DEFAULT = "sensor.p1_meter_energy_export"' in text
    assert "recorder/statistics_during_period" in text
    assert "goodwe-tou-windows-changed" in text
    # Green check for a cheaper change, red cross for a more expensive one.
    assert '"✓"' in text
    assert '"✗"' in text
    assert 'better: "{delta} goedkoper"' in text
    assert 'worse: "{delta} duurder"' in text


def test_cost_card_uses_only_the_p1_meter_for_energy() -> None:
    """Import/export always come from the P1 totals or their tariff children."""
    text = (_WWW / "goodwe-tou-cost-card.js").read_text(encoding="utf-8")
    # The inverter's own today counters must never drive the cost.
    assert "today_energy_import" not in text
    assert "today_energy_export" not in text
    assert "_energyFromTodayCounters" not in text
    # A total that has no statistics can be rebuilt from high/low tariffs.
    assert 'TARIFF_SUFFIXES = ["tariff_1", "tariff_2"]' in text
    assert "_sideKwh" in text
    assert "_energyStatisticIds" in text
    # Last resort is the P1 lifetime total minus its midnight history value.
    assert "history/history_during_period" in text
    assert "_energyFromP1Totals" in text


def test_cost_card_prices_imports_all_in_and_exports_wholesale() -> None:
    """Taxes and fees are added to imports, not earned back on exports."""
    text = (_WWW / "goodwe-tou-cost-card.js").read_text(encoding="utf-8")
    assert "all_in_price_entity" in text
    assert "all_in_average_entity" in text
    assert "import_price_adder" in text
    assert "_importPriceAt" in text
    assert "_exportPriceAt" in text
    assert "importAdder" in text
    # Priced all-in when the Tibber forecast exists, else market + adder.
    assert "this._priceAt(date) + this.importAdder()" in text
    assert "return this._priceAt(date);" in text
    assert 'taxes: "incl. {value}/kWh heffingen"' in text
    assert 'taxes: "incl. {value}/kWh taxes & fees"' in text


def test_planner_card_notifies_the_cost_card_of_window_edits() -> None:
    """Planner edits are visible to the cost card before Toepassen."""
    text = (_WWW / "goodwe-tou-planner-card.js").read_text(encoding="utf-8")
    assert "_notifyCostCard" in text
    assert "goodwe-tou-windows-changed" in text
    assert "CustomEvent" in text


def test_dashboard_opens_with_the_cost_card() -> None:
    """The example dashboard puts the cost card first."""
    text = (_ROOT / "dashboards" / "goodwe_tou.yaml").read_text(encoding="utf-8")
    cost_at = text.index("custom:goodwe-tou-cost-card")
    overview_at = text.index("custom:goodwe-tou-card", cost_at)
    assert cost_at < overview_at
    first_card = text.index("cards:")
    assert text.index("custom:goodwe-tou-cost-card", first_card) < text.index(
        "custom:goodwe-tou-card", first_card
    )
    # The cost card prices imports all-in; the solar bar reads P1 history.
    assert "all_in_average_entity: sensor.akkerv5_electricity_price" in text
    assert "import_history_entity: sensor.p1_meter_energy_import" in text
    assert "export_history_entity: sensor.p1_meter_energy_export" in text
    assert "today_import_entity" not in text
    assert "import_history_entity: sensor.today_energy_import" not in text


def test_cost_card_shows_large_battery_and_grid_direction_symbols() -> None:
    """The cost card has a battery and a grid symbol whose colour is the state."""
    text = (_WWW / "goodwe-tou-cost-card.js").read_text(encoding="utf-8")
    assert 'BATTERY_POWER_DEFAULT = "sensor.battery_power"' in text
    assert 'GRID_POWER_DEFAULT = "sensor.meter_active_power_total"' in text
    assert "_powerInfo" in text
    assert "invert_grid" in text
    assert "power_idle_w" in text
    # Battery: grey idle, red charging, green discharging.
    assert ".ico.battery.charge { color: #dc2626; }" in text
    assert ".ico.battery.discharge { color: #16a34a; }" in text
    # Grid: grey idle, purple importing, blue exporting.
    assert ".ico.grid.import { color: #9333ea; }" in text
    assert ".ico.grid.export { color: #2563eb; }" in text
    assert "idle { color: var(--disabled-text-color" in text
    # Both languages name the states.
    for key in ("batCharge", "batDischarge", "gridImport", "gridExport"):
        assert text.count(f"{key}:") == 2, f"{key} must exist in nl and en"
    # A battery fill follows the state of charge.
    assert 'id="batFill"' in text
    assert "battery_state_of_charge" in text
    # Named placeholders in the translations are filled in by _t().
    assert "value.replace(/\\{(\\w+)\\}/g" in text
    assert "usedSub.textContent" in text

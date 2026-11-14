"""Shared test configuration.

The integration's pure-logic modules (``const``, ``registers``, ``goodwe_udp``)
do not import Home Assistant, so they are loaded as a package without installing
Home Assistant. The integration-level tests are skipped unless
``pytest-homeassistant-custom-component`` is installed.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PKG_DIR = _ROOT / "custom_components" / "goodwe_tou"
_PKG = "goodwe_tou_under_test"

# Make ``custom_components.goodwe_tou`` importable for the HA-level tests.
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def load(module: str):
    """Import ``custom_components/goodwe_tou/<module>.py`` standalone."""
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PKG_DIR)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(f"{_PKG}.{module}")


try:  # pragma: no cover - depends on the test environment
    import pytest_homeassistant_custom_component  # noqa: F401

    _HAS_HA_TESTS = True
except ImportError:  # pragma: no cover
    _HAS_HA_TESTS = False


if _HAS_HA_TESTS:  # pragma: no cover - depends on the test environment

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Enable loading of custom integrations in every test."""
        yield

"""Unit tests for the integration's run-time translations (pure logic)."""

from __future__ import annotations

import datetime as dt

from conftest import load

i18n = load("i18n")


def test_resolve_language_maps_variants():
    """Any Dutch or English variant maps onto a language we ship."""
    assert i18n.resolve_language("nl") == "nl"
    assert i18n.resolve_language("nl-NL") == "nl"
    assert i18n.resolve_language("en") == "en"
    assert i18n.resolve_language("en-GB") == "en"
    # Anything unknown (or empty) falls back to the default.
    assert i18n.resolve_language("de") == i18n.DEFAULT_LANGUAGE
    assert i18n.resolve_language(None) == i18n.DEFAULT_LANGUAGE


def test_translate_fills_placeholders_per_language():
    assert i18n.translate("nl", "entity.window_start", n=2) == "Venster 2 start"
    assert i18n.translate("en", "entity.window_start", n=2) == "Window 2 start"
    assert i18n.translate("en", "day.today") == "today"
    assert i18n.translate("nl", "day.tomorrow") == "morgen"


def test_translate_falls_back_to_the_key():
    """A missing translation never breaks the integration."""
    assert i18n.translate("en", "does.not.exist") == "does.not.exist"


def test_every_dutch_key_has_an_english_translation():
    nl = set(i18n.CATALOG["nl"])
    en = set(i18n.CATALOG["en"])
    assert nl == en


def test_weekday_name_per_language():
    assert i18n.weekday_name("nl", 0) == "maandag"
    assert i18n.weekday_name("en", 0) == "Monday"
    assert i18n.weekday_name("en", 5) == "Saturday"


def test_day_label_names_today_and_tomorrow():
    today = dt.date(2026, 11, 16)  # a Monday
    assert i18n.day_label("nl", today, today) == "vandaag"
    assert i18n.day_label("en", today + dt.timedelta(days=1), today) == "tomorrow"
    assert i18n.day_label("en", today - dt.timedelta(days=1), today) == "yesterday"
    # Anything further out falls back to the weekday name.
    assert i18n.day_label("en", today + dt.timedelta(days=5), today) == "Saturday"

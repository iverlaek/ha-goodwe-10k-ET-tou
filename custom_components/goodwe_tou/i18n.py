"""Translations for the text the integration generates itself.

Home Assistant already translates the config/options dialogs through
``strings.json`` and ``translations/<lang>.json``. This module covers the text
the *integration* produces at run time: the planner's reasoning and notes, the
activity-log entries, the entity names and the messages the cards show. The
language is picked when the integration is installed (and can be changed in the
options), so one instance can speak Dutch or English.

Everything here is pure (no Home Assistant imports) so it is easy to test.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

#: The language used when nothing else is known.
DEFAULT_LANGUAGE = "nl"

#: The languages the integration speaks, in menu order.
LANGUAGES = ("nl", "en")

_WEEKDAYS: dict[str, tuple[str, ...]] = {
    "nl": (
        "maandag",
        "dinsdag",
        "woensdag",
        "donderdag",
        "vrijdag",
        "zaterdag",
        "zondag",
    ),
    "en": (
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ),
}

#: Every string the integration builds at run time, per language. ``{name}``
#: placeholders are filled in with :func:`translate`.
CATALOG: dict[str, dict[str, str]] = {
    "nl": {
        # -- entity names --------------------------------------------------
        "entity.activity_log": "Activiteitenlogboek",
        "entity.min_cell_voltage": "Laagste celspanning",
        "entity.plan": "Plan",
        "entity.prices": "Stroomprijzen",
        "entity.eco_mode": "ECO-modus",
        "entity.auto_replan": "Auto-herberekening",
        "entity.window_power": "Venster {n} vermogen",
        "entity.window_days": "Venster {n} dagen",
        "entity.window_start": "Venster {n} start",
        "entity.window_stop": "Venster {n} stop",
        "entity.read": "Lezen van omvormer",
        "entity.apply": "Toepassen op omvormer",
        "entity.cheapest_hours": "Optimale prijzen berekenen",
        "entity.auto_eco": "Auto/ECO-modus",
        "entity.manual_override": "Handmatige override",
        "entity.manual_charge": "Handmatig laden",
        "entity.manual_discharge": "Handmatig ontladen",
        # -- days and modes ------------------------------------------------
        "day.today": "vandaag",
        "day.tomorrow": "morgen",
        "day.yesterday": "gisteren",
        "mode.charge": "laden",
        "mode.discharge": "ontladen",
        # -- planner notes -------------------------------------------------
        "plan.no_prices": "Geen prijzen beschikbaar.",
        "plan.no_future_prices": "Geen toekomstige prijzen beschikbaar.",
        "plan.summary_empty": "Geen vensters nodig.",
        "plan.overview": "Vensterplan \u2014 {parts}.",
        "plan.overview_day": "{day}: {items}",
        "plan.overview_item": "{verb} {start}\u2013{stop}",
        "plan.skipped_charge": (
            "Overnacht laden overgeslagen: de goedkoopste uren (gemiddeld "
            "\u20ac{charge:.3f}/kWh) zijn, met de laad-/ontlaadverliezen "
            "(RTE {rte:.0f}%), niet goedkoper dan het duurste uur "
            "(gemiddeld \u20ac{reference:.3f}/kWh)."
        ),
        "plan.already_target": "Batterij staat al op het doel ({soc:.0f}%).",
        "plan.charge_note": (
            "Batterij {soc:.0f}% \u2192 doel {target:.0f}%. Met het verbruik "
            "van {standby:.1f} kW tot en tijdens de laaduren en de "
            "laadverliezen (RTE {rte:.0f}%) is ongeveer "
            "{kwh:.1f} kWh nodig."
        ),
        "plan.no_cheap_hours": (
            "Batterij {soc:.0f}%; er zijn nu geen goedkope uren om naar het doel "
            "{target:.0f}% te laden."
        ),
        "plan.pv_note": (
            "PV-prognose levert nog {pv:.1f} kWh voor niets. Dat verlaagt "
            "je inkoop overdag, dus laden we de batterij 's nachts goedkoop vol "
            "in plaats van later duur in te kopen."
        ),
        "plan.export_cap_note": (
            "Ontladen is per dag beperkt tot {cap:.1f} kWh (max "
            "{hours:.1f} uur terugleveren op vol vermogen per dag) "
            "zodat de batterij niet te vroeg leegloopt; er is {avail:.1f} kWh "
            "beschikbaar boven {min_soc:.0f}%."
        ),
        "plan.available_note": (
            "Er is {avail:.1f} kWh beschikbaar om te ontladen boven "
            "{min_soc:.0f}%."
        ),
        "plan.standby_note": (
            "Het huis gebruikt ongeveer {standby:.1f} kW standby; dat "
            "neemt de batterij in ECO-modus zelf voor haar rekening."
        ),
        "plan.charge_reason": (
            "Goedkoopste {minutes:.0f} min van de komende periode "
            "(gemiddeld \u20ac{avg:.3f}/kWh). Met {power:.0f} kW laad je hier "
            "{kwh:.1f} kWh in de batterij (RTE {rte:.0f}%)."
        ),
        "plan.discharge_reason": (
            "Duurste {minutes:.0f} min van {when} "
            "(gemiddeld \u20ac{avg:.3f}/kWh). Met {power:.0f} kW ontlaad je hier "
            "{kwh:.1f} kWh; dat levert ongeveer \u20ac{revenue:.2f} op."
        ),
        "plan.coming_period": "de komende periode",
        "plan.no_windows": (
            "Geen vensters nodig: de batterij is op doel en er valt niets te "
            "ontladen."
        ),
        # -- activity log / automation ------------------------------------
        "log.auto_eco": (
            "Auto/ECO-modus ingeschakeld: de omvormer staat in ECO en de "
            "automatische herberekening houdt het plan zelf actueel."
        ),
        "log.manual_override": (
            "Handmatige override: automatische herberekening gepauzeerd. De "
            "vensters op de omvormer blijven staan; kies Auto/ECO-modus om de "
            "automatisering te hervatten."
        ),
        "log.guard": (
            "Automatisering gestopt: laagste celspanning {voltage} mV is onder de "
            "drempel van {threshold} mV. Laad de batterij bij via de SEMS/SolarGo "
            "app en zet de automatisering daarna weer aan."
        ),
        "log.manual_charge": (
            "Handmatig laden: venster {n} laadt nu op {power}%. Start, stop en "
            "dagen blijven ongewijzigd."
        ),
        "log.manual_discharge": (
            "Handmatig ontladen: venster {n} ontlaadt nu op {power}%. Start, stop "
            "en dagen blijven ongewijzigd."
        ),
        "log.manual_off": "Handmatig laden/ontladen gestopt: venster {n} staat uit.",
        "log.replan_changed": "Gewijzigd: {changes}.",
        "log.replan_benefit": "Verwacht voordeel: \u20ac{amount:.2f}.",
        "log.window_change_mode": (
            "venster {n}: {old_mode} {old_power}% \u2192 {new_mode} {new_power}%"
        ),
        "log.window_change_time": (
            "venster {n}: {old_start}\u2013{old_stop} \u2192 "
            "{new_start}\u2013{new_stop}"
        ),
        # -- errors --------------------------------------------------------
        "error.no_entry": "Er is geen GoodWe Time-of-Use-configuratie geladen",
        "error.write": "Kon niet naar de omvormer schrijven: {error}",
        "error.read": "Kon niet van de omvormer lezen: {error}",
        "error.verify": "Kon de schrijfactie niet controleren: {error}",
        "error.window_missing": "Venster {n} is nog niet beschikbaar",
        "error.readback": (
            "De omvormer heeft de wijziging niet overgenomen (teruglezen klopt "
            "niet na {attempts} pogingen). Mogelijk pollt een andere integratie "
            "tegelijk met de dongle."
        ),
        "error.price_entity_missing": "Prijssensor {entity} is niet gevonden",
        "error.price_entity_no_forecast": (
            "Prijssensor {entity} heeft geen bruikbare prijsverwachting "
            "(verwacht een 'prices'-, 'prices_today'- of 'raw_today'-attribuut)"
        ),
        "error.tibber_not_fetched": (
            "De Tibber-prijzen zijn nog niet opgehaald. Controleer of de "
            "Tibber-integratie is ingesteld en probeer het zo opnieuw."
        ),
        "error.tibber_no_values": (
            "De Tibber-prijzen bevatten geen bruikbare waarden"
        ),
    },
    "en": {
        # -- entity names --------------------------------------------------
        "entity.activity_log": "Activity log",
        "entity.min_cell_voltage": "Lowest cell voltage",
        "entity.plan": "Plan",
        "entity.prices": "Energy prices",
        "entity.eco_mode": "ECO mode",
        "entity.auto_replan": "Auto re-check",
        "entity.window_power": "Window {n} power",
        "entity.window_days": "Window {n} days",
        "entity.window_start": "Window {n} start",
        "entity.window_stop": "Window {n} stop",
        "entity.read": "Read from inverter",
        "entity.apply": "Apply to inverter",
        "entity.cheapest_hours": "Set cheapest hours",
        "entity.auto_eco": "Auto/ECO mode",
        "entity.manual_override": "Manual override",
        "entity.manual_charge": "Manual charge",
        "entity.manual_discharge": "Manual discharge",
        # -- days and modes ------------------------------------------------
        "day.today": "today",
        "day.tomorrow": "tomorrow",
        "day.yesterday": "yesterday",
        "mode.charge": "charging",
        "mode.discharge": "discharging",
        # -- planner notes -------------------------------------------------
        "plan.no_prices": "No prices available.",
        "plan.no_future_prices": "No future prices available.",
        "plan.summary_empty": "No windows needed.",
        "plan.overview": "Window plan \u2014 {parts}.",
        "plan.overview_day": "{day}: {items}",
        "plan.overview_item": "{verb} {start}\u2013{stop}",
        "plan.skipped_charge": (
            "Overnight charging skipped: the cheapest hours (average "
            "\u20ac{charge:.3f}/kWh) are, once the charge/discharge losses are "
            "counted (RTE {rte:.0f}%), not cheaper than the most expensive hour "
            "(average \u20ac{reference:.3f}/kWh)."
        ),
        "plan.already_target": "The battery is already at the target ({soc:.0f}%).",
        "plan.charge_note": (
            "Battery {soc:.0f}% \u2192 target {target:.0f}%. With the "
            "{standby:.1f} kW the house draws up to and during the charging "
            "hours and the charging losses (RTE {rte:.0f}%), about "
            "{kwh:.1f} kWh is needed."
        ),
        "plan.no_cheap_hours": (
            "Battery {soc:.0f}%; there are no cheap hours right now to charge "
            "towards the {target:.0f}% target."
        ),
        "plan.pv_note": (
            "The PV forecast still delivers {pv:.1f} kWh for free. That lowers "
            "what you buy during the day, so we charge the battery cheaply at "
            "night instead of buying expensively later."
        ),
        "plan.export_cap_note": (
            "Discharging is capped at {cap:.1f} kWh per day (at most "
            "{hours:.1f} hours of export at full power per day) so the battery "
            "does not run flat too early; {avail:.1f} kWh is available above "
            "{min_soc:.0f}%."
        ),
        "plan.available_note": (
            "{avail:.1f} kWh is available to discharge above {min_soc:.0f}%."
        ),
        "plan.standby_note": (
            "The house draws about {standby:.1f} kW standby; the battery covers "
            "that itself in ECO mode."
        ),
        "plan.charge_reason": (
            "Cheapest {minutes:.0f} min of the coming period "
            "(average \u20ac{avg:.3f}/kWh). At {power:.0f} kW this charges "
            "{kwh:.1f} kWh into the battery (RTE {rte:.0f}%)."
        ),
        "plan.discharge_reason": (
            "Most expensive {minutes:.0f} min of {when} "
            "(average \u20ac{avg:.3f}/kWh). At {power:.0f} kW this discharges "
            "{kwh:.1f} kWh; that yields about \u20ac{revenue:.2f}."
        ),
        "plan.coming_period": "the coming period",
        "plan.no_windows": (
            "No windows needed: the battery is at target and there is nothing to "
            "discharge."
        ),
        # -- activity log / automation ------------------------------------
        "log.auto_eco": (
            "Auto/ECO mode enabled: the inverter runs in ECO and the automatic "
            "re-check keeps the plan up to date on its own."
        ),
        "log.manual_override": (
            "Manual override: automatic re-check paused. The windows on the "
            "inverter stay as they are; pick Auto/ECO mode to resume the "
            "automation."
        ),
        "log.guard": (
            "Automation stopped: the lowest cell voltage {voltage} mV is below "
            "the {threshold} mV threshold. Charge the battery via the SEMS/"
            "SolarGo app and then switch the automation back on."
        ),
        "log.manual_charge": (
            "Manual charge: window {n} charges now at {power}%. Start, stop and "
            "days are unchanged."
        ),
        "log.manual_discharge": (
            "Manual discharge: window {n} discharges now at {power}%. Start, stop "
            "and days are unchanged."
        ),
        "log.manual_off": "Manual charge/discharge stopped: window {n} is off.",
        "log.replan_changed": "Changed: {changes}.",
        "log.replan_benefit": "Expected benefit: \u20ac{amount:.2f}.",
        "log.window_change_mode": (
            "window {n}: {old_mode} {old_power}% \u2192 {new_mode} {new_power}%"
        ),
        "log.window_change_time": (
            "window {n}: {old_start}\u2013{old_stop} \u2192 "
            "{new_start}\u2013{new_stop}"
        ),
        # -- errors --------------------------------------------------------
        "error.no_entry": "No GoodWe Time-of-Use entry is loaded",
        "error.write": "Could not write to the inverter: {error}",
        "error.read": "Could not read from the inverter: {error}",
        "error.verify": "Could not verify the write: {error}",
        "error.window_missing": "Window {n} is not available yet",
        "error.readback": (
            "The inverter did not accept the change (read-back mismatch after "
            "{attempts} attempts). Another integration may be polling the dongle "
            "at the same time."
        ),
        "error.price_entity_missing": "Price entity {entity} was not found",
        "error.price_entity_no_forecast": (
            "Price entity {entity} does not expose a usable price forecast "
            "(expected a 'prices', 'prices_today' or 'raw_today' attribute)"
        ),
        "error.tibber_not_fetched": (
            "The Tibber prices have not been fetched yet. Check that the Tibber "
            "integration is set up and try again."
        ),
        "error.tibber_no_values": (
            "The Tibber prices contain no usable values"
        ),
    },
}


def resolve_language(value: object) -> str:
    """Normalise a language code to one the catalog knows.

    Any Dutch or English variant (``nl``, ``nl-NL``, ``en-GB``, ...) maps onto
    the two languages we ship; anything else falls back to the default.
    """
    code = str(value or "").strip().lower()
    if code.startswith("en"):
        return "en"
    if code.startswith("nl"):
        return "nl"
    return DEFAULT_LANGUAGE


def translate(lang: str, key: str, **params: Any) -> str:
    """Look up ``key`` for ``lang`` and fill in ``params``.

    Falls back to the default language and, failing that, to the key itself, so
    a missing translation never breaks the integration.
    """
    code = resolve_language(lang)
    catalog = CATALOG.get(code, CATALOG[DEFAULT_LANGUAGE])
    template = catalog.get(key)
    if template is None:
        template = CATALOG[DEFAULT_LANGUAGE].get(key, key)
    if not params:
        return template
    return template.format(**params)


def weekday_name(lang: str, index: int) -> str:
    """The (lower-case where natural) name of weekday ``index`` (Monday = 0)."""
    names = _WEEKDAYS.get(resolve_language(lang), _WEEKDAYS[DEFAULT_LANGUAGE])
    return names[index % 7]


def day_label(lang: str, day: dt.date, today: dt.date) -> str:
    """A short label for ``day`` relative to ``today``.

    The price horizon only spans today and tomorrow, so "today"/"tomorrow" read
    more naturally than a weekday name. Anything further out (or in the past)
    falls back to the weekday.
    """
    delta = (day - today).days
    if delta == 0:
        return translate(lang, "day.today")
    if delta == 1:
        return translate(lang, "day.tomorrow")
    if delta == -1:
        return translate(lang, "day.yesterday")
    return weekday_name(lang, day.weekday())

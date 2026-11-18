"""Set up the integration inside a real Home Assistant test instance.

Skipped automatically when ``pytest-homeassistant-custom-component`` is not
installed (for example in the lightweight CI job).
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.goodwe_tou import _recheck_minutes
from custom_components.goodwe_tou.coordinator import next_retry_delay
from custom_components.goodwe_tou.const import (
    CONF_ADAPTIVE_LOAD,
    CONF_CHARGE_WINDOWS,
    CONF_DISCHARGE_WINDOWS,
    CONF_EXPORT_MIN_SPREAD,
    CONF_LANGUAGE,
    CONF_LOG_SIZE,
    CONF_MIN_CELL_VOLTAGE,
    CONF_PRICE_SOURCE,
    CONF_PROFILE_ENABLED,
    CONF_RECHECK_MINUTES,
    CONF_STANDBY_KW,
    CONF_TEMP_COEFF,
    CONF_TIBBER_PRICES,
    CONF_WINDOW_POWER,
    DOMAIN,
)
from custom_components.goodwe_tou.goodwe_udp import GoodweRejectedError, GoodweUdpClient
from custom_components.goodwe_tou.registers import TouWindow

BASE = 47515

# window 1 = 04:00-06:00 charge 100% every day
# window 2 = 12:30-15:30 charge 30% (switch off)
# window 3 = 10:45-13:45 off, saturday
# window 4 = 22:15-23:00 off, every day
TOU_REGISTERS = [
    0x0400,
    0x0600,
    0xFF9C,
    0xFF7F,
    0x0C1E,
    0x0F1E,
    0xFFE2,
    0x007F,
    0x0A2D,
    0x0D2D,
    0x0000,
    0x0040,
    0x160F,
    0x1700,
    0x0000,
    0x007F,
]


@pytest.fixture
def store() -> list[int]:
    return list(TOU_REGISTERS)


def _read_from(
    store: list[int], work_mode: int = 3, export=(1, 10000), cells=(3300, 3300)
):
    def _read(offset: int, count: int):
        if offset == 47000:
            return [work_mode]
        if offset == 47509:
            assert count == 2
            return list(export)
        if offset == 37022:
            assert count == 2
            return list(cells)
        if offset == BASE:
            assert count == 16
            return list(store)
        raise AssertionError(f"unexpected read at {offset}")

    return _read


def _write_to(store: list[int]):
    def _write(register: int, value: int) -> None:
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    return _write


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.20.13", "port": 8899, "comm_addr": 247},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def fast_retries():
    """Drop the pause between re-sends so retry tests run instantly."""
    with patch("custom_components.goodwe_tou.coordinator._RETRY_DELAY", 0):
        yield


def _entry_with_options(hass: HomeAssistant, **options: object) -> MockConfigEntry:
    """A config entry whose addon options have been set by the user."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.20.13", "port": 8899, "comm_addr": 247},
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_setup_creates_entities(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    ids = sorted(hass.states.async_entity_ids())
    assert "switch.goodwe_time_of_use_eco_mode" in ids
    assert "button.goodwe_time_of_use_read_from_inverter" in ids
    assert "button.goodwe_time_of_use_apply_to_inverter" in ids
    assert "button.goodwe_time_of_use_set_cheapest_hours" in ids
    assert "button.goodwe_time_of_use_manual_charge" in ids
    assert "button.goodwe_time_of_use_manual_discharge" in ids

    start = hass.states.get("time.goodwe_time_of_use_window_1_start")
    stop = hass.states.get("time.goodwe_time_of_use_window_1_stop")
    power = hass.states.get("number.goodwe_time_of_use_window_1_power")
    days = hass.states.get("text.goodwe_time_of_use_window_1_days")
    eco = hass.states.get("switch.goodwe_time_of_use_eco_mode")

    assert start.state == "04:00:00"
    assert stop.state == "06:00:00"
    assert power.state == "-100.0"
    assert days.state == "sun,mon,tue,wed,thu,fri,sat"
    assert eco.state == "on"

    # The default language is Dutch: translated names, pinned ids.
    activity = hass.states.get("sensor.goodwe_time_of_use_activity_log")
    assert "Activiteitenlogboek" in activity.name
    assert activity.attributes["language"] == "nl"

    # no more select entities
    assert not any(i.startswith("select.goodwe_time_of_use") for i in ids)

    # window 2 has the switch off -> signed power 0
    assert hass.states.get("number.goodwe_time_of_use_window_2_power").state == "0.0"
    # window 3 runs on saturday only
    assert hass.states.get("text.goodwe_time_of_use_window_3_days").state == "sat"


@pytest.mark.asyncio
async def test_apply_writes_registers(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    writes: list[tuple[int, int]] = []

    def _write(register: int, value: int) -> None:
        writes.append((register, value))
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        writes.clear()  # the setup itself already pushed the export limit

        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.goodwe_time_of_use_apply_to_inverter"},
            blocking=True,
        )
        await hass.async_block_till_done()

    mapping = dict(writes)
    # window 1 start 04:00 -> 0x0400, stop 06:00 -> 0x0600
    assert mapping[47515] == 0x0400
    assert mapping[47516] == 0x0600
    # window 1 charge 100% -> signed -100
    assert mapping[47517] == 0xFF9C
    assert mapping[47518] == 0xFF7F
    # window 3, saturday only, off -> switch off, mask 0x40
    assert mapping[47526] == 0x0040
    # the grid export limit travels with the plan, so discharge can export
    assert mapping[47509] == 1
    assert mapping[47510] == 10000
    assert len(writes) == 18


@pytest.mark.asyncio
async def test_setup_repairs_a_blocked_export_limit(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """A Feed Power limit of 0 silently stops every discharge window."""
    writes: list[tuple[int, int]] = []

    def _write(register: int, value: int) -> None:
        writes.append((register, value))

    with (
        patch.object(
            GoodweUdpClient,
            "read_registers",
            side_effect=_read_from(store, export=(1, 0)),
        ),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    mapping = dict(writes)
    assert mapping[47509] == 1
    assert mapping[47510] == 10000


@pytest.mark.asyncio
async def test_setup_leaves_a_healthy_export_limit_alone(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """A restart must not write to the dongle when nothing needs changing."""
    writes: list[tuple[int, int]] = []

    def _write(register: int, value: int) -> None:
        writes.append((register, value))

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert writes == []


@pytest.mark.asyncio
async def test_set_windows_service_writes_a_whole_plan(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The planner card applies a whole plan through one verified service call."""
    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write_to(store)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            DOMAIN,
            "set_windows",
            {
                "windows": [
                    {"start": "02:00", "stop": "05:00", "power": -80, "days": "mon"},
                    {"start": "18:30", "stop": "20:00", "power": 50, "days": "mon"},
                ],
                "reason": "Inkoopprijs is met €0.050/kWh verhoogd voor batterijkosten.",
            },
            blocking=True,
        )
        await hass.async_block_till_done()

    assert hass.states.get("time.goodwe_time_of_use_window_1_start").state == "02:00:00"
    assert hass.states.get("time.goodwe_time_of_use_window_1_stop").state == "05:00:00"
    assert hass.states.get("number.goodwe_time_of_use_window_1_power").state == "-80.0"
    assert hass.states.get("text.goodwe_time_of_use_window_1_days").state == "mon"

    assert hass.states.get("time.goodwe_time_of_use_window_2_start").state == "18:30:00"
    assert hass.states.get("number.goodwe_time_of_use_window_2_power").state == "50.0"

    # the windows that were not given are turned off
    assert hass.states.get("number.goodwe_time_of_use_window_3_power").state == "0.0"
    assert hass.states.get("switch.goodwe_time_of_use_eco_mode").state == "on"

    # Applying a plan logs the explanation, so the activity log shows the
    # calculation behind the windows (the planner card passes the plan summary).
    entry_log = hass.states.get("sensor.goodwe_time_of_use_activity_log").attributes[
        "entries"
    ][0]
    assert entry_log["action"] == "set_windows"
    assert "batterijkosten" in entry_log["reason"]


@pytest.mark.asyncio
async def test_set_time_writes_single_register(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    writes: list[tuple[int, int]] = []

    def _write(register: int, value: int) -> None:
        writes.append((register, value))
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        writes.clear()  # ignore the export-limit write done during setup

        await hass.services.async_call(
            "time",
            "set_value",
            {
                "entity_id": "time.goodwe_time_of_use_window_1_start",
                "time": "05:15:00",
            },
            blocking=True,
        )
        await hass.async_block_till_done()

    assert writes == [(47515, 5 * 256 + 15)]
    assert hass.states.get("time.goodwe_time_of_use_window_1_start").state == "05:15:00"


@pytest.mark.asyncio
async def test_set_signed_power_enables_window(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    writes: list[tuple[int, int]] = []

    def _write(register: int, value: int) -> None:
        writes.append((register, value))
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": "number.goodwe_time_of_use_window_2_power",
                "value": 60,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

    mapping = dict(writes)
    # +60 discharges: power register 60, switch on with the existing days mask
    assert mapping[47519 + 2] == 60
    assert mapping[47519 + 3] == 0xFF7F
    assert hass.states.get("number.goodwe_time_of_use_window_2_power").state == "60.0"


@pytest.mark.asyncio
async def test_read_ignores_a_foreign_frame(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """A frame answering another client's same-length read must not be trusted."""
    foreign = [
        0x1A0B, 0x110B, 0x2935, 0x07AC, 0x0002, 0x0000, 0x0026, 0x0796,
        0x0001, 0x0000, 0x0012, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    ]
    calls = {"n": 0}

    def _read(offset: int, count: int):
        if offset == 47000:
            return [3]
        if offset == 47509:
            return [1, 10000]
        if offset == 37022:
            return [3300, 3300]
        assert offset == BASE and count == 16
        calls["n"] += 1
        return list(foreign) if calls["n"] == 1 else list(store)

    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert calls["n"] >= 2
    assert (
        hass.states.get("time.goodwe_time_of_use_window_1_start").state == "04:00:00"
    )
    assert (
        hass.states.get("number.goodwe_time_of_use_window_1_power").state == "-100.0"
    )


@pytest.mark.asyncio
async def test_read_retries_a_foreign_exception(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """Another client's 'illegal data address' must not fail our read."""
    calls = {"n": 0}

    def _read(offset: int, count: int):
        if offset == 47000:
            calls["n"] += 1
            if calls["n"] == 1:
                raise GoodweRejectedError("illegal data address (2)")
            return [3]
        if offset == 47509:
            return [1, 10000]
        if offset == 37022:
            return [3300, 3300]
        assert offset == BASE and count == 16
        return list(store)

    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert calls["n"] >= 2
    assert hass.states.get("switch.goodwe_time_of_use_eco_mode").state == "on"


@pytest.mark.asyncio
async def test_set_time_raises_when_inverter_ignores_it(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int], fast_retries
):
    """An unverified single-register write must surface instead of silently reverting."""

    def _ignored(register: int, value: int) -> None:
        pass  # the inverter silently drops the write

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_ignored),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "time",
                "set_value",
                {
                    "entity_id": "time.goodwe_time_of_use_window_1_start",
                    "time": "05:15:00",
                },
                blocking=True,
            )
        await hass.async_block_till_done()

    # The write was sent three times before giving up, which the log and the
    # retry counter both record.
    entry_log = hass.states.get("sensor.goodwe_time_of_use_activity_log").attributes[
        "entries"
    ][0]
    assert entry_log["result"] == "error"
    assert entry_log["attempts"] == 3
    retries = hass.states.get("sensor.goodwe_time_of_use_herpogingen")
    # Three attempts means two re-sends, which is what the counter tallies.
    assert retries.state == "2"
    assert retries.attributes["last_attempts"] == 3


@pytest.mark.asyncio
async def test_cheapest_hours_service(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    now = dt.datetime.now(dt.UTC).astimezone()
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    prices = [
        {
            "time": (base + dt.timedelta(minutes=15 * i)).isoformat(),
            "price": i / 100.0,
        }
        for i in range(96)
    ]
    hass.states.async_set(
        "sensor.average_electricity_price",
        "0.1",
        {"prices": prices, "unit_of_measurement": "€/kWh"},
    )
    hass.states.async_set("sensor.battery_state_of_charge", "50")
    hass.states.async_set("sensor.energy_production_today_remaining", "0")

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write_to(store)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(DOMAIN, "set_cheapest_hours", {}, blocking=True)
        await hass.async_block_till_done()

    powers = [
        float(hass.states.get(f"number.goodwe_time_of_use_window_{n}_power").state)
        for n in range(1, 5)
    ]
    assert any(p < 0 for p in powers), powers
    assert any(p > 0 for p in powers), powers

    # The manual "set cheapest hours" write logs the plan's explanation, which
    # includes the battery-cost and export-spread calculation.
    entry_log = hass.states.get("sensor.goodwe_time_of_use_activity_log").attributes[
        "entries"
    ][0]
    assert entry_log["action"] == "cheapest_hours"
    assert "batterijkosten" in entry_log["reason"]
    assert "spread" in entry_log["reason"]


@pytest.mark.asyncio
async def test_calculate_cheapest_hours_service_returns_without_writing(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The planner card fills its rows from this service without touching the inverter."""
    now = dt.datetime.now(dt.UTC).astimezone()
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    prices = [
        {
            "time": (base + dt.timedelta(minutes=15 * i)).isoformat(),
            "price": i / 100.0,
        }
        for i in range(96)
    ]
    hass.states.async_set(
        "sensor.average_electricity_price",
        "0.1",
        {"prices": prices, "unit_of_measurement": "€/kWh"},
    )
    hass.states.async_set("sensor.battery_state_of_charge", "50")
    hass.states.async_set("sensor.energy_production_today_remaining", "0")

    writes: list[tuple[int, int]] = []

    def _write(register: int, value: int) -> None:
        writes.append((register, value))
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        writes.clear()  # ignore the export-limit write done during setup

        response = await hass.services.async_call(
            DOMAIN,
            "calculate_cheapest_hours",
            {},
            blocking=True,
            return_response=True,
        )
        await hass.async_block_till_done()

    assert response["windows"], response
    first = response["windows"][0]
    assert set(first) >= {"start", "stop", "power", "days", "mode", "enabled"}
    assert any(window["power"] < 0 for window in response["windows"])
    assert any(window["power"] > 0 for window in response["windows"])

    # Nothing was written and the entities still show the inverter's own schedule.
    assert writes == []
    assert (
        hass.states.get("number.goodwe_time_of_use_window_1_power").state == "-100.0"
    )


@pytest.mark.asyncio
async def test_forecast_soc_service_returns_points(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The week card draws its forecast part from this service."""
    hass.states.async_set("sensor.battery_state_of_charge", "50")

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write_to(store)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        response = await hass.services.async_call(
            DOMAIN,
            "forecast_soc",
            {},
            blocking=True,
            return_response=True,
        )
        await hass.async_block_till_done()

    points = response["points"]
    assert len(points) > 1
    assert set(points[0]) == {"time", "soc"}
    # It starts at the battery's current state of charge.
    assert points[0]["soc"] == 50.0
    times = [point["time"] for point in points]
    assert times == sorted(times)
    assert all(0 <= point["soc"] <= 100 for point in points)
    # Discharging stops at the configured minimum state of charge.
    assert min(point["soc"] for point in points) >= 20.0


@pytest.mark.asyncio
async def test_activity_log_records_a_write(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write_to(store)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.goodwe_time_of_use_window_1_power", "value": 50},
            blocking=True,
        )
        await hass.async_block_till_done()

    sensor = hass.states.get("sensor.goodwe_time_of_use_activity_log")
    assert sensor is not None
    assert sensor.state == "1"
    assert sensor.attributes["last_action"] == "window_power"
    assert sensor.attributes["last_result"] == "ok"
    assert sensor.attributes["entries"][0]["changes"] == [
        {"field": "window_1_power", "old": -100, "new": 50}
    ]


@pytest.mark.asyncio
async def test_activity_log_records_a_failed_write(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int], fast_retries
):
    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        # A write that never lands: the read-back never matches.
        patch.object(GoodweUdpClient, "write_register", return_value=None),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "set_windows",
                {
                    "windows": [
                        {"start": "01:00", "stop": "02:00", "power": -100},
                        {"start": "03:00", "stop": "04:00", "power": -100},
                    ]
                },
                blocking=True,
            )
        await hass.async_block_till_done()

    sensor = hass.states.get("sensor.goodwe_time_of_use_activity_log")
    assert sensor.attributes["last_result"] == "error"
    entry_log = sensor.attributes["entries"][0]
    assert entry_log["action"] == "set_windows"
    assert entry_log["result"] == "error"
    assert entry_log["error"]
    # All three sends failed, so the badge can say "3x mislukt".
    assert entry_log["attempts"] == 3
    assert hass.states.get("sensor.goodwe_time_of_use_herpogingen").state == "2"


@pytest.mark.asyncio
async def test_write_retries_once_then_succeeds(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int], fast_retries
):
    """A dropped first send is re-sent, and the retry counter records it."""
    calls = {"n": 0}

    def _drop_first(register: int, value: int) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            return  # the overloaded dongle drops the first register
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_drop_first),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            DOMAIN,
            "set_windows",
            {"windows": [{"start": "02:00", "stop": "05:00", "power": -80}]},
            blocking=True,
        )
        await hass.async_block_till_done()

    # The change stuck on the second send.
    assert hass.states.get("time.goodwe_time_of_use_window_1_start").state == "02:00:00"
    entry_log = hass.states.get("sensor.goodwe_time_of_use_activity_log").attributes[
        "entries"
    ][0]
    assert entry_log["result"] == "ok"
    assert entry_log["attempts"] == 2
    retries = hass.states.get("sensor.goodwe_time_of_use_herpogingen")
    assert retries.state == "1"
    assert retries.attributes["total"] == 1
    assert retries.attributes["last_attempts"] == 2


def test_next_retry_delay_is_random_between_the_bounds():
    """The first re-send waits a random time between the configured bounds."""
    with (
        patch("custom_components.goodwe_tou.coordinator._RETRY_MIN", 20.0),
        patch("custom_components.goodwe_tou.coordinator._RETRY_MAX", 90.0),
    ):
        delays = [next_retry_delay() for _ in range(200)]
    assert all(20.0 <= delay <= 90.0 for delay in delays)
    # Not a constant: the wait is actually randomised.
    assert len(set(delays)) > 1


def test_next_retry_delay_grows_by_seven_seconds():
    """Every re-send after the first waits 7 s longer than the previous one."""
    with (
        patch("custom_components.goodwe_tou.coordinator._RETRY_MIN", 20.0),
        patch("custom_components.goodwe_tou.coordinator._RETRY_MAX", 20.0),
    ):
        first = next_retry_delay()
    assert first == 20.0
    assert next_retry_delay(first) == 27.0
    assert next_retry_delay(27.0) == 34.0


@pytest.mark.asyncio
async def test_failed_write_is_retried_after_a_delay(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int], fast_retries
):
    """A write that fails completely is re-sent a minute later, until it sticks.

    The dongle is flaky enough to drop a whole write; instead of leaving the
    inverter misprogrammed, the integration keeps re-sending it, so the log's
    newest entry ends up a success.
    """
    state = {"working": False}

    def _write(register: int, value: int) -> None:
        if not state["working"]:
            return  # the overloaded dongle drops the whole write
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
        patch("custom_components.goodwe_tou.coordinator._RETRY_MIN", 1),
        patch("custom_components.goodwe_tou.coordinator._RETRY_MAX", 1),
        patch("custom_components.goodwe_tou.coordinator._RETRY_STEP", 1),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "set_windows",
                {"windows": [{"start": "02:00", "stop": "05:00", "power": -80}]},
                blocking=True,
            )
        await hass.async_block_till_done()

        # For now the newest entry is the failure, with the attempt count.
        entries = hass.states.get(
            "sensor.goodwe_time_of_use_activity_log"
        ).attributes["entries"]
        assert entries[0]["result"] == "error"
        assert entries[0]["attempts"] == 3
        assert entries[0]["retry"] == 0

        # The dongle recovers; the queued re-send succeeds on the next round.
        state["working"] = True
        async_fire_time_changed(
            hass, dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5)
        )
        await hass.async_block_till_done()

    entries = hass.states.get("sensor.goodwe_time_of_use_activity_log").attributes[
        "entries"
    ]
    # The log ends on the successful re-send, marked as a retry.
    assert entries[0]["result"] == "ok"
    assert entries[0]["retry"] == 1
    assert entries[0]["attempts"] == 1
    assert hass.states.get("time.goodwe_time_of_use_window_1_start").state == "02:00:00"
    # The failed round contributed its two re-sends to the tally.
    assert hass.states.get("sensor.goodwe_time_of_use_herpogingen").state == "2"
    # Nothing is left waiting once the write stuck.
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._pending_writes == {}


@pytest.mark.asyncio
async def test_failed_write_keeps_retrying_until_it_sticks(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int], fast_retries
):
    """Every failed round is logged and queued again, not given up on."""
    state = {"working": False}

    def _write(register: int, value: int) -> None:
        if not state["working"]:
            return
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
        patch("custom_components.goodwe_tou.coordinator._RETRY_MIN", 1),
        patch("custom_components.goodwe_tou.coordinator._RETRY_MAX", 1),
        patch("custom_components.goodwe_tou.coordinator._RETRY_STEP", 1),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "set_windows",
                {"windows": [{"start": "02:00", "stop": "05:00", "power": -80}]},
                blocking=True,
            )
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._pending_writes["set_windows"]["delay"] == 1

        # A second failed round is logged and queued again, waiting a step longer.
        async_fire_time_changed(
            hass, dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5)
        )
        await hass.async_block_till_done()

        entries = hass.states.get(
            "sensor.goodwe_time_of_use_activity_log"
        ).attributes["entries"]
        assert entries[0]["result"] == "error"
        assert entries[0]["retry"] == 1
        assert coordinator._pending_writes["set_windows"]["delay"] == 2

        # The third round finally sticks.
        state["working"] = True
        async_fire_time_changed(
            hass, dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5)
        )
        await hass.async_block_till_done()

    entries = hass.states.get("sensor.goodwe_time_of_use_activity_log").attributes[
        "entries"
    ]
    assert entries[0]["result"] == "ok"
    assert entries[0]["retry"] == 2
    assert entries[1]["result"] == "error"
    assert entries[1]["retry"] == 1
    assert hass.data[DOMAIN][entry.entry_id]._pending_writes == {}


@pytest.mark.asyncio
async def test_manual_override_cancels_a_pending_plan_retry(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int], fast_retries
):
    """A manual choice must win from a plan the automation still wanted to send."""
    state = {"working": False}

    def _write(register: int, value: int) -> None:
        if not state["working"]:
            return
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
        patch("custom_components.goodwe_tou.coordinator._RETRY_MIN", 1),
        patch("custom_components.goodwe_tou.coordinator._RETRY_MAX", 1),
        patch("custom_components.goodwe_tou.coordinator._RETRY_STEP", 1),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "set_windows",
                {"windows": [{"start": "02:00", "stop": "05:00", "power": -80}]},
                blocking=True,
            )
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._pending_writes

        await coordinator.async_set_manual_override()
        await hass.async_block_till_done()
        assert coordinator._pending_writes == {}

        # The queued re-send is gone, so firing the clock changes nothing.
        state["working"] = True
        async_fire_time_changed(
            hass, dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5)
        )
        await hass.async_block_till_done()

    entries = hass.states.get("sensor.goodwe_time_of_use_activity_log").attributes[
        "entries"
    ]
    assert entries[0]["action"] == "mode"
    assert all(entry["result"] == "error" for entry in entries if entry["action"] != "mode")


@pytest.mark.asyncio
async def test_unload_cancels_pending_write_retries(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int], fast_retries
):
    """Unloading the entry must not leave a re-send timer running."""
    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        # A write that never lands: the read-back never matches.
        patch.object(GoodweUdpClient, "write_register", return_value=None),
        patch("custom_components.goodwe_tou.coordinator._RETRY_MIN", 1),
        patch("custom_components.goodwe_tou.coordinator._RETRY_MAX", 1),
        patch("custom_components.goodwe_tou.coordinator._RETRY_STEP", 1),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "set_windows",
                {"windows": [{"start": "02:00", "stop": "05:00", "power": -80}]},
                blocking=True,
            )
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._pending_writes

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert coordinator._pending_writes == {}


@pytest.mark.asyncio
async def test_get_activity_service_filters_by_window(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write_to(store)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        for window, value in ((2, -50), (1, 30)):
            await hass.services.async_call(
                "number",
                "set_value",
                {
                    "entity_id": f"number.goodwe_time_of_use_window_{window}_power",
                    "value": value,
                },
                blocking=True,
            )
        await hass.async_block_till_done()

        response = await hass.services.async_call(
            DOMAIN, "get_activity", {"window": 2}, blocking=True, return_response=True
        )
        await hass.async_block_till_done()

    assert response["count"] == 1
    assert response["entries"][0]["changes"][0]["field"] == "window_2_power"


@pytest.mark.asyncio
async def test_activity_log_loads_persisted_entries(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    from homeassistant.helpers.storage import Store

    seed = Store(hass, 1, f"{DOMAIN}_activity_{entry.entry_id}")
    await seed.async_save(
        [
            {
                "time": "2026-01-01T00:00:00+00:00",
                "action": "eco",
                "result": "ok",
                "error": None,
                "changes": [{"field": "eco_mode", "old": "general", "new": "eco"}],
                "windows": [],
            }
        ]
    )

    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    sensor = hass.states.get("sensor.goodwe_time_of_use_activity_log")
    assert sensor.state == "1"
    assert sensor.attributes["entries"][0]["action"] == "eco"


@pytest.mark.asyncio
async def test_calculate_cheapest_hours_returns_reasons(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The planner card gets a human readable reason for every window."""
    now = dt.datetime.now(dt.UTC).astimezone()
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    prices = [
        {"time": (base + dt.timedelta(minutes=15 * i)).isoformat(), "price": i / 100.0}
        for i in range(96)
    ]
    hass.states.async_set(
        "sensor.average_electricity_price",
        "0.1",
        {"prices": prices, "unit_of_measurement": "€/kWh"},
    )
    hass.states.async_set("sensor.battery_state_of_charge", "40")
    hass.states.async_set("sensor.energy_production_today_remaining", "5")

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write_to(store)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        response = await hass.services.async_call(
            DOMAIN,
            "calculate_cheapest_hours",
            {},
            blocking=True,
            return_response=True,
        )
        await hass.async_block_till_done()

    assert response["windows"]
    assert response["reasons"]
    assert len(response["reasons"]) == len(response["windows"])
    for reason in response["reasons"]:
        assert reason["window"] >= 1
        assert reason["mode"] in ("charge", "discharge")
        assert reason["text"]
    assert response["summary"]
    # The PV forecast is reported as a discount, not a reduction of the charge.
    assert any("PV-prognose" in note for note in response["notes"])
    # The GoodWe charges at 6 kW by default (the legacy power_kw is not set).
    charge_texts = [
        reason["text"] for reason in response["reasons"] if reason["mode"] == "charge"
    ]
    assert charge_texts
    assert all("6 kW" in text for text in charge_texts)


@pytest.mark.asyncio
async def test_plan_sensor_exposes_the_reasons_behind_the_windows(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The status card reads the planner's reasoning from this sensor."""
    now = dt.datetime.now(dt.UTC).astimezone()
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    prices = [
        {"time": (base + dt.timedelta(minutes=15 * i)).isoformat(), "price": i / 100.0}
        for i in range(96)
    ]
    hass.states.async_set(
        "sensor.average_electricity_price",
        "0.1",
        {"prices": prices, "unit_of_measurement": "€/kWh"},
    )
    hass.states.async_set("sensor.battery_state_of_charge", "40")
    hass.states.async_set("sensor.energy_production_today_remaining", "0")

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write_to(store)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        sensor = hass.states.get("sensor.goodwe_time_of_use_plan")
        assert sensor is not None
        # Nothing has been planned yet, so there is nothing to explain.
        assert sensor.state == "0"
        assert sensor.attributes["reasons"] == []

        await hass.services.async_call(DOMAIN, "set_cheapest_hours", {}, blocking=True)
        await hass.async_block_till_done()

    sensor = hass.states.get("sensor.goodwe_time_of_use_plan")
    assert sensor.state != "0"
    reasons = sensor.attributes["reasons"]
    assert reasons
    assert {reason["window"] for reason in reasons} == set(
        range(1, len(sensor.attributes["windows"]) + 1)
    )
    for reason in reasons:
        assert reason["mode"] in ("charge", "discharge")
        assert reason["text"]
    assert sensor.attributes["summary"]
    assert sensor.attributes["notes"]
    assert sensor.attributes["planned_at"]


@pytest.mark.asyncio
async def test_auto_replan_switch_defaults_on(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    switch = hass.states.get("switch.goodwe_time_of_use_auto_herberekening")
    assert switch is not None
    assert switch.state == "on"


@pytest.mark.asyncio
async def test_auto_replan_writes_and_logs_the_reason(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    now = dt.datetime.now(dt.UTC).astimezone()
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    prices = [
        {"time": (base + dt.timedelta(minutes=15 * i)).isoformat(), "price": i / 100.0}
        for i in range(96)
    ]
    hass.states.async_set(
        "sensor.average_electricity_price",
        "0.1",
        {"prices": prices, "unit_of_measurement": "€/kWh"},
    )
    hass.states.async_set("sensor.battery_state_of_charge", "50")
    hass.states.async_set("sensor.energy_production_today_remaining", "0")

    writes: list[tuple[int, int]] = []

    def _write(register: int, value: int) -> None:
        writes.append((register, value))
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        plan = await coordinator.async_auto_replan(force=True)
        await hass.async_block_till_done()

    # The inverter's own schedule differs from the plan, so it was rewritten.
    assert plan is not None
    assert writes
    sensor = hass.states.get("sensor.goodwe_time_of_use_activity_log")
    entry_log = sensor.attributes["entries"][0]
    assert entry_log["action"] == "auto_replan"
    assert entry_log["reason"]
    assert entry_log["result"] == "ok"


@pytest.mark.asyncio
async def test_auto_replan_is_paused_by_the_switch(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    now = dt.datetime.now(dt.UTC).astimezone()
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    prices = [
        {"time": (base + dt.timedelta(minutes=15 * i)).isoformat(), "price": i / 100.0}
        for i in range(96)
    ]
    hass.states.async_set(
        "sensor.average_electricity_price",
        "0.1",
        {"prices": prices, "unit_of_measurement": "€/kWh"},
    )
    hass.states.async_set("sensor.battery_state_of_charge", "50")
    hass.states.async_set("sensor.energy_production_today_remaining", "0")

    writes: list[tuple[int, int]] = []

    def _write(register: int, value: int) -> None:
        writes.append((register, value))
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": "switch.goodwe_time_of_use_auto_herberekening"},
            blocking=True,
        )
        await hass.async_block_till_done()
        writes.clear()  # ignore the export-limit write done during setup

        coordinator = hass.data[DOMAIN][entry.entry_id]
        result = await coordinator.async_auto_replan()
        await hass.async_block_till_done()

    assert result is None
    assert writes == []


@pytest.mark.asyncio
async def test_auto_eco_button_enables_eco_and_the_replan(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """Auto/ECO Mode puts the inverter in ECO and resumes the automation."""
    state = {"work_mode": 0}
    writes: list[tuple[int, int]] = []

    def _read(offset: int, count: int):
        if offset == 47000:
            return [state["work_mode"]]
        if offset == 47509:
            return [1, 10000]
        if offset == 37022:
            return [3300, 3300]
        assert offset == BASE and count == 16
        return list(store)

    def _write(register: int, value: int) -> None:
        writes.append((register, value))
        if register == 47000:
            state["work_mode"] = value
        elif BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        writes.clear()

        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.goodwe_time_of_use_auto_eco_mode"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert (47000, 3) in writes  # ECO work mode
    assert (
        hass.states.get("switch.goodwe_time_of_use_auto_herberekening").state == "on"
    )
    latest = hass.states.get(
        "sensor.goodwe_time_of_use_activity_log"
    ).attributes["entries"][0]
    assert latest["action"] == "mode"


@pytest.mark.asyncio
async def test_manual_override_button_pauses_the_automation(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """Manual override stops the re-check and leaves the windows alone."""
    writes: list[tuple[int, int]] = []

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(
            GoodweUdpClient,
            "write_register",
            side_effect=lambda register, value: writes.append((register, value)),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        writes.clear()

        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.goodwe_time_of_use_manual_override"},
            blocking=True,
        )
        await hass.async_block_till_done()

    # Nothing is written to the inverter: the windows on it keep running.
    assert writes == []
    assert (
        hass.states.get("switch.goodwe_time_of_use_auto_herberekening").state == "off"
    )
    latest = hass.states.get(
        "sensor.goodwe_time_of_use_activity_log"
    ).attributes["entries"][0]
    assert latest["action"] == "mode"


@pytest.mark.asyncio
async def test_setup_creates_a_cell_voltage_sensor(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The lowest cell voltage is exposed, with the guard threshold next to it."""
    with patch.object(
        GoodweUdpClient,
        "read_registers",
        side_effect=_read_from(store, cells=(3248, 3215)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    sensor = hass.states.get("sensor.goodwe_time_of_use_laagste_celspanning")
    assert sensor is not None
    assert sensor.state == "3215"
    assert sensor.attributes["threshold"] == 3000


@pytest.mark.asyncio
async def test_low_cell_voltage_pauses_the_automation(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """A cell near empty is the real "battery is flat" signal, not the SOC.

    The automation stops itself and points at the SEMS/SolarGo app instead of
    forcing a charge.
    """
    writes: list[tuple[int, int]] = []

    with (
        patch.object(
            GoodweUdpClient,
            "read_registers",
            side_effect=_read_from(store, cells=(2900, 3300)),
        ),
        patch.object(
            GoodweUdpClient,
            "write_register",
            side_effect=lambda register, value: writes.append((register, value)),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        writes.clear()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        result = await coordinator.async_auto_replan(force=True)
        await hass.async_block_till_done()

    assert result is None
    assert writes == []
    assert (
        hass.states.get("switch.goodwe_time_of_use_auto_herberekening").state == "off"
    )
    latest = hass.states.get(
        "sensor.goodwe_time_of_use_activity_log"
    ).attributes["entries"][0]
    assert latest["action"] == "guard"
    assert latest["result"] == "error"
    assert "SEMS" in latest["reason"]


@pytest.mark.asyncio
async def test_low_cell_voltage_guard_can_be_disabled(
    hass: HomeAssistant, store: list[int]
):
    """A threshold of 0 switches the low-cell guard off."""
    entry = _entry_with_options(hass, **{CONF_MIN_CELL_VOLTAGE: 0})
    with patch.object(
        GoodweUdpClient,
        "read_registers",
        side_effect=_read_from(store, cells=(2900, 3300)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._cell_guard_reason() is None


@pytest.mark.asyncio
async def test_log_size_option_trims_the_activity_log(
    hass: HomeAssistant, store: list[int]
):
    """The activity log keeps only as many entries as the user configured."""
    entry = _entry_with_options(hass, **{CONF_LOG_SIZE: 5})
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    for _ in range(12):
        coordinator._record("eco", result="ok")

    assert len(coordinator.activity()) == 5


def test_recheck_minutes_are_configurable(hass: HomeAssistant):
    """The re-check schedule follows the interval, a minute before the boundary."""
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_RECHECK_MINUTES: 30})
    assert _recheck_minutes(entry) == [29, 59]

    # An interval that does not divide an hour falls back to the default.
    odd = MockConfigEntry(domain=DOMAIN, options={CONF_RECHECK_MINUTES: 7})
    assert _recheck_minutes(odd) == [14, 29, 44, 59]

    default = MockConfigEntry(domain=DOMAIN)
    assert _recheck_minutes(default) == [14, 29, 44, 59]


@pytest.mark.asyncio
async def test_active_window_detection(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """A window is active only inside its start/stop window and on its day."""
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.data["windows"] = {
        1: TouWindow(
            start=dt.time(10, 0), stop=dt.time(11, 0), mode="discharge", power=100
        ),
        2: TouWindow(start=dt.time(0, 0), stop=dt.time(0, 0), mode="off", power=0),
        3: TouWindow(start=dt.time(0, 0), stop=dt.time(0, 0), mode="off", power=0),
        4: TouWindow(start=dt.time(0, 0), stop=dt.time(0, 0), mode="off", power=0),
    }

    monday = dt.datetime(2026, 1, 5, 10, 30, tzinfo=dt.UTC)
    assert coordinator._active_window(monday) is not None
    assert coordinator._active_window(monday.replace(hour=9)) is None
    assert coordinator._active_window(monday.replace(hour=11)) is None

    # A disabled window never counts, even inside its start/stop window.
    coordinator.data["windows"][1] = TouWindow(
        start=dt.time(10, 0), stop=dt.time(11, 0), mode="off", power=0
    )
    assert coordinator._active_window(monday) is None


@pytest.mark.asyncio
async def test_auto_replan_leaves_a_running_window_alone(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """Re-planning mid-window used to slide the window a quarter hour each time."""
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    running = TouWindow(
        start=dt.time(10, 0), stop=dt.time(11, 0), mode="discharge", power=100
    )
    with (
        patch.object(coordinator, "_active_window", return_value=running),
        patch.object(
            coordinator, "_plan_cheapest_hours", side_effect=AssertionError("planned")
        ),
    ):
        result = await coordinator.async_auto_replan()

    assert result is None


@pytest.mark.asyncio
async def test_manual_charge_and_discharge_buttons(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The manual buttons flip one window's direction and pause the re-check."""
    writes: list[tuple[int, int]] = []

    def _write(register: int, value: int) -> None:
        writes.append((register, value))
        if BASE <= register < BASE + 16:
            store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.auto_replan_enabled is True

        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.goodwe_time_of_use_manual_charge"},
            blocking=True,
        )
        await hass.async_block_till_done()

    window = coordinator.data["windows"][4]
    assert window.mode == "charge"
    assert window.power == 100
    # start/stop/days of the chosen window are left alone
    assert window.start == dt.time(22, 15)
    assert window.stop == dt.time(23, 0)
    assert coordinator.auto_replan_enabled is False
    assert coordinator.manual_mode() == "charge"
    assert (
        hass.states.get("sensor.goodwe_time_of_use_activity_log")
        .attributes["entries"][0]["action"]
        == "manual_mode"
    )

    # Pressing the other direction flips the same window (mutual exclusion) ...
    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.goodwe_time_of_use_manual_discharge"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert coordinator.data["windows"][4].mode == "discharge"
    assert coordinator.data["windows"][4].power == 100

    # ... and pressing the active direction again switches the window off.
    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.goodwe_time_of_use_manual_discharge"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert coordinator.data["windows"][4].mode == "off"
    assert coordinator.manual_mode() == "off"


@pytest.mark.asyncio
async def test_planner_structure_options_are_used(
    hass: HomeAssistant, store: list[int]
):
    """The number of windows and the window power come from the addon options."""
    now = dt.datetime.now(dt.UTC).astimezone()
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    prices = [
        {"time": (base + dt.timedelta(minutes=15 * i)).isoformat(), "price": i / 100.0}
        for i in range(96)
    ]
    hass.states.async_set(
        "sensor.average_electricity_price",
        "0.1",
        {"prices": prices, "unit_of_measurement": "€/kWh"},
    )
    hass.states.async_set("sensor.battery_state_of_charge", "50")
    hass.states.async_set("sensor.energy_production_today_remaining", "0")

    entry = _entry_with_options(hass, **{CONF_WINDOW_POWER: 55})
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    plan = coordinator._plan_cheapest_hours()
    assert plan.windows
    assert all(window.power == 55 for window in plan.windows)


@pytest.mark.asyncio
async def test_planner_can_leave_windows_unused(hass: HomeAssistant, store: list[int]):
    """Setting both window counts to 0 asks the planner for no windows at all."""
    now = dt.datetime.now(dt.UTC).astimezone()
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    prices = [
        {"time": (base + dt.timedelta(minutes=15 * i)).isoformat(), "price": i / 100.0}
        for i in range(96)
    ]
    hass.states.async_set(
        "sensor.average_electricity_price",
        "0.1",
        {"prices": prices, "unit_of_measurement": "€/kWh"},
    )
    hass.states.async_set("sensor.battery_state_of_charge", "50")
    hass.states.async_set("sensor.energy_production_today_remaining", "0")

    entry = _entry_with_options(
        hass, **{CONF_CHARGE_WINDOWS: 0, CONF_DISCHARGE_WINDOWS: 0}
    )
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    plan = coordinator._plan_cheapest_hours()
    assert plan.windows == []


@pytest.mark.asyncio
async def test_tibber_price_sensor_exposes_the_price_list(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The price sensor turns tibber.get_prices into a {time, price} list."""
    from homeassistant.core import SupportsResponse

    now = dt.datetime.now(dt.UTC).astimezone()
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    records = [
        {
            "start_time": (base + dt.timedelta(minutes=15 * i)).isoformat(),
            "price": 0.30 + i / 1000.0,
        }
        for i in range(8)
    ]

    async def _get_prices(call):
        # The sensor asks for a window that covers today and tomorrow.
        assert call.data["start"] and call.data["end"]
        return {"prices": {"akkerv5": records}}

    hass.services.async_register(
        "tibber", "get_prices", _get_prices, supports_response=SupportsResponse.ONLY
    )

    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    sensor = hass.states.get("sensor.goodwe_time_of_use_stroomprijzen")
    assert sensor is not None
    # The state is the price of the quarter hour that is running now.
    assert sensor.state == "0.3"
    assert sensor.attributes["unit_of_measurement"] == "EUR/kWh"
    assert sensor.attributes["home"] == "akkerv5"
    assert len(sensor.attributes["prices"]) == 8
    assert sensor.attributes["prices"][0] == {
        "time": base.isoformat(),
        "price": 0.30,
    }


@pytest.mark.asyncio
async def test_tibber_price_sensor_without_tibber(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """Without the Tibber service the sensor exists but has nothing to show."""
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    sensor = hass.states.get("sensor.goodwe_time_of_use_stroomprijzen")
    assert sensor is not None
    assert sensor.state == "unknown"
    assert sensor.attributes["prices"] == []


@pytest.mark.asyncio
async def test_tibber_price_sensor_can_be_disabled(hass: HomeAssistant, store: list[int]):
    """The Tibber price sensor is optional; disabling it removes the entity."""
    entry = _entry_with_options(hass, **{CONF_TIBBER_PRICES: False})
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get("sensor.goodwe_time_of_use_stroomprijzen") is None


@pytest.mark.asyncio
async def test_price_source_tibber_plans_from_tibber_prices(
    hass: HomeAssistant, store: list[int]
):
    """Choosing Tibber makes the planner use Tibber's prices, not the entity."""
    from homeassistant.core import SupportsResponse

    now = dt.datetime.now(dt.UTC).astimezone()
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    records = [
        {
            "start_time": (base + dt.timedelta(minutes=15 * i)).isoformat(),
            "price": 0.10 if i < 8 else 0.40,
        }
        for i in range(96)
    ]

    async def _get_prices(call):
        return {"prices": {"akkerv5": records}}

    hass.services.async_register(
        "tibber", "get_prices", _get_prices, supports_response=SupportsResponse.ONLY
    )
    # A decoy price entity with no usable forecast: if the planner read it, the
    # service would raise instead of returning a plan.
    hass.states.async_set(
        "sensor.average_electricity_price", "0.1", {"prices": [], "unit_of_measurement": "€/kWh"}
    )
    hass.states.async_set("sensor.battery_state_of_charge", "40")
    hass.states.async_set("sensor.energy_production_today_remaining", "0")

    entry = _entry_with_options(hass, **{CONF_PRICE_SOURCE: "tibber"})
    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write_to(store)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        response = await hass.services.async_call(
            DOMAIN,
            "calculate_cheapest_hours",
            {},
            blocking=True,
            return_response=True,
        )
        await hass.async_block_till_done()

    assert response["windows"]
    assert response["reasons"]


@pytest.mark.asyncio
async def test_tibber_source_without_the_price_sensor(hass: HomeAssistant, store: list[int]):
    """Tibber as the price source needs no price sensor entity."""
    from homeassistant.core import SupportsResponse

    async def _get_prices(call):
        return {"prices": {"akkerv5": []}}

    hass.services.async_register(
        "tibber", "get_prices", _get_prices, supports_response=SupportsResponse.ONLY
    )
    entry = _entry_with_options(
        hass, **{CONF_TIBBER_PRICES: False, CONF_PRICE_SOURCE: "tibber"}
    )
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # The sensor is off, but the coordinator still exists for the planner.
    assert hass.states.get("sensor.goodwe_time_of_use_stroomprijzen") is None
    assert hass.data[DOMAIN][entry.entry_id].price_coordinator is not None


@pytest.mark.asyncio
async def test_config_flow_success(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"

    with patch.object(GoodweUdpClient, "read_registers", return_value=[3]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "192.168.20.13", "port": 8899, "comm_addr": 247},
        )
    assert result["type"] == "create_entry"
    assert result["data"]["host"] == "192.168.20.13"


@pytest.mark.asyncio
async def test_options_flow_offers_a_price_source(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The Configure dialog lets the user pick ENTSO-e or Tibber."""
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    keys = {str(key) for key in result["data_schema"].schema}
    assert "price_source" in keys
    assert "price_entity" in keys


@pytest.mark.asyncio
async def test_options_flow_offers_a_language(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The Configure dialog lets the user change the language."""
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    keys = {str(key) for key in result["data_schema"].schema}
    assert "language" in keys


@pytest.mark.asyncio
async def test_options_flow_offers_an_export_spread(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The configured export spread is selectable from the Configure dialog."""
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    keys = {str(key) for key in result["data_schema"].schema}
    assert CONF_EXPORT_MIN_SPREAD in keys


@pytest.mark.asyncio
async def test_language_option_translates_entity_names(
    hass: HomeAssistant, store: list[int]
):
    """The language chosen at install translates the names but not the ids."""
    entry = _entry_with_options(hass, **{CONF_LANGUAGE: "en"})
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # The entity id is pinned, so the translated name never moves the entity ...
    activity = hass.states.get("sensor.goodwe_time_of_use_activity_log")
    assert activity is not None
    assert "Activity log" in activity.name
    # ... and the cards read the language from the activity sensor.
    assert activity.attributes["language"] == "en"
    window = hass.states.get("time.goodwe_time_of_use_window_1_start")
    assert "Window 1 start" in window.name


@pytest.mark.asyncio
async def test_config_flow_cannot_connect(hass: HomeAssistant):
    from custom_components.goodwe_tou.goodwe_udp import GoodweTimeoutError

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with patch.object(
        GoodweUdpClient, "read_registers", side_effect=GoodweTimeoutError("nope")
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "192.168.20.13", "port": 8899, "comm_addr": 247},
        )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}


# -- deviation tracking / adaptive load ------------------------------------


def _set_weather(hass: HomeAssistant, temperature: float, humidity: float = 50, wind: float = 0):
    hass.states.async_set(
        "weather.forecast_home",
        "sunny",
        {
            "temperature": temperature,
            "temperature_unit": "\u00b0C",
            "humidity": humidity,
            "wind_speed": wind,
            "wind_speed_unit": "km/h",
        },
    )


@pytest.mark.asyncio
async def test_setup_creates_a_deviation_sensor(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The deviation sensor is registered and carries the language the cards read."""
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    sensor = hass.states.get("sensor.goodwe_time_of_use_afwijking")
    assert sensor is not None
    assert sensor.state == "0"
    assert sensor.attributes["language"] == "nl"
    assert sensor.attributes["today"]["samples"] == 0


@pytest.mark.asyncio
async def test_effective_standby_follows_the_weather(
    hass: HomeAssistant, store: list[int]
):
    """A cold, feels-like hour makes the planner assume a higher house load."""
    entry = _entry_with_options(
        hass, **{CONF_STANDBY_KW: 1.2, CONF_TEMP_COEFF: 0.05}
    )
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]

    _set_weather(hass, 20.0)
    assert coordinator.effective_standby_kw() == pytest.approx(1.2)

    _set_weather(hass, 5.0)
    _, feels, _ = coordinator._weather_now()
    assert feels is not None
    assert coordinator.effective_standby_kw() == pytest.approx(
        1.2 + 0.05 * (15.5 - feels)
    )


@pytest.mark.asyncio
async def test_adaptive_load_can_be_turned_off(
    hass: HomeAssistant, store: list[int]
):
    """With adaptive_load off the planner keeps the configured standby load."""
    entry = _entry_with_options(
        hass,
        **{
            CONF_ADAPTIVE_LOAD: False,
            CONF_STANDBY_KW: 1.2,
            CONF_TEMP_COEFF: 0.05,
        },
    )
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    _set_weather(hass, 2.0)
    assert coordinator.effective_standby_kw() == pytest.approx(1.2)
    assert coordinator._learned is None


@pytest.mark.asyncio
async def test_deviation_log_collects_an_hourly_sample(
    hass: HomeAssistant, store: list[int]
):
    """The coordinator integrates one hour of load, weather and PV into a sample."""
    entry = _entry_with_options(
        hass, **{CONF_STANDBY_KW: 1.2, CONF_TEMP_COEFF: 0.0}
    )
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    hass.states.async_set("sensor.house_consumption", "1500")
    hass.states.async_set("sensor.pv_power", "500")
    hass.states.async_set("sensor.energy_production_today_remaining", "10")
    _set_weather(hass, 20.0)

    t0 = dt.datetime(2026, 11, 17, 3, 0, tzinfo=dt.UTC)
    coordinator._sample_deviation(t0)  # opens the 03:00 bucket
    coordinator._sample_deviation(t0 + dt.timedelta(minutes=30))  # half an hour
    hass.states.async_set("sensor.energy_production_today_remaining", "9")
    coordinator._sample_deviation(t0 + dt.timedelta(hours=1))  # rolls the hour

    entries = coordinator.deviation()
    assert len(entries) == 1
    sample = entries[0]
    assert sample["time"] == "2026-11-17T03:00+00:00"
    assert sample["hours"] == pytest.approx(0.5)
    assert sample["actual_kwh"] == pytest.approx(0.75)  # 1.5 kW * 0.5 h
    assert sample["planned_kwh"] == pytest.approx(0.6)  # 1.2 kW * 0.5 h
    assert sample["deviation_kwh"] == pytest.approx(0.15)
    assert sample["outdoor_temp"] == pytest.approx(20.0)
    assert sample["pv_kwh"] == pytest.approx(0.25)  # 0.5 kW * 0.5 h
    assert sample["pv_forecast_kwh"] == pytest.approx(1.0)  # 10 - 9

    # The full log is available through the service ...
    response = await hass.services.async_call(
        DOMAIN, "get_deviations", {}, blocking=True, return_response=True
    )
    assert response["count"] == 1
    assert response["entries"][0]["time"] == "2026-11-17T03:00+00:00"

    # ... and the sensor exposes the newest samples as an attribute.
    coordinator._publish(coordinator.data)
    sensor = hass.states.get("sensor.goodwe_time_of_use_afwijking")
    assert sensor.state == "1"
    assert sensor.attributes["entries"][0]["time"] == "2026-11-17T03:00+00:00"


@pytest.mark.asyncio
async def test_deviation_log_learns_a_house_load_model(
    hass: HomeAssistant, store: list[int]
):
    """Enough clean samples give the planner its own base load and slope."""
    entry = _entry_with_options(
        hass, **{CONF_STANDBY_KW: 1.2, CONF_TEMP_COEFF: 0.05}
    )
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    # 48 perfect hourly samples: 1.0 kW base plus 0.1 kW per degree.
    coordinator._deviation = [
        {"hours": 1.0, "degree_hours": float(d), "actual_kwh": 1.0 + 0.1 * d}
        for d in range(48)
    ]
    coordinator._recompute_learned()

    learned = coordinator._learned
    assert learned is not None
    assert learned["samples"] == 48
    assert learned["base_kw"] == pytest.approx(1.0)
    assert learned["temp_coeff"] == pytest.approx(0.1)

    # The learned model now drives the assumed load ...
    _set_weather(hass, 20.0)
    assert coordinator.effective_standby_kw() == pytest.approx(1.0)
    _set_weather(hass, 5.0)
    _, feels, _ = coordinator._weather_now()
    assert coordinator.effective_standby_kw() == pytest.approx(
        1.0 + 0.1 * (15.5 - feels)
    )

    # ... unless a plan pins standby_kw explicitly.
    assert coordinator._standby_setting({CONF_STANDBY_KW: 3.0}) == pytest.approx(3.0)
    assert coordinator._standby_setting({}) == pytest.approx(
        coordinator.effective_standby_kw()
    )


# -- measured quarter-hour consumption profile -----------------------------


def _stat_rows(values_w, start_local, step_minutes: int = 15):
    """Recorder-style statistics rows (mean in watt) for one local day."""
    rows = []
    for i, value in enumerate(values_w):
        moment = start_local + dt.timedelta(minutes=step_minutes * i)
        rows.append(
            {
                "start": moment.timestamp(),
                "end": (moment + dt.timedelta(minutes=step_minutes)).timestamp(),
                "mean": value,
            }
        )
    return rows


def _patch_statistics(load_w, grid_w):
    """Patch the recorder so the profile reads the given watt series."""

    def _fake(hass, start_time, end_time, statistic_ids, period, units, types):
        return {
            "sensor.house_consumption": load_w,
            "sensor.meter_active_power_total": grid_w,
        }

    return patch(
        "homeassistant.components.recorder.statistics.statistics_during_period",
        side_effect=_fake,
    )


@pytest.mark.asyncio
async def test_profile_is_built_from_statistics(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """The profile is read from the recorder's 5-minute statistics, in kW."""
    now = dt.datetime.now(dt.UTC).astimezone()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # 1 kW house load all day; the meter imports in the morning (negative) and
    # exports in the afternoon (positive).
    load = _stat_rows([1000.0] * 96, day_start)
    grid = _stat_rows(
        [-500.0] * 48 + [800.0] * 48,
        day_start,
    )

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        _patch_statistics(load, grid),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        response = await hass.services.async_call(
            DOMAIN, "get_profile", {}, blocking=True, return_response=True
        )

    assert len(response["load"]) == 96
    assert response["load"][0] == pytest.approx(1.0)  # kW, not watt
    assert response["load_daily_kwh"] == pytest.approx(24.0)
    assert response["import_daily_kwh"] == pytest.approx(0.5 * 12.0)
    assert response["export_daily_kwh"] == pytest.approx(0.8 * 12.0)
    assert response["days"] >= 1

    sensor = hass.states.get("sensor.goodwe_time_of_use_verbruiksprofiel")
    assert sensor is not None
    assert float(sensor.state) == pytest.approx(24.0)
    assert len(sensor.attributes["load"]) == 96


@pytest.mark.asyncio
async def test_profile_clamps_a_negative_house_load(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """A negative house-consumption reading becomes a zero net load, never below."""
    now = dt.datetime.now(dt.UTC).astimezone()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # The derived house sensor tips negative while the battery charges from PV.
    load = _stat_rows([-1500.0] * 48 + [2000.0] * 48, day_start)
    grid = _stat_rows([0.0] * 96, day_start)

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        _patch_statistics(load, grid),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        response = await hass.services.async_call(
            DOMAIN, "get_profile", {}, blocking=True, return_response=True
        )

    assert min(response["load"]) == 0.0
    assert response["load"][0] == 0.0
    assert response["load"][60] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_profile_feeds_the_planner(
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
):
    """With a profile in place the planner plans from it and says so."""
    now = dt.datetime.now(dt.UTC).astimezone()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    prices = [
        {
            "time": (base + dt.timedelta(minutes=15 * i)).isoformat(),
            "price": i / 100.0,
        }
        for i in range(96)
    ]
    hass.states.async_set(
        "sensor.average_electricity_price",
        "0.1",
        {"prices": prices, "unit_of_measurement": "€/kWh"},
    )
    hass.states.async_set("sensor.battery_state_of_charge", "30")
    hass.states.async_set("sensor.energy_production_today_remaining", "0")

    load = _stat_rows([1200.0] * 96, day_start)
    grid = _stat_rows([0.0] * 96, day_start)
    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        _patch_statistics(load, grid),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    sequence = coordinator._load_profile_sequence()
    assert sequence is not None and len(sequence) == 96
    assert sequence[0] == pytest.approx(1.2)

    plan = coordinator._plan_cheapest_hours({})
    text = " ".join(plan.notes)
    assert "verbruiksprofiel" in text
    assert "standby" not in text


@pytest.mark.asyncio
async def test_profile_can_be_disabled(
    hass: HomeAssistant, store: list[int]
):
    """With the profile off the planner keeps its flat standby load."""
    entry = _entry_with_options(hass, **{CONF_PROFILE_ENABLED: False})
    now = dt.datetime.now(dt.UTC).astimezone()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    load = _stat_rows([1000.0] * 96, day_start)

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        _patch_statistics(load, []),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._load_profile_sequence() is None
    assert coordinator.profile_summary()["enabled"] is False

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
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.goodwe_tou import _recheck_minutes
from custom_components.goodwe_tou.const import (
    CONF_CHARGE_WINDOWS,
    CONF_DISCHARGE_WINDOWS,
    CONF_LOG_SIZE,
    CONF_MIN_CELL_VOLTAGE,
    CONF_RECHECK_MINUTES,
    CONF_TIBBER_PRICES,
    CONF_WINDOW_POWER,
    DOMAIN,
)
from custom_components.goodwe_tou.goodwe_udp import GoodweRejectedError, GoodweUdpClient

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
                ]
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
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
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
    hass: HomeAssistant, entry: MockConfigEntry, store: list[int]
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
    """The re-check schedule follows the interval, but stays aligned to the hour."""
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_RECHECK_MINUTES: 30})
    assert _recheck_minutes(entry) == [0, 30]

    # An interval that does not divide an hour falls back to the default.
    odd = MockConfigEntry(domain=DOMAIN, options={CONF_RECHECK_MINUTES: 7})
    assert _recheck_minutes(odd) == [0, 15, 30, 45]

    default = MockConfigEntry(domain=DOMAIN)
    assert _recheck_minutes(default) == [0, 15, 30, 45]


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

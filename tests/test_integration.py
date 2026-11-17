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

from custom_components.goodwe_tou.const import DOMAIN
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
    0x0020,
    0x160F,
    0x1700,
    0x0000,
    0x007F,
]


@pytest.fixture
def store() -> list[int]:
    return list(TOU_REGISTERS)


def _read_from(store: list[int], work_mode: int = 3):
    def _read(offset: int, count: int):
        if offset == 47000:
            return [work_mode]
        if offset == BASE:
            assert count == 16
            return list(store)
        raise AssertionError(f"unexpected read at {offset}")

    return _read


def _write_to(store: list[int]):
    def _write(register: int, value: int) -> None:
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
    assert days.state == "mon,tue,wed,thu,fri,sat,sun"
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
        store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

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
    # window 3, saturday only, off -> switch off, mask 0x20
    assert mapping[47526] == 0x0020
    assert len(writes) == 16


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
        store[register - BASE] = value & 0xFFFF

    with (
        patch.object(GoodweUdpClient, "read_registers", side_effect=_read_from(store)),
        patch.object(GoodweUdpClient, "write_register", side_effect=_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

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

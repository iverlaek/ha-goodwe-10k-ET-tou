"""Set up the integration inside a real Home Assistant test instance.

Skipped automatically when ``pytest-homeassistant-custom-component`` is not
installed (for example in the lightweight CI job).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.goodwe_tou.const import DOMAIN
from custom_components.goodwe_tou.goodwe_udp import GoodweUdpClient

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


def _fake_read(offset: int, count: int):
    if offset == 47000:
        return [3]
    if offset == 47515:
        assert count == 16
        return list(TOU_REGISTERS)
    raise AssertionError(f"unexpected read at {offset}")


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.20.13", "port": 8899, "comm_addr": 247},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_setup_creates_entities(hass: HomeAssistant, entry: MockConfigEntry):
    with patch.object(GoodweUdpClient, "read_registers", side_effect=_fake_read):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    ids = sorted(hass.states.async_entity_ids())
    assert "switch.goodwe_time_of_use_eco_mode" in ids
    assert "button.goodwe_time_of_use_read_from_inverter" in ids
    assert "button.goodwe_time_of_use_apply_to_inverter" in ids

    start = hass.states.get("time.goodwe_time_of_use_window_1_start")
    stop = hass.states.get("time.goodwe_time_of_use_window_1_stop")
    mode = hass.states.get("select.goodwe_time_of_use_window_1_mode")
    power = hass.states.get("number.goodwe_time_of_use_window_1_power")
    days = hass.states.get("text.goodwe_time_of_use_window_1_days")
    eco = hass.states.get("switch.goodwe_time_of_use_eco_mode")

    assert start.state == "04:00:00"
    assert stop.state == "06:00:00"
    assert mode.state == "charge"
    assert power.state == "100.0"
    assert days.state == "mon,tue,wed,thu,fri,sat,sun"
    assert eco.state == "on"

    # window 2 has the switch off -> mode off, stored magnitude preserved
    assert hass.states.get("select.goodwe_time_of_use_window_2_mode").state == "off"
    assert hass.states.get("number.goodwe_time_of_use_window_2_power").state == "30.0"
    # window 3 runs on saturday only
    assert hass.states.get("text.goodwe_time_of_use_window_3_days").state == "sat"


@pytest.mark.asyncio
async def test_apply_writes_registers(hass: HomeAssistant, entry: MockConfigEntry):
    writes: list[tuple[int, int]] = []

    with patch.object(GoodweUdpClient, "read_registers", side_effect=_fake_read), patch.object(
        GoodweUdpClient, "write_register", side_effect=lambda r, v: writes.append((r, v))
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
async def test_set_time_writes_single_register(
    hass: HomeAssistant, entry: MockConfigEntry
):
    writes: list[tuple[int, int]] = []

    with patch.object(GoodweUdpClient, "read_registers", side_effect=_fake_read), patch.object(
        GoodweUdpClient, "write_register", side_effect=lambda r, v: writes.append((r, v))
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
    assert (
        hass.states.get("time.goodwe_time_of_use_window_1_start").state == "05:15:00"
    )


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

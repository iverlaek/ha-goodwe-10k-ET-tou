"""Unit tests for the register encode/decode helpers."""

from __future__ import annotations

import datetime as dt

from conftest import load

const = load("const")
registers = load("registers")


def test_time_round_trip():
    for hour, minute in [(0, 0), (4, 0), (12, 30), (23, 59)]:
        value = registers.encode_time(dt.time(hour, minute))
        assert value == hour * 256 + minute
        assert registers.decode_time(value) == dt.time(hour, minute)


def test_time_decode_clamps_invalid():
    assert registers.decode_time(0xFF00) == dt.time(0, 0)
    assert registers.decode_time(0x00FF) == dt.time(0, 0)


def test_power_encode_decode():
    assert registers.encode_power("charge", 30) == -30
    assert registers.encode_power("discharge", 100) == 100
    assert registers.encode_power("off", 50) == 0
    assert registers.encode_power("charge", 500) == -100

    assert registers.decode_power(0xFFE2) == ("charge", 30)
    assert registers.decode_power(100) == ("discharge", 100)
    assert registers.decode_power(0) == ("off", 0)


def test_switch_encode_decode():
    assert registers.encode_switch("off", 0x7F) == 0x007F
    assert registers.encode_switch("charge", 0x7F) == 0xFF7F
    assert registers.encode_switch("discharge", 0x20) == 0xFF20

    assert registers.decode_switch(0xFF7F) == (True, 0x7F)
    assert registers.decode_switch(0x007F) == (False, 0x7F)


def test_days_to_mask():
    assert registers.days_to_mask("all") == 0x7F
    assert registers.days_to_mask("mon-sun") == 0x7F
    assert registers.days_to_mask("weekdays") == 0x1F
    assert registers.days_to_mask("weekend") == 0x60
    assert registers.days_to_mask("sat") == 0x20
    assert registers.days_to_mask("mon,wed,fri") == 0x15
    assert registers.days_to_mask("none") == 0
    assert registers.days_to_mask("") == 0


def test_mask_to_days():
    assert registers.mask_to_days(0x7F) == "mon,tue,wed,thu,fri,sat,sun"
    assert registers.mask_to_days(0x20) == "sat"
    assert registers.mask_to_days(0) == "none"


def test_window_round_trip_snapshot():
    window = registers.TouWindow(
        start=dt.time(4, 0),
        stop=dt.time(6, 0),
        mode="charge",
        power=100,
        days="all",
    )
    regs = registers.window_registers(window)
    assert regs[0] == 0x0400
    assert regs[1] == 0x0600
    assert regs[2] == 0xFF9C  # -100
    assert regs[3] == 0xFF7F

    decoded = registers.decode_window(
        registers.base_for(4), [regs[i] for i in range(4)]
    )
    assert decoded.start == dt.time(4, 0)
    assert decoded.stop == dt.time(6, 0)
    assert decoded.mode == "charge"
    assert decoded.power == 100
    assert decoded.days == "mon,tue,wed,thu,fri,sat,sun"


def test_decode_disabled_window_preserves_power():
    # switch off (0x007F) with a stored -30 charge magnitude
    decoded = registers.decode_window(registers.base_for(1), [0, 0, 0xFFE2, 0x007F])
    assert decoded.mode == "off"
    assert decoded.power == 30


def test_base_for():
    assert [registers.base_for(n) for n in const.WINDOWS] == [
        47515,
        47519,
        47523,
        47527,
    ]

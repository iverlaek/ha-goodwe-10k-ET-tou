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
    assert registers.days_to_mask("weekdays") == 0x3E
    assert registers.days_to_mask("weekend") == 0x41
    assert registers.days_to_mask("sat") == 0x40
    assert registers.days_to_mask("mon,wed,fri") == 0x2A
    assert registers.days_to_mask("none") == 0
    assert registers.days_to_mask("") == 0


def test_mask_to_days():
    assert registers.mask_to_days(0x7F) == "sun,mon,tue,wed,thu,fri,sat"
    assert registers.mask_to_days(0x20) == "fri"
    assert registers.mask_to_days(0) == "none"


def test_day_bit_is_sunday_first():
    # Sunday is bit 0, Saturday bit 6 (official register doc: "0~6bit means
    # Sunday~Saturday"), so a Python weekday index has to be shifted.
    assert registers.day_bit(dt.date(2026, 11, 15)) == 0x01  # Sunday
    assert registers.day_bit(dt.date(2026, 11, 16)) == 0x02  # Monday
    assert registers.day_bit(dt.date(2026, 11, 21)) == 0x40  # Saturday
    # plain weekday indices (Monday 0 .. Sunday 6)
    assert registers.day_bit(0) == 0x02
    assert registers.day_bit(6) == 0x01
    # a datetime is a date subclass and must work too
    assert registers.day_bit(dt.datetime(2026, 11, 16, 12, 0)) == 0x02


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
    assert decoded.days == "sun,mon,tue,wed,thu,fri,sat"


def test_decode_disabled_window_preserves_power():
    # switch off (0x007F) with a stored -30 charge magnitude
    decoded = registers.decode_window(registers.base_for(1), [0, 0, 0xFFE2, 0x007F])
    assert decoded.mode == "off"
    assert decoded.power == 30


def test_signed_power_property():
    assert (
        registers.TouWindow(
            start=dt.time(1), stop=dt.time(2), mode="charge", power=70
        ).signed_power
        == -70
    )
    assert (
        registers.TouWindow(
            start=dt.time(1), stop=dt.time(2), mode="discharge", power=70
        ).signed_power
        == 70
    )
    assert (
        registers.TouWindow(
            start=dt.time(1), stop=dt.time(2), mode="off", power=70
        ).signed_power
        == 0
    )


def test_from_signed_power():
    charge = registers.TouWindow.from_signed_power(
        start=dt.time(1), stop=dt.time(2), signed_power=-80, days="all"
    )
    assert charge.mode == "charge"
    assert charge.power == 80

    discharge = registers.TouWindow.from_signed_power(
        start=dt.time(1), stop=dt.time(2), signed_power=45, days="mon"
    )
    assert discharge.mode == "discharge"
    assert discharge.power == 45

    off = registers.TouWindow.from_signed_power(
        start=dt.time(1), stop=dt.time(2), signed_power=0, days="mon"
    )
    assert off.mode == "off"
    assert off.power == 0
    assert off.signed_power == 0

    clamped = registers.TouWindow.from_signed_power(
        start=dt.time(1), stop=dt.time(2), signed_power=-250, days="all"
    )
    assert clamped.power == 100


def test_base_for():
    assert [registers.base_for(n) for n in const.WINDOWS] == [
        47515,
        47519,
        47523,
        47527,
    ]


def test_valid_block_accepts_well_formed_windows():
    assert registers.valid_block(
        [0x0400, 0x0600, 0xFF9C, 0xFF7F, 0x0C1E, 0x0F1E, 0xFFE2, 0x007F]
        + [0x0A2D, 0x0D2D, 0x0000, 0x0020, 0x160F, 0x1700, 0x0000, 0x007F]
    )
    # a fully disabled / zeroed block is still structurally valid
    assert registers.valid_block([0] * 16)


def test_valid_block_rejects_foreign_data():
    # Runtime block from the official integration: 0x1A0B is 26:11, not a time.
    foreign = [
        0x1A0B,
        0x110B,
        0x2935,
        0x07AC,
        0x0002,
        0x0000,
        0x0026,
        0x0796,
        0x0001,
        0x0000,
        0x0012,
        0x0000,
        0x0000,
        0x0000,
        0x0000,
        0x0000,
    ]
    assert not registers.valid_block(foreign)


def test_valid_block_rejects_bad_length_and_fields():
    assert not registers.valid_block([0] * 15)
    # minute out of range
    assert not registers.valid_block(
        [0x0460, 0x0600, 0xFF9C, 0xFF7F] + [0] * 12
    )
    # power out of range
    assert not registers.valid_block(
        [0x0400, 0x0600, 0x0064 + 1, 0xFF7F] + [0] * 12
    )
    # switch high byte neither 0x00 nor 0xFF
    assert not registers.valid_block(
        [0x0400, 0x0600, 0xFF9C, 0x017F] + [0] * 12
    )

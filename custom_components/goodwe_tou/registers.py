"""Encode/decode helpers for the GoodWe Time-of-Use registers."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .const import (
    ALL_DAYS_MASK,
    DAY_NAMES,
    WEEKDAY_MASK,
    WEEKEND_MASK,
    WINDOW_BASES,
)


@dataclass
class TouWindow:
    """One Time-of-Use window as exposed to Home Assistant."""

    start: dt.time
    stop: dt.time
    mode: str = "off"
    power: int = 100
    days: str = "mon,tue,wed,thu,fri,sat,sun"

    @property
    def mask(self) -> int:
        """Day bitmask (Sun = bit 0 .. Sat = bit 6)."""
        return days_to_mask(self.days)

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def signed_power(self) -> int:
        """Signed percentage: negative charges, positive discharges, 0 idles."""
        if self.mode == "charge":
            return -abs(self.power)
        if self.mode == "discharge":
            return abs(self.power)
        return 0

    @classmethod
    def from_signed_power(
        cls,
        *,
        start: dt.time,
        stop: dt.time,
        signed_power: int,
        days: str,
    ) -> TouWindow:
        """Build a window from a signed power percentage.

        ``signed_power < 0`` charges, ``> 0`` discharges and ``0`` disables the
        window. The magnitude is clamped to ``0..100``.
        """
        value = int(signed_power)
        magnitude = min(100, abs(value))
        if value < 0:
            mode = "charge"
        elif value > 0:
            mode = "discharge"
        else:
            mode, magnitude = "off", 0
        return cls(start=start, stop=stop, mode=mode, power=magnitude, days=days)


def encode_time(value: dt.time) -> int:
    """``HH:MM`` packed into a register (``hour * 256 + minute``)."""
    return value.hour * 256 + value.minute


def decode_time(register: int) -> dt.time:
    """Decode a packed ``HH:MM`` register into a :class:`datetime.time`."""
    register &= 0xFFFF
    hour, minute = register >> 8, register & 0xFF
    if hour > 23:
        hour = 0
    if minute > 59:
        minute = 0
    return dt.time(hour=hour, minute=minute)


def encode_power(mode: str, power: int) -> int:
    """Signed power register: negative charges, positive discharges, 0 idles."""
    power = max(0, min(100, int(power)))
    if mode == "charge":
        return -power
    if mode == "discharge":
        return power
    return 0


def decode_power(register: int) -> tuple[str, int]:
    """Return ``(mode, power)`` for a signed power register."""
    signed = register - 0x10000 if register >= 0x8000 else register
    if signed < 0:
        return "charge", abs(signed)
    if signed > 0:
        return "discharge", signed
    return "off", 0


def encode_switch(mode: str, mask: int) -> int:
    """High byte = on/off switch, low byte = day bitmask."""
    switch = 0x00 if mode == "off" else 0xFF
    return (switch << 8) | (mask & 0xFF)


def decode_switch(register: int) -> tuple[bool, int]:
    """Return ``(enabled, day_mask)`` for a switch register."""
    register &= 0xFFFF
    return (register >> 8) != 0, register & 0xFF


def days_to_mask(text: str) -> int:
    """Parse a human day description into a bitmask.

    Accepts ``all``/``every``/``mon-sun``, ``weekdays``/``weekday``,
    ``weekend``, ``none``, or a comma/space separated list of day names.
    """
    normalized = (text or "").strip().lower()
    if not normalized or normalized in {"none", "off", "-"}:
        return 0
    if "all" in normalized or "every" in normalized or "mon-sun" in normalized:
        return ALL_DAYS_MASK
    if "weekday" in normalized:
        return WEEKDAY_MASK
    if "weekend" in normalized:
        return WEEKEND_MASK
    mask = 0
    for index, name in enumerate(DAY_NAMES):
        if name in normalized:
            mask |= 1 << index
    return mask


def day_bit(day: dt.date | int) -> int:
    """The inverter's day bit for a date or weekday index.

    Sunday is bit 0 and Saturday bit 6, so this is *not* the same as
    ``1 << day.weekday()`` (which numbers Monday 0). An :class:`int` is treated
    as a Python weekday index (Monday 0 .. Sunday 6).
    """
    weekday = day.weekday() if isinstance(day, dt.date) else int(day)
    return 1 << ((weekday + 1) % 7)


def mask_to_days(mask: int) -> str:
    """Format a bitmask as a comma separated list of day names."""
    mask &= 0xFF
    names = [name for index, name in enumerate(DAY_NAMES) if mask & (1 << index)]
    return ",".join(names) if names else "none"


def decode_window(base: int, registers: list[int]) -> TouWindow:
    """Decode the four registers of one window."""
    mode, power = decode_power(registers[2])
    enabled, mask = decode_switch(registers[3])
    if not enabled:
        mode = "off"
        # keep the stored magnitude so re-enabling restores it
        stored = abs(registers[2] - 0x10000 if registers[2] >= 0x8000 else registers[2])
        power = stored or power
    return TouWindow(
        start=decode_time(registers[0]),
        stop=decode_time(registers[1]),
        mode=mode,
        power=power,
        days=mask_to_days(mask),
    )


def window_registers(window: TouWindow) -> dict[int, int]:
    """Return the ``{relative_register: value}`` map for a window."""
    return {
        0: encode_time(window.start),
        1: encode_time(window.stop),
        2: encode_power(window.mode, window.power) & 0xFFFF,
        3: encode_switch(window.mode, window.mask),
    }


def base_for(window_number: int) -> int:
    """Base register for a 1-based window number."""
    return WINDOW_BASES[window_number]


def valid_block(registers: list[int]) -> bool:
    """Whether a block looks like well-formed Time-of-Use windows.

    A read response does not say which registers it answers, so when the dongle
    is shared with another client a frame answering somebody else's request can
    land in our datagram. Foreign data almost never satisfies the time, switch
    and power ranges checked here, so this cheap test rejects it before it can
    overwrite the entities.
    """
    count = 4 * len(WINDOW_BASES)
    if len(registers) != count:
        return False
    for index in range(0, count, 4):
        start, stop, power, switch = registers[index : index + 4]
        if (start >> 8) > 23 or (start & 0xFF) > 59:
            return False
        if (stop >> 8) > 23 or (stop & 0xFF) > 59:
            return False
        signed = power - 0x10000 if power >= 0x8000 else power
        if not -100 <= signed <= 100:
            return False
        if (switch >> 8) not in (0x00, 0xFF):
            return False
    return True

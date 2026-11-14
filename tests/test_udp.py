"""Unit tests for the UDP framing/parsing (no network access)."""

from __future__ import annotations

import pytest
from conftest import load

udp = load("goodwe_udp")

ADDR = 0xF7


def _frame(payload: bytes) -> bytes:
    """Wrap ``payload`` (addr..data) in the 0xAA55 header and append the CRC."""
    body = bytes([ADDR]) + payload
    crc = udp.crc16(body)
    return b"\xaa\x55" + body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def test_build_request_layout_and_crc():
    frame = udp._build_request(ADDR, udp.READ_CMD, 47515, 16)
    assert len(frame) == 8
    assert frame[0] == ADDR
    assert frame[1] == udp.READ_CMD
    assert frame[2:4] == (47515).to_bytes(2, "big")
    assert frame[4:6] == (16).to_bytes(2, "big")
    assert udp.crc16(frame[:6]) == int.from_bytes(frame[6:], "little")


def test_parse_read_response():
    data = [0x0C1E, 0x0F1E]
    payload = bytes([udp.READ_CMD, len(data) * 2])
    for reg in data:
        payload += reg.to_bytes(2, "big")
    frame, partial = udp.GoodweUdpClient._parse(
        _frame(payload), udp.READ_CMD, 47515, 2
    )
    assert frame is not None
    assert partial is False


def test_parse_oversized_datagram():
    payload = bytes([udp.READ_CMD, 2]) + (0x0400).to_bytes(2, "big")
    oversized = _frame(payload) + b"\x00" * 40
    frame, partial = udp.GoodweUdpClient._parse(
        oversized, udp.READ_CMD, 47515, 1
    )
    assert frame is not None
    assert partial is False


def test_parse_fragment_is_partial():
    frame = _frame(bytes([udp.READ_CMD, 2]) + (0x0400).to_bytes(2, "big"))
    frame, partial = udp.GoodweUdpClient._parse(
        frame[:4], udp.READ_CMD, 47515, 1
    )
    assert frame is None
    assert partial is True


def test_parse_bad_crc_rejected():
    frame = bytearray(_frame(bytes([udp.READ_CMD, 2]) + (0x0400).to_bytes(2, "big")))
    frame[-1] ^= 0xFF
    parsed, partial = udp.GoodweUdpClient._parse(
        bytes(frame), udp.READ_CMD, 47515, 1
    )
    assert parsed is None
    assert partial is False


def test_parse_exception_response():
    body = bytes([ADDR, udp.READ_CMD | 0x80, 0x02])
    crc = udp.crc16(body)
    frame = b"\xaa\x55" + body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])
    with pytest.raises(udp.GoodweRejectedError):
        udp.GoodweUdpClient._parse(frame, udp.READ_CMD, 47515, 1)


def test_parse_write_echo():
    value = 0xFF9C
    payload = bytes([udp.WRITE_CMD]) + (47529).to_bytes(2, "big") + value.to_bytes(
        2, "big"
    )
    frame, partial = udp.GoodweUdpClient._parse(
        _frame(payload), udp.WRITE_CMD, 47529, value
    )
    assert frame is not None
    assert partial is False


def test_parse_write_echo_mismatch():
    payload = (
        bytes([udp.WRITE_CMD]) + (47529).to_bytes(2, "big") + (0x0001).to_bytes(2, "big")
    )
    parsed, partial = udp.GoodweUdpClient._parse(
        _frame(payload), udp.WRITE_CMD, 47529, 0xFF9C
    )
    assert parsed is None
    assert partial is False

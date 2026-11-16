"""Minimal, dependency-free GoodWe UDP (port 8899) Modbus/RTU client.

The GoodWe Wi-Fi/LAN dongle speaks a Modbus/RTU variant over UDP:

* Request (8 bytes): ``[addr][cmd][off_hi][off_lo][val_hi][val_lo][crc_lo][crc_hi]``
* Response: ``0xAA 0x55 [addr][cmd] ... [crc_lo][crc_hi]``

``cmd`` is ``0x03`` to read holding registers and ``0x06`` to write a single
register. The CRC is the standard Modbus CRC-16 (poly ``0xA001``) computed over
the frame without the two leading ``0xAA 0x55`` bytes and without the CRC itself.

The dongle is unreliable: it occasionally answers with an oversized datagram, a
fragmented one, or not at all. A response is therefore only accepted once a
CRC-valid frame has been parsed out of it, and each request is retried a few
times before giving up.
"""

from __future__ import annotations

import socket
import time
from typing import Final

DEFAULT_PORT: Final = 8899
DEFAULT_COMM_ADDR: Final = 0xF7
DEFAULT_TIMEOUT: Final = 1.0
DEFAULT_RETRIES: Final = 3

READ_CMD: Final = 0x03
WRITE_CMD: Final = 0x06

_HEADER: Final = b"\xaa\x55"


class GoodweError(Exception):
    """Base class for GoodWe communication errors."""


class GoodweConnectionError(GoodweError):
    """Raised when the dongle cannot be reached."""


class GoodweTimeoutError(GoodweConnectionError):
    """Raised when no valid answer arrives in time."""


class GoodweProtocolError(GoodweError):
    """Raised when a response is malformed or rejected."""


class GoodweRejectedError(GoodweProtocolError):
    """Raised when the inverter answers with a Modbus exception code."""


# --- CRC-16 (Modbus) -------------------------------------------------------


def _build_crc_table() -> tuple[int, ...]:
    table = []
    for i in range(256):
        buf = i << 1
        crc = 0
        for _ in range(8, 0, -1):
            buf >>= 1
            if (buf ^ crc) & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
        table.append(crc)
    return tuple(table)


_CRC_TABLE: Final = _build_crc_table()

_FAILURE_CODES: Final = {
    1: "illegal function",
    2: "illegal data address",
    3: "illegal data value",
    4: "slave device failure",
    5: "acknowledge",
    6: "slave device busy",
}


def crc16(data: bytes) -> int:
    """Return the Modbus CRC-16 of ``data``."""
    crc = 0xFFFF
    for byte in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ byte) & 0xFF]
    return crc


def _build_request(comm_addr: int, cmd: int, offset: int, value: int) -> bytes:
    frame = bytearray(6)
    frame[0] = comm_addr & 0xFF
    frame[1] = cmd & 0xFF
    frame[2] = (offset >> 8) & 0xFF
    frame[3] = offset & 0xFF
    frame[4] = (value >> 8) & 0xFF
    frame[5] = value & 0xFF
    checksum = crc16(frame)
    frame.append(checksum & 0xFF)
    frame.append((checksum >> 8) & 0xFF)
    return bytes(frame)


class GoodweUdpClient:
    """Small synchronous client for the GoodWe UDP dongle protocol."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        comm_addr: int = DEFAULT_COMM_ADDR,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.host = host
        self.port = port
        self.comm_addr = comm_addr
        self.timeout = timeout
        self.retries = retries

    # -- low level ---------------------------------------------------------

    def _transceive(self, cmd: int, offset: int, value: int) -> bytes:
        """Send one request and return the raw datagram of the valid answer."""
        request = _build_request(self.comm_addr, cmd, offset, value)
        last_error: GoodweTimeoutError | None = None
        for _ in range(self.retries + 1):
            try:
                return self._attempt(request, cmd, offset, value)
            except GoodweTimeoutError as err:
                last_error = err
        assert last_error is not None
        raise last_error

    def _attempt(self, request: bytes, cmd: int, offset: int, value: int) -> bytes:
        """One send/receive attempt, tolerating fragmented answers."""
        deadline = time.monotonic() + self.timeout
        partial = b""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            try:
                sock.sendto(request, (self.host, self.port))
            except OSError as err:  # pragma: no cover - environment specific
                raise GoodweConnectionError(str(err)) from err

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GoodweTimeoutError(
                        f"no valid answer from {self.host}:{self.port}"
                    )
                sock.settimeout(remaining)
                try:
                    data, _ = sock.recvfrom(2048)
                except TimeoutError as err:
                    raise GoodweTimeoutError(
                        f"no valid answer from {self.host}:{self.port}"
                    ) from err
                except OSError as err:  # pragma: no cover - environment specific
                    raise GoodweConnectionError(str(err)) from err

                frame, is_partial = self._parse(partial + data, cmd, offset, value)
                if frame is not None:
                    return frame
                partial = partial + data if is_partial else b""

    @staticmethod
    def _parse(
        data: bytes, cmd: int, offset: int, value: int
    ) -> tuple[bytes | None, bool]:
        """Validate a datagram.

        Returns ``(frame, is_partial)`` where ``frame`` is the valid answer when
        one could be parsed, and ``is_partial`` signals that ``data`` looks like
        the start of an answer that has not fully arrived yet.
        """
        if len(data) < 2 or data[0:2] != _HEADER:
            return None, False
        if len(data) < 4:
            return None, True

        resp_cmd = data[3]
        if resp_cmd & 0x80:  # Modbus exception
            code = data[4] if len(data) > 4 else 0
            raise GoodweRejectedError(
                f"inverter rejected request: {_FAILURE_CODES.get(code, 'unknown')} "
                f"({code})"
            )

        if resp_cmd == READ_CMD:
            if len(data) < 5:
                return None, True
            expected = 5 + data[4] + 2
        elif resp_cmd == WRITE_CMD:
            expected = 10
        else:
            return None, False

        if len(data) < expected:
            return None, True

        frame = data[:expected]
        if crc16(frame[2:-2]) != int.from_bytes(frame[-2:], "little"):
            return None, False

        if resp_cmd == WRITE_CMD:
            resp_offset = int.from_bytes(frame[4:6], "big")
            resp_value = int.from_bytes(frame[6:8], "big")
            if resp_offset != offset or resp_value != (value & 0xFFFF):
                return None, False

        return frame, False

    # -- public API --------------------------------------------------------

    def read_registers(self, offset: int, count: int) -> list[int]:
        """Read ``count`` consecutive holding registers starting at ``offset``."""
        frame = self._transceive(READ_CMD, offset, count)
        payload = frame[5:-2]
        return [int.from_bytes(payload[i * 2 : i * 2 + 2], "big") for i in range(count)]

    def write_register(self, register: int, value: int) -> None:
        """Write a single holding register (value masked to 16 bits)."""
        self._transceive(WRITE_CMD, register, value & 0xFFFF)


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import sys

    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.20.13"
    client = GoodweUdpClient(host)
    print("work mode:", client.read_registers(47000, 1))
    regs = client.read_registers(47515, 16)
    print("tou regs:", [hex(r) for r in regs])

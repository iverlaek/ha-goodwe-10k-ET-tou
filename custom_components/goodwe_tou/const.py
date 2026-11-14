"""Constants for the GoodWe Time-of-Use integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "goodwe_tou"
MANUFACTURER: Final = "GoodWe"
MODEL: Final = "Time-of-Use (Eco Mode)"

# Config entry keys / defaults.
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_COMM_ADDR: Final = "comm_addr"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_HOST: Final = "192.168.20.13"
DEFAULT_PORT: Final = 8899
DEFAULT_COMM_ADDR: Final = 0xF7
DEFAULT_SCAN_INTERVAL: Final = 60
DEFAULT_TIMEOUT: Final = 1.0
DEFAULT_RETRIES: Final = 3

# Modbus registers.
WORK_MODE_REGISTER: Final = 47000
ECO_WORK_MODE: Final = 3
GENERAL_WORK_MODE: Final = 0

# The four "Eco Mode" / Time-of-Use windows (ARM firmware < 19, 8-byte layout).
WINDOW_BASES: Final = {1: 47515, 2: 47519, 3: 47523, 4: 47527}
WINDOWS: Final = tuple(WINDOW_BASES)

MODES: Final = ["off", "charge", "discharge"]

DAY_NAMES: Final = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ALL_DAYS_MASK: Final = 0x7F
WEEKDAY_MASK: Final = 0x1F
WEEKEND_MASK: Final = 0x60

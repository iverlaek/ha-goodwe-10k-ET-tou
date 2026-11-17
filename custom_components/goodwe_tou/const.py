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

# Options / service tuning for the cheapest-hours planner.
CONF_PRICE_ENTITY: Final = "price_entity"
CONF_SOC_ENTITY: Final = "soc_entity"
CONF_PV_ENTITY: Final = "pv_entity"
CONF_CAPACITY_KWH: Final = "capacity_kwh"
CONF_POWER_KW: Final = "power_kw"
CONF_CHARGE_KW: Final = "charge_kw"
CONF_DISCHARGE_KW: Final = "discharge_kw"
CONF_STANDBY_KW: Final = "standby_kw"
CONF_EXPORT_HOURS: Final = "export_hours"
CONF_TARGET_SOC: Final = "target_soc"
CONF_MIN_SOC: Final = "min_soc"
CONF_PV_FACTOR: Final = "pv_factor"
CONF_AUTO_REPLAN: Final = "auto_replan"

DEFAULT_PRICE_ENTITY: Final = "sensor.average_electricity_price"
DEFAULT_SOC_ENTITY: Final = "sensor.battery_state_of_charge"
DEFAULT_PV_ENTITY: Final = "sensor.energy_production_today_remaining"
DEFAULT_CAPACITY_KWH: Final = 24.0
#: Legacy combined charge/discharge power, kept as the fallback for both.
DEFAULT_POWER_KW: Final = 7.0
#: The GoodWe can put at most ~6 kW into the battery (AC + DC together).
DEFAULT_CHARGE_KW: Final = 6.0
#: The GoodWe can take at most ~7 kW out of the battery.
DEFAULT_DISCHARGE_KW: Final = 7.0
#: Standby consumption of the house, drawn from the battery in ECO mode.
DEFAULT_STANDBY_KW: Final = 1.2
#: How long the battery may export to the grid at full power in a bad solar
#: period before it must be kept for the house.
DEFAULT_EXPORT_HOURS: Final = 1.0
DEFAULT_TARGET_SOC: Final = 100
DEFAULT_MIN_SOC: Final = 20
DEFAULT_PV_FACTOR: Final = 0.5
#: Re-check the plan every quarter hour and apply it automatically.
DEFAULT_AUTO_REPLAN: Final = True

# Price slots are 15 minutes in the ENTSO-e attribute.
SLOT_MINUTES: Final = 15

# Modbus registers.
WORK_MODE_REGISTER: Final = 47000
ECO_WORK_MODE: Final = 3
GENERAL_WORK_MODE: Final = 0

# The four "Eco Mode" / Time-of-Use windows (ARM firmware < 19, 8-byte layout).
WINDOW_BASES: Final = {1: 47515, 2: 47519, 3: 47523, 4: 47527}
WINDOWS: Final = tuple(WINDOW_BASES)

DAY_NAMES: Final = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ALL_DAYS_MASK: Final = 0x7F
WEEKDAY_MASK: Final = 0x1F
WEEKEND_MASK: Final = 0x60

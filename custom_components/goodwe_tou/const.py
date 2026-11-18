"""Constants for the GoodWe Time-of-Use integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "goodwe_tou"
MANUFACTURER: Final = "GoodWe"
MODEL: Final = "Time-of-Use (Eco Mode)"
#: The device name every entity hangs under, and its slug. The slug is part of
#: every entity id, so it must stay stable even though the entity names are
#: translated.
DEVICE_NAME: Final = "GoodWe Time-of-Use"
DEVICE_SLUG: Final = "goodwe_time_of_use"

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
CONF_PRICE_SOURCE: Final = "price_source"
CONF_PRICE_ENTITY: Final = "price_entity"
CONF_SOC_ENTITY: Final = "soc_entity"
CONF_PV_ENTITY: Final = "pv_entity"
CONF_CAPACITY_KWH: Final = "capacity_kwh"
CONF_POWER_KW: Final = "power_kw"
CONF_CHARGE_KW: Final = "charge_kw"
CONF_DISCHARGE_KW: Final = "discharge_kw"
CONF_STANDBY_KW: Final = "standby_kw"
CONF_EXPORT_HOURS: Final = "export_hours"
CONF_EXPORT_LIMIT: Final = "export_limit"
CONF_TARGET_SOC: Final = "target_soc"
CONF_MIN_SOC: Final = "min_soc"
CONF_PV_FACTOR: Final = "pv_factor"
CONF_RTE: Final = "rte"
CONF_AUTO_REPLAN: Final = "auto_replan"
CONF_CHARGE_WINDOWS: Final = "charge_windows"
CONF_DISCHARGE_WINDOWS: Final = "discharge_windows"
CONF_WINDOW_POWER: Final = "window_power"
CONF_RECHECK_MINUTES: Final = "recheck_minutes"
CONF_LOG_SIZE: Final = "log_size"
CONF_MIN_CELL_VOLTAGE: Final = "min_cell_voltage"
#: The window the manual charge/discharge buttons drive (start/stop/days are the
#: user's; the buttons only flip its direction and power).
CONF_MANUAL_WINDOW: Final = "manual_window"
CONF_TIBBER_PRICES: Final = "tibber_prices"
CONF_TIBBER_HOME: Final = "tibber_home"
#: Deviation tracking / adaptive house load. The planner's standby load is
#: adjusted for the weather (cold makes the air conditioner work harder) and
#: learned from a log of the deviations between plan and reality.
CONF_WEATHER_ENTITY: Final = "weather_entity"
CONF_LOAD_ENTITY: Final = "load_entity"
CONF_PV_POWER_ENTITY: Final = "pv_power_entity"
CONF_ADAPTIVE_LOAD: Final = "adaptive_load"
CONF_TEMP_COEFF: Final = "temp_coeff"
CONF_DEGREE_BASE_C: Final = "degree_base_c"
CONF_DEVIATION_SIZE: Final = "deviation_size"
#: The language the integration speaks: the planner's reasoning, the activity
#: log and the entity names. Chosen at installation, changeable in the options.
CONF_LANGUAGE: Final = "language"

DEFAULT_PRICE_ENTITY: Final = "sensor.average_electricity_price"
#: Where the planner reads its forecast: an existing price entity ("entity",
#: ENTSO-e/Nordpool style) or the Tibber prices the integration fetches itself.
DEFAULT_PRICE_SOURCE: Final = "entity"
PRICE_SOURCE_ENTITY: Final = "entity"
PRICE_SOURCE_TIBBER: Final = "tibber"
#: Options allowed for the price source.
PRICE_SOURCES: Final = (PRICE_SOURCE_ENTITY, PRICE_SOURCE_TIBBER)
DEFAULT_SOC_ENTITY: Final = "sensor.battery_state_of_charge"
DEFAULT_PV_ENTITY: Final = "sensor.energy_production_today_remaining"
DEFAULT_CAPACITY_KWH: Final = 24.0
#: Legacy combined charge/discharge power, kept as the fallback for both.
DEFAULT_POWER_KW: Final = 7.0
#: The GoodWe can put at most ~6 kW into the battery (AC + DC together).
DEFAULT_CHARGE_KW: Final = 6.0
#: The GoodWe can take at most ~6 kW out of the battery over AC.
DEFAULT_DISCHARGE_KW: Final = 6.0
#: Standby consumption of the house, drawn from the battery in ECO mode.
DEFAULT_STANDBY_KW: Final = 1.2
#: How long the battery may export to the grid at full power in a bad solar
#: period before it must be kept for the house.
DEFAULT_EXPORT_HOURS: Final = 1.0
#: Maximum power (W) the inverter may feed back to the grid. The GoodWe blocks
#: *all* export when this is 0, which also stops the discharge windows: the
#: battery then only covers the house load. Default to the inverter's AC rating.
DEFAULT_EXPORT_LIMIT: Final = 10000
DEFAULT_TARGET_SOC: Final = 100
DEFAULT_MIN_SOC: Final = 20
DEFAULT_PV_FACTOR: Final = 0.5
#: Round-trip efficiency of the battery (energy in vs energy out).
DEFAULT_RTE: Final = 0.85
#: Re-check the plan every quarter hour and apply it automatically.
DEFAULT_AUTO_REPLAN: Final = True
#: How many of the four windows the planner may use for charging and discharging.
DEFAULT_CHARGE_WINDOWS: Final = 2
DEFAULT_DISCHARGE_WINDOWS: Final = 2
#: Power percentage written into the planned windows.
DEFAULT_WINDOW_POWER: Final = 100
#: Which window the manual charge/discharge buttons flip.
DEFAULT_MANUAL_WINDOW: Final = 4
#: How often the plan is re-checked, in minutes (has to divide an hour).
DEFAULT_RECHECK_MINUTES: Final = 15
#: Options allowed for the re-check interval, in minutes.
RECHECK_CHOICES: Final = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60)
#: How many entries the activity log keeps (newest first).
DEFAULT_LOG_SIZE: Final = 1000
#: Hard upper bound for the activity log size.
MAX_LOG_SIZE: Final = 10000
#: Minimum cell voltage (mV) that stops the automation. The state of charge is
#: often unreliable, so a cell near empty is the real "battery is flat" signal.
DEFAULT_MIN_CELL_VOLTAGE: Final = 3000
#: Expose the Tibber day-ahead prices as a sensor (for the price card). Enabled by
#: default; the sensor simply stays empty when the Tibber integration is absent.
DEFAULT_TIBBER_PRICES: Final = True
#: Which Tibber home to read when the account has more than one; empty = the first.
DEFAULT_TIBBER_HOME: Final = ""
#: Weather entity for the outdoor temperature, humidity and wind. Home Assistant
#: computes the feels-like (apparent) temperature from those, so no separate
#: sensor is needed.
DEFAULT_WEATHER_ENTITY: Final = "weather.forecast_home"
#: The sensor with the house's real consumption, compared with the plan.
DEFAULT_LOAD_ENTITY: Final = "sensor.house_consumption"
#: The PV power sensor, for the forecast-error part of the deviation log.
DEFAULT_PV_POWER_ENTITY: Final = "sensor.pv_power"
#: Adjust the planner's house load for the weather and learn it from the log.
DEFAULT_ADAPTIVE_LOAD: Final = True
#: Extra house load (kW) per degree the feels-like temperature is below the
#: degree-day base. The deviation log refines this automatically.
DEFAULT_TEMP_COEFF: Final = 0.05
#: Heating degree days are counted below this outdoor (feels-like) temperature.
DEFAULT_DEGREE_BASE_C: Final = 15.5
#: How many hourly deviation samples to keep (newest first). The raw sensor
#: history stays in Home Assistant's recorder; only these derived samples are
#: retained, and they may be kept much longer.
DEFAULT_DEVIATION_SIZE: Final = 2160
#: Hard upper bound for the deviation log size.
MAX_DEVIATION_SIZE: Final = 10000
#: The default language: Dutch, because the integration grew up in a Dutch home.
#: The install step pre-selects the Home Assistant language when we support it.
DEFAULT_LANGUAGE: Final = "nl"
LANGUAGE_NL: Final = "nl"
LANGUAGE_EN: Final = "en"
#: The languages the integration speaks.
LANGUAGES: Final = (LANGUAGE_NL, LANGUAGE_EN)

# Price slots are 15 minutes in the ENTSO-e attribute.
SLOT_MINUTES: Final = 15

# Modbus registers.
WORK_MODE_REGISTER: Final = 47000
ECO_WORK_MODE: Final = 3
GENERAL_WORK_MODE: Final = 0

#: Battery cell voltages in mV (37022/37023). The low-cell guard reads both and
#: takes the lowest, because the firmware order is not guaranteed.
CELL_VOLTAGE_REGISTERS: Final = (37022, 37023)

# "Feed Power" enable + limit (official GoodWe EMS protocol). The limit has to be
# greater than 0, otherwise the inverter refuses to export and the discharge
# windows only cover the house load instead of feeding the grid.
EXPORT_ENABLE_REGISTER: Final = 47509
EXPORT_LIMIT_REGISTER: Final = 47510
EXPORT_ENABLED: Final = 1

# The four "Eco Mode" / Time-of-Use windows (ARM firmware < 19, 8-byte layout).
WINDOW_BASES: Final = {1: 47515, 2: 47519, 3: 47523, 4: 47527}
WINDOWS: Final = tuple(WINDOW_BASES)

#: Day names in the inverter's bit order. The GoodWe "Work Week" register
#: (e.g. 47518) documents its low byte as "0~6bit means Sunday~Saturday", so
#: bit 0 is Sunday and bit 6 is Saturday. OpenEMS and the ``goodwe`` library
#: use the same order.
DAY_NAMES: Final = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
ALL_DAYS_MASK: Final = 0x7F
#: Monday..Friday = bits 1..5.
WEEKDAY_MASK: Final = 0x3E
#: Saturday (bit 6) plus Sunday (bit 0).
WEEKEND_MASK: Final = 0x41

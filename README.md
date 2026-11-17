# GoodWe Time-of-Use (Eco Mode) for Home Assistant

A small, self-contained Home Assistant custom integration that reads and writes
the four **Time-of-Use / Eco Mode** windows of a GoodWe hybrid inverter directly
over the local network — no cloud, no SEMS portal, no dependency on another
integration.

It talks the GoodWe Wi-Fi/LAN dongle protocol (Modbus/RTU over UDP port `8899`)
itself, and exposes each window as native Home Assistant entities so you can
automate them from scripts, automations and dashboards.

> Built and tested against a **GoodWe GW10K-ET** (ARM firmware 17, 8-byte TOU
> layout) with a Wi-Fi/LAN Kit 2.0 dongle. Other ET-series hybrids that use the
> same register map should work.

## Features

- The 4 Time-of-Use windows as entities: **start time**, **stop time**, a
  **signed power** percentage (`-100` charge … `0` off … `+100` discharge) and
  **days**.
- A **Read from inverter** button that pulls the live schedule back into HA.
- An **Apply to inverter** button that writes the schedule in HA back to the
  inverter, with a read-back verification and automatic retries (the dongle is
  flaky).
- A **Set cheapest hours** button and a `goodwe_tou.set_cheapest_hours` service
  that plan the four windows from an electricity price forecast: charge in the
  cheapest hours and discharge in the most expensive ones, taking the current
  state of charge and the PV forecast into account.
- A **bundled Lovelace card** (`custom:goodwe-tou-card`) that shows all four
  windows in one tile with red↔green sliders and a *Cheapest hours* button.
- A **planner card** (`custom:goodwe-tou-planner-card`) that lays the four
  windows on a single *now → midnight tomorrow* timeline with draggable
  charge/discharge bars. Tomorrow is greyed out until tomorrow's prices are
  known, overlapping windows are impossible, and a window that crosses midnight
  is split into two inverter windows on apply.
- A `goodwe_tou.set_windows` service that writes an explicit plan (start, stop,
  signed power, days) in a single verified request — used by the planner card.
- An **ECO mode** switch (`work_mode = 3`); Time-of-Use only runs in ECO mode.
- Local polling with configurable interval, retries and a config flow — no YAML.
- Robust against a dongle that is **shared** with another integration: reads
  ignore foreign frames (two matching reads plus a structural check) and every
  write is confirmed by reading it back, so a change cannot silently revert.

## Installation

### Via the Home Assistant Marketplace (HACS)

1. Open **Settings → Devices & services → Marketplace**.
2. Choose **Add repository** / **Add from link** and paste:
   `https://github.com/iverlaek/ha-goodwe-10k-ET-tou`
3. Install **GoodWe Time-of-Use (Eco Mode)** and restart Home Assistant.

### Manually

Copy the `custom_components/goodwe_tou` folder into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

## Configuration

**Settings → Devices & services → Add integration → GoodWe Time-of-Use**.

| Field | Default | Description |
| --- | --- | --- |
| Host | `192.168.20.13` | IP address of the GoodWe Wi-Fi/LAN dongle |
| UDP port | `8899` | GoodWe local protocol port |
| Modbus address | `247` | Communication address of the inverter |

The flow probes the inverter before creating the entry, so a wrong host or a
dongle that is offline is reported immediately.

The poll interval (seconds, `0` to disable polling) can be changed in the
integration **Configure** dialog.

## Entities

With the default device name the entities are:

| Entity | Purpose |
| --- | --- |
| `time.goodwe_time_of_use_window_1_start` … `_4_start` | Window start time |
| `time.goodwe_time_of_use_window_1_stop` … `_4_stop` | Window stop time |
| `number.goodwe_time_of_use_window_1_power` … `_4_power` | Signed power % (`-100` charge … `+100` discharge, `0` off) |
| `text.goodwe_time_of_use_window_1_days` … `_4_days` | Active days |
| `switch.goodwe_time_of_use_eco_mode` | ECO work mode on/off |
| `button.goodwe_time_of_use_read_from_inverter` | Read the schedule from the inverter |
| `button.goodwe_time_of_use_apply_to_inverter` | Apply the schedule to the inverter (verified) |
| `button.goodwe_time_of_use_set_cheapest_hours` | Plan + apply the cheapest hours |

Entity IDs are derived from the device name, so they may differ if you rename the
device. Always pick entities from the UI rather than hard-coding the IDs above.

### The `days` text entity

The **days** entity accepts:

- `all` / `every` / `mon-sun` — every day
- `weekdays` — Monday to Friday
- `weekend` — Saturday and Sunday
- `none` — no days (window never runs)
- a comma separated list such as `mon,wed,fri` or `sat,sun`

### Charging vs discharging

The **power** number is *signed*:

- a **negative** value charges the battery (the inverter stores a negative
  percentage),
- `0` disables the window,
- a **positive** value discharges the battery.

Setting a non-zero value also enables the window's switch; setting `0` turns it
off. The integration handles the sign for you.

## The bundled card

The integration ships a Lovelace card that puts all four windows in a single
tile: start/stop time pickers, a red↔green slider per window, weekday chips and
**Read** / **Cheapest hours** / **Apply** buttons. It is served at
`/goodwe_tou/goodwe-tou-card.js` and loaded automatically, so you only have to
add it to a dashboard:

```yaml
type: custom:goodwe-tou-card
title: GoodWe Time-of-Use        # optional
prefix: goodwe_time_of_use       # optional entity-id prefix
```

A ready-to-paste dashboard is in
[`dashboards/goodwe_tou.yaml`](dashboards/goodwe_tou.yaml).

## The planner card

`custom:goodwe-tou-planner-card` is a *planner* view for the same four windows.
Instead of a fixed start/stop pair per window it draws one timeline that starts
at the current quarter hour and ends at midnight at the end of tomorrow, and
renders each window as a draggable bar on it:

- drag the **left** and **right** handles to move a window's start and stop;
- the **Laden / Ontladen** button toggles a window between charging and
  discharging, `✕` turns it off, and the number next to it is the power
  percentage;
- the bars snap to quarter hours and **cannot overlap** — a handle stops at the
  neighbouring window;
- **tomorrow is greyed out** until the configured price entity exposes prices
  for tomorrow (usually around 15:00);
- **Goedkoopste uren** calls the integration's cheapest-hours planner;
- **Toepassen** writes the whole plan at once through `goodwe_tou.set_windows`.
  A bar that crosses midnight is split into two inverter windows (one ending at
  `23:59`, one starting at `00:00`), because the inverter stores one window per
  day.

```yaml
type: custom:goodwe-tou-planner-card
title: Laadplanning                                  # optional
prefix: goodwe_time_of_use                           # optional
price_entity: sensor.average_electricity_price       # optional, for the grey-out
slot_minutes: 15                                     # optional, timeline granularity
```

## Cheapest hours

The **Set cheapest hours** button (and the `goodwe_tou.set_cheapest_hours`
service) reads the quarter-hourly price forecast from a price entity, works out
how much energy the battery still needs (from the state of charge, the battery
capacity, the charge power and the remaining PV forecast) and fills the four
windows with the cheapest charging hours and the most expensive discharging
hours.

The defaults can be changed in the integration **Configure** dialog or passed to
the service:

```yaml
action: goodwe_tou.set_cheapest_hours
data:
  price_entity: sensor.average_electricity_price
  soc_entity: sensor.battery_state_of_charge
  pv_entity: sensor.energy_production_today_remaining
  capacity_kwh: 24
  power_kw: 7
  target_soc: 100
  min_soc: 20
  pv_factor: 0.5
```

| Option | Default | Meaning |
| --- | --- | --- |
| `price_entity` | `sensor.average_electricity_price` | Entity exposing the quarter-hourly forecast — ENTSO-e (`prices` with `{time, price}`) or Nordpool (`raw_today`/`raw_tomorrow` with `{start, value}`) |
| `soc_entity` | `sensor.battery_state_of_charge` | Battery state of charge (%) |
| `pv_entity` | `sensor.energy_production_today_remaining` | Remaining PV production today (kWh) |
| `capacity_kwh` | `24` | Usable battery capacity |
| `power_kw` | `7` | Charge / discharge power |
| `target_soc` | `100` | Charge up to / discharge down from |
| `min_soc` | `20` | Stop discharging at |
| `pv_factor` | `0.5` | Fraction of the PV forecast credited against grid charging |

## Applying an explicit plan

The `goodwe_tou.set_windows` service writes a list of windows in one verified
request. It is what the planner card uses, and it is handy for automations that
compute their own plan:

```yaml
action: goodwe_tou.set_windows
data:
  windows:
    - start: "02:00"
      stop: "05:00"
      power: -80        # negative = charge
      days: "mon"
    - start: "18:30"
      stop: "20:00"
      power: 50         # positive = discharge
      days: "mon"
```

`power` is a signed percentage (`-100` charge … `+100` discharge). `days` is
optional and defaults to `all`. The list is padded to four windows — any window
you leave out is turned off — and the result is verified by reading it back.

## Example: cheap-rate battery charging

```yaml
automation:
  - alias: Charge battery in the cheap night window
    triggers:
      - trigger: time
        at: "01:00:00"
    actions:
      - action: time.set_value
        target:
          entity_id: time.goodwe_time_of_use_window_4_start
        data:
          time: "04:00:00"
      - action: time.set_value
        target:
          entity_id: time.goodwe_time_of_use_window_4_stop
        data:
          time: "06:00:00"
      - action: number.set_value
        target:
          entity_id: number.goodwe_time_of_use_window_4_power
        data:
          value: -100        # negative = charge
      - action: button.press
        target:
          entity_id: button.goodwe_time_of_use_apply_to_inverter
```

Or let the integration pick the hours for you every day:

```yaml
automation:
  - alias: Plan the cheapest hours every afternoon
    triggers:
      - trigger: time
        at: "13:00:00"
    actions:
      - action: goodwe_tou.set_cheapest_hours
```

## A dashboard

An example dashboard that uses the bundled card is in
[`dashboards/goodwe_tou.yaml`](dashboards/goodwe_tou.yaml).

## Register map

See [`docs/registers.md`](docs/registers.md) for the full register layout and the
day-mask encoding.

## Troubleshooting

- **Cannot connect.** Check the dongle IP, that it is reachable
  (`ping 192.168.20.13`) and that nothing else is holding the dongle. The dongle
  only answers one request at a time and cannot tell which client a reply belongs
  to, so when the official GoodWe integration polls it at the same time you may
  see occasional timeouts. The client retries automatically, ignores replies that
  answer somebody else's request and confirms every write by reading it back.
- **Writes seem to do nothing.** Time-of-Use only runs while the inverter is in
  ECO mode — make sure `switch.goodwe_time_of_use_eco_mode` is on
  (`work_mode = 3`).
- **Enable debug logging:**

  ```yaml
  logger:
    logs:
      custom_components.goodwe_tou: debug
  ```

## Disclaimer

This integration writes to your inverter over an undocumented local protocol.
It has been tested on a single GW10K-ET. Use it at your own risk, and keep the
SEMS portal or the inverter keypad available as a fallback.

## License

MIT — see [LICENSE](LICENSE).

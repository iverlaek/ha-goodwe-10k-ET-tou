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
  that plan the four windows from an electricity price forecast. The battery is
  charged up to the target state of charge in the cheapest hours; the forecast PV
  is a **discount** on the energy the house would otherwise buy (which is why
  charging cheaply now is worth it), not a reduction of the charge. Discharging
  happens in the most expensive hours, capped so the battery is never drained so
  early that the house has to buy expensive power later.
- An **automatic quarter-hour re-check** (`switch.goodwe_time_of_use_auto_herberekening`,
  on by default): every 15 minutes the plan is recomputed and, when it is no
  longer on the minimum-cost line, the new plan is written and the **reason** is
  logged.
- A **bundled card** (`custom:goodwe-tou-card`) — a **read-only** overview: a
  calendar of the current week (ma…zo) with the windows that run on each day, and
  below it a **battery state-of-charge graph** that shows the measured SoC for the
  last `hours_back` hours and continues with the **forecast** SoC (if the plan on
  the inverter is followed) until the end of tomorrow.
- A **planner card** (`custom:goodwe-tou-planner-card`) with two views: a
  *now → midnight tomorrow* timeline with draggable bars at the very top, and a
  *whole-week* calendar list of the seven days. Each window has a single power
  field that sits in a **Laden** column (left) or an **Ontladen** column (right);
  clicking the empty cell moves it to the other column. Inactive windows collapse
  to one thin grey line so the active ones stand together. Tomorrow stays greyed
  out — with a warning triangle — until tomorrow's prices are known, and
  overlapping windows are impossible. *Optimale prijzen berekenen* also shows a
  **"Waarom deze tijden"** section: the reason behind every charge and discharge
  window.
- A `goodwe_tou.set_windows` service that writes an explicit plan (start, stop,
  signed power, days) in a single verified request — used by the planner card.
- A `goodwe_tou.calculate_cheapest_hours` service that returns the cheapest-hours
  plan — with the reasoning behind every window — **without writing** it, so a
  dashboard can show the plan before applying.
- A `goodwe_tou.forecast_soc` service that projects the battery state of charge
  from now until the end of tomorrow, following the windows that are on the
  inverter right now — used by the bundled card's graph.
- An **activity log** of every modification sent to the inverter — successful
  and failed, with the fields that changed, the plan that was written and the
  error message. It survives a restart (persisted to `.storage`, newest 1000
  entries), is exposed as the `sensor.goodwe_time_of_use_activity_log` sensor and
  through the `goodwe_tou.get_activity` service, and is shown by the bundled
  **log card** (`custom:goodwe-tou-log-card`).
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
| `switch.goodwe_time_of_use_auto_herberekening` | Automatic quarter-hour re-check on/off |
| `button.goodwe_time_of_use_read_from_inverter` | Read the schedule from the inverter |
| `button.goodwe_time_of_use_apply_to_inverter` | Apply the schedule to the inverter (verified) |
| `button.goodwe_time_of_use_set_cheapest_hours` | Plan + apply the cheapest hours |
| `sensor.goodwe_time_of_use_activity_log` | Activity log: number of writes, newest entries as attributes |

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

`custom:goodwe-tou-card` is a **read-only** overview of the schedule and the
battery:

- a **calendar of the current week** (ma…zo) listing the windows that run on each
  day, with their time range and charge/discharge percentage; today is highlighted;
- below it a **battery state-of-charge graph**: the *measured* SoC (solid line) for
  the last `hours_back` hours, continuing with the *forecast* SoC (dashed orange)
  until the end of tomorrow. The forecast follows the windows that are on the
  inverter right now, so it shows what you get if you leave the plan as it is.

It has no sliders, buttons or switches — editing happens in the planner card. It
is served at `/goodwe_tou/goodwe-tou-card.js` and loaded automatically, so you only
have to add it to a dashboard:

```yaml
type: custom:goodwe-tou-card
title: GoodWe Time-of-Use                     # optional; " (Version x.y.z)" is appended
prefix: goodwe_time_of_use                    # optional entity-id prefix
soc_entity: sensor.battery_state_of_charge    # optional, for the graph
hours_back: 24                                # optional, measured hours shown
```

The graph needs the recorder (it reads history through the HA API) and the
`goodwe_tou.forecast_soc` service. A ready-to-paste dashboard is in
[`dashboards/goodwe_tou.yaml`](dashboards/goodwe_tou.yaml).

## The planner card

`custom:goodwe-tou-planner-card` is a *planner* for the same four windows.

**One 24-hour timeline (00:00–24:00) with four sliders.** A **week strip**
(ma…zo) at the top picks the day; the number under each day is how many windows
run on it. On the timeline:

- the **grey background is the inverter's general mode** — outside the windows
  the GoodWe is not in a Time-of-Use block. A summary band above the rows shows
  the whole day at a glance (grey = general mode, red = charging, green =
  discharging) together with what the inverter is doing **now**;
- every window is a **compact row** (its number, a **Laden/Ontladen** button, the
  power **%**, the start and stop time and the days) with its own **bar** on the
  shared axis. Drag the **left/right handle** to move the start/stop; bars snap
  to quarter hours and **cannot overlap** — a handle stops at the neighbouring
  window. The **%** and the times can also be typed directly;
- an **unconfigured window is grey** and stays inactive until you press
  *Activeren*;
- a window that has **already passed today** is greyed, tagged **afgelopen** and
  moved to the bottom of the list. A window that does not run on the selected day
  is dimmed and tagged *niet op <dag>*.

Only the **order on screen** changes; the windows keep the number they have in
the inverter, and *Toepassen* writes every window back to its own number.

**Buttons at the bottom:**

- **Lezen van omvormer** pulls the live schedule from the inverter;
- **Optimale prijzen berekenen** calls `goodwe_tou.calculate_cheapest_hours`,
  fills the rows with the plan and does **not** write anything — review it, then
  press *Toepassen*. Below the buttons it shows **"Waarom deze tijden"**: the
  reason behind every window (which slots were the cheapest or most expensive,
  the average price, the energy involved) and the plan's notes;
- **Toepassen** writes the whole plan at once through `goodwe_tou.set_windows`.

```yaml
type: custom:goodwe-tou-planner-card
title: Laadplanning                                  # optional; " (Version x.y.z)" is appended
prefix: goodwe_time_of_use                           # optional
price_entity: sensor.average_electricity_price       # optional, for the price bars
slot_minutes: 15                                     # optional, timeline granularity
```

## The log card

`custom:goodwe-tou-log-card` is a **read-only** activity log: every modification
the integration sent to the inverter, newest first. Each entry shows when it
happened, what action it was, the fields that changed (old → new), the plan that
was written and whether the inverter **confirmed** it — or, in red, why it did
not (for example a read-back mismatch or a timeout). An automatic re-check
(`auto_replan`) also shows **why** the plan was changed.

A changed window is shown on **one line** — its number, the action (laden /
ontladen / uit) and the time range before and after, e.g.
`1: laden 03:45–04:30 → 01:30–05:45`. The windows that were written are listed
below it as `1: laden 01:30–05:45 · wo`.

Filters at the top narrow the list by **result** (Alles / Gelukt / Mislukt) and
by **window** (Alle / 1–4). The card fetches the full log through the
`goodwe_tou.get_activity` service, so it can show more entries than fit in the
sensor's attributes, and it refreshes whenever the activity sensor changes:

```yaml
type: custom:goodwe-tou-log-card
title: Logboek                                   # optional; " (Version x.y.z)" is appended
prefix: goodwe_time_of_use                        # optional entity-id prefix
sensor: sensor.goodwe_time_of_use_activity_log    # optional, refresh trigger
limit: 200                                        # optional, entries fetched
```

## Cheapest hours

The **Set cheapest hours** button (and the `goodwe_tou.set_cheapest_hours`
service) reads the quarter-hourly price forecast from a price entity, works out
how much energy the battery still needs (from the state of charge, the battery
capacity and the target state of charge) and fills the four windows with the
cheapest charging hours and the most expensive discharging hours.

The house's standby consumption and the GoodWe's power limits are taken into
account: the battery charges at up to `charge_kw` (6 kW, AC + DC together) and
discharges at up to `discharge_kw` (6 kW over AC). Discharging is capped at
`export_hours` at full power, so a bad solar period cannot drain the battery so
early that the house has to buy expensive power at the peak.

Charging is only planned when it actually pays off. Every kWh taken from the grid
is grossed up for the round-trip losses of the battery (`rte`, 85% by default),
so it is roughly 15% more expensive once it is stored. The plan charges in the
cheapest quarter hours only when that price still beats the most expensive hours
the house would otherwise buy later. When the spread does not cover the losses,
the battery is left alone and a note explains why.

The defaults can be changed in the integration **Configure** dialog or passed to
the service:

```yaml
action: goodwe_tou.set_cheapest_hours
data:
  price_entity: sensor.average_electricity_price
  soc_entity: sensor.battery_state_of_charge
  pv_entity: sensor.energy_production_today_remaining
  capacity_kwh: 24
  charge_kw: 6
  discharge_kw: 6
  standby_kw: 1.2
  export_hours: 1
  target_soc: 100
  min_soc: 20
  pv_factor: 0.5
  rte: 0.85
```

| Option | Default | Meaning |
| --- | --- | --- |
| `price_entity` | `sensor.average_electricity_price` | Entity exposing the quarter-hourly forecast — ENTSO-e (`prices` with `{time, price}`) or Nordpool (`raw_today`/`raw_tomorrow` with `{start, value}`) |
| `soc_entity` | `sensor.battery_state_of_charge` | Battery state of charge (%) |
| `pv_entity` | `sensor.energy_production_today_remaining` | Remaining PV production today (kWh) |
| `capacity_kwh` | `24` | Usable battery capacity |
| `charge_kw` | `6` | Maximum charge power (GoodWe limit, AC + DC) |
| `discharge_kw` | `6` | Maximum discharge power (GoodWe limit over AC) |
| `standby_kw` | `1.2` | House standby consumption the battery covers in ECO mode |
| `export_hours` | `1` | How long the battery may export at full power before it is kept for the house; `0` disables the cap |
| `target_soc` | `100` | Charge up to / discharge down from |
| `min_soc` | `20` | Stop discharging at |
| `pv_factor` | `0.5` | Fraction of the PV forecast counted as a discount on the energy the house would buy |
| `rte` | `0.85` | Round-trip efficiency of the battery; charging only pays off when the cheap price / `rte` beats the price bought later |
| `power_kw` | `7` | Legacy combined charge/discharge power, used when `charge_kw`/`discharge_kw` are not set |

### Automatic quarter-hour re-check

Every quarter hour the integration recomputes the plan and compares it with the
windows that are on the inverter. When they differ — because the prices, the
state of charge or the PV forecast changed — the new plan is written and the
**reason** is added to the activity log (action `auto_replan`). Turn the
`switch.goodwe_time_of_use_auto_herberekening` switch off to keep the plan that
is on the inverter as it is.

### Calculating without applying

`goodwe_tou.calculate_cheapest_hours` takes the same options but **returns** the
plan instead of writing it. It is what the planner card's *Optimale prijzen
berekenen* button calls:

```yaml
action: goodwe_tou.calculate_cheapest_hours
data:
  price_entity: sensor.average_electricity_price
response_variable: plan
```

The response is the plan: a `windows` list, a `reasons` list (one entry per
window with `window`, `mode` and a human readable `text`), a one-line `summary`
and general `notes` (for example about the PV discount and the export cap):

```yaml
windows:
  - start: "02:00"
    stop: "05:00"
    power: -100
    days: "mon,tue,wed,thu,fri,sat,sun"
    mode: charge
    enabled: true
reasons:
  - window: 1
    mode: charge
    text: "Goedkoopste 180 min van de komende periode (gemiddeld €0.081/kWh)…"
summary: "Batterij 40% → doel 100% = 14.4 kWh. …"
notes:
  - "PV-prognose levert nog 5.0 kWh voor niets. …"
```

## Forecasting the state of charge

`goodwe_tou.forecast_soc` projects the battery state of charge from now until the
end of tomorrow, following the Time-of-Use windows that are currently on the
inverter. The house's standby consumption is drawn from the battery too, so the
forecast shows the real drain. It is what the bundled card draws after the
measured part of its graph:

```yaml
action: goodwe_tou.forecast_soc
data:
  soc_entity: sensor.battery_state_of_charge   # optional
  capacity_kwh: 24                             # optional
  charge_kw: 6                                 # optional; max charge power
  discharge_kw: 6                              # optional; max discharge power
  standby_kw: 1.2                              # optional; house standby load
  min_soc: 20                                  # optional; discharge floor
  rte: 0.85                                    # optional; round-trip efficiency
response_variable: forecast
```

The response is a list of `{time, soc}` points, starting at the battery's current
state of charge:

```yaml
points:
  - time: "2026-11-17T13:30:00+01:00"
    soc: 77.0
  - time: "2026-11-17T13:45:00+01:00"
    soc: 77.0
  # …
```

## Reading the activity log

Every write the integration sends to the inverter is recorded (successful and
failed) and kept newest-first, capped at 1000 entries and persisted across
restarts. The `sensor.goodwe_time_of_use_activity_log` sensor exposes the count
as its state and the newest 50 entries as its `entries` attribute, together with
`total`, `last_time`, `last_action` and `last_result`.

`goodwe_tou.get_activity` returns the full log, optionally filtered:

```yaml
action: goodwe_tou.get_activity
data:
  limit: 100        # optional, newest N entries (1..1000)
  window: 2         # optional, only entries about this window
  result: error     # optional, "ok" or "error"
response_variable: log
```

Each entry has `time`, `action`, `result` (`ok` / `error`), `error` (or `null`),
`changes` (a list of `{field, old, new}`) and `windows` (the plan that was
written, `{n, start, stop, power, days}`). This is what the log card uses.

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

# GoodWe Time-of-Use (Eco Mode) for Home Assistant

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=iverlaek&amp;repository=ha-goodwe-10k-ET-tou&amp;category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open your Home Assistant instance and open a repository inside the Home Assistant Community Store."></a>
  <a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=goodwe_tou"><img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Open your Home Assistant instance and start setting up a new integration."></a>
</p>

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

<p align="center">
  <a href="https://www.goodwe.com/">
    <img src="https://hrmobiles.com/wp-content/uploads/2026/09/GoodWe-GW10K-ET-PLUS.webp" alt="GoodWe GW10K-ET PLUS hybrid inverter" width="420">
  </a>
</p>

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
- A **power card** (`custom:goodwe-tou-power-card`) — a live *energy display*
  like the HomeWizard: one big circle whose ring is the **battery SOC** with the
  **current grid power** in the middle (**purple** while drawing from the grid,
  **green** while feeding back), and two smaller circles for the lowest/highest
  **cell temperature** and **cell voltage**.
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
- **Deviation tracking with an adaptive house load.** The planner's assumed
  house load is compared with what the house really used, hour by hour, and
  adjusted for the weather: a cold day (the air conditioner working harder) or a
  bad solar forecast makes the house draw more than planned, which used to drain
  the battery early. The integration keeps a compact log of the deviation and the
  variables behind it — outdoor temperature, **feels-like** (apparent)
  temperature, **heating degree hours** and the PV forecast error — fits a
  house-load model (`base + temperature term`) from it, and uses that model in the
  plan once enough hours are in. Only the derived samples are stored (the raw
  sensor history already lives in Home Assistant's recorder database), so they can
  be kept for 90 days. See the `sensor.goodwe_time_of_use_afwijking` sensor, the
  `goodwe_tou.get_deviations` service and the bundled **deviation card**
  (`custom:goodwe-tou-deviation-card`).
- An **ECO mode** switch (`work_mode = 3`); Time-of-Use only runs in ECO mode.
- Local polling with configurable interval, retries and a config flow — no YAML.
- **Dutch or English**: chosen at installation and changeable in the options. It
  translates the planner's reasoning, the activity log, the entity names and the
  bundled cards, while the entity *ids* stay fixed.
- Robust against a dongle that is **shared** with another integration: reads
  ignore foreign frames (two matching reads plus a structural check) and every
  write is confirmed by reading it back, so a change cannot silently revert.

## Installation

### Via the Home Assistant Marketplace (HACS)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=iverlaek&repository=ha-goodwe-10k-ET-tou&category=integration)

1. Open **Settings → Devices & services → Marketplace**.
2. Choose **Add repository** / **Add from link** and paste:
   `https://github.com/iverlaek/ha-goodwe-10k-ET-tou`
3. Install **GoodWe Time-of-Use (Eco Mode)** and restart Home Assistant.

Or click the blue **Open in Home Assistant** button above: it opens HACS on your
own instance with this repository already filled in.

### Manually

Copy the `custom_components/goodwe_tou` folder into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

## Configuration

**Settings → Devices & services → Add integration → GoodWe Time-of-Use**.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=goodwe_tou)

The blue **Add integration** button above jumps straight to the setup dialog on
your own instance.

| Field | Default | Description |
| --- | --- | --- |
| Host | `192.168.20.13` | IP address of the GoodWe Wi-Fi/LAN dongle |
| UDP port | `8899` | GoodWe local protocol port |
| Modbus address | `247` | Communication address of the inverter |
| Language | the HA language, else `Nederlands` | The language the integration speaks (Dutch or English) |

The flow probes the inverter before creating the entry, so a wrong host or a
dongle that is offline is reported immediately.

The poll interval (seconds, `0` to disable polling) can be changed in the
integration **Configure** dialog. The same dialog holds the planner's boundary
conditions (see [Cheapest hours](#cheapest-hours)), the language, the window the
[manual buttons](#manual-charge--discharge) drive (`manual_window`, default `4`)
and the Tibber price sensor (`tibber_prices`, on by default; `tibber_home` when
your account has several homes). It also holds the [deviation
tracking](#deviation-tracking) settings: `adaptive_load`, `weather_entity`,
`load_entity`, `pv_power_entity`, `temp_coeff`, `degree_base_c` and
`deviation_size`.

### Language

Pick **Nederlands** or **English** at installation; the form pre-selects the Home
Assistant language when the integration speaks it. The choice drives the
planner's reasons and notes, the activity log, the entity names and the bundled
cards. You can change it later in the **Configure** dialog.

Entity *ids* never change with the language (they are pinned to
`goodwe_time_of_use_*`), so a dashboard or automation keeps working after a
switch. The bundled cards follow the same language automatically — they read it
from the activity sensor — and each card also accepts a `language:` override:

```yaml
type: custom:goodwe-tou-planner-card
language: en
```

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
| `button.goodwe_time_of_use_auto_eco_mode` | Resume the automation (ECO + re-check on) |
| `button.goodwe_time_of_use_manual_override` | Pause the automation, keep the windows |
| `button.goodwe_time_of_use_manual_charge` | Make the manual window charge now |
| `button.goodwe_time_of_use_manual_discharge` | Make the manual window discharge now |
| `sensor.goodwe_time_of_use_activity_log` | Activity log: number of writes, newest entries as attributes |
| `sensor.goodwe_time_of_use_plan` | Last computed plan: number of windows, with `reasons` (per window) and `notes` as attributes |
| `sensor.goodwe_time_of_use_afwijking` | Deviation log: number of hourly samples, with the learned model, the current assumed load, today's totals and the newest samples as attributes |
| `sensor.goodwe_time_of_use_stroomprijzen` | Tibber day-ahead prices (state = current price; `prices` attribute = the full list) |

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

### Manual charge / discharge

`button.goodwe_time_of_use_manual_charge` and
`button.goodwe_time_of_use_manual_discharge` force the battery to charge or
discharge **now**. They only flip the **direction and power** of one window —
window 4 by default, changeable with the `manual_window` addon option — and leave
that window's start, stop and days exactly as you set them. They are **mutually
exclusive** (one window has one direction) and pressing the direction that is
already running switches the window off again. Pressing either button also pauses
the automatic re-check, so the next quarter-hour cannot undo your choice; pick
**Auto/ECO mode** to hand control back to the planner.

On the dashboard they only appear while the automation is paused. See the
[manual buttons](#manual-charge--discharge-buttons) example below.

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

## The power card

`custom:goodwe-tou-power-card` is a **read-only**, live widget styled after the
HomeWizard Energy Display:

- one **big circle** whose ring is the **battery state of charge** (0–100%), with
  the **current grid power** in the middle. The circle is **purple** while the
  house draws from the grid (afname) and **green** while it feeds back
  (teruglevering); below 25 W it is grey ("in balans");
- two **smaller circles** next to it — the lowest/highest **cell temperature**
  (`12–27 °C`) and the lowest/highest **cell voltage** (`3.18–3.31 V`).

The big circle is 50% larger than the two smaller ones. It is served at
`/goodwe_tou/goodwe-tou-power-card.js` and loaded automatically:

```yaml
type: custom:goodwe-tou-power-card
title: GoodWe vermogen                        # optional; " (Version x.y.z)" is appended
grid_entity: sensor.meter_active_power_total  # optional; defaults to the GoodWe meter
invert_grid: false                            # see "Grid sign" below
soc_entity: sensor.battery_state_of_charge
min_temp_entity: sensor.battery_min_cell_temperature
max_temp_entity: sensor.battery_max_cell_temperature
min_volt_entity: sensor.battery_min_cell_voltage
max_volt_entity: sensor.battery_max_cell_voltage
language: nl                                  # optional, overrides the language
```

**Grid sign.** The GoodWe meter (`sensor.meter_active_power_total`) reports a
*negative* value while the house draws from the grid, so the card's default
(`invert_grid: false`) is correct for it. For a meter that reports consumption as
*positive*, such as a P1/DSMR meter (`sensor.p1_meter_power`), set
`invert_grid: true`.

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
  is dimmed and tagged *niet op <dag>*;
- the **price graph** above the band shows the quarter-hour prices for the
  selected day as bars, with the price on the **left axis in whole cents**
  (5¢, 10¢, 15¢ … and −5¢ when prices go negative). The 0¢ line is drawn a bit
  darker so you can see at a glance when the price is negative.

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

## The price card

<p align="center">
  <a href="https://tibber.com/">
    <img src="https://app.tibber.com/images/logos/tibber-logo.svg" alt="Tibber" width="150">
  </a>
</p>

`custom:goodwe-tou-planner-card` draws its own compact price strip, but for a
clearer two-day overview the integration also exposes the **Tibber** day-ahead
prices as a sensor so you can drop the familiar *Stroomprijs vandaag & morgen*
card next to the planner:

- `sensor.goodwe_time_of_use_stroomprijzen` — state is the price of the quarter
  hour that is running now (`EUR/kWh`); the `prices` attribute holds **every**
  slot for today and tomorrow as `{time, price}` records, the same shape the
  ENTSO-e/Nordpool sensors use;
- the values come from Tibber's `tibber.get_prices` service (all-in €/kWh), which
  the integration refreshes every 15 minutes on its own. No YAML, template or
  token is needed. When Tibber is not installed the sensor simply stays empty.

A ready-made card for the HACS [`apexcharts-card`](https://github.com/RomRider/apexcharts-card)
frontend (the same style as the start dashboard) is included in the example
dashboard:

```yaml
type: custom:apexcharts-card
graph_span: 2d
span:
  start: day
now:
  show: true
  label: nu
header:
  show: true
  show_states: true
  colorize_states: true
  title: Stroomprijs vandaag & morgen (€/kWh)
update_interval: 5m
yaxis:
  - id: Prijs
    decimals: 2
series:
  - entity: sensor.goodwe_time_of_use_stroomprijzen
    name: Tibber
    type: column
    float_precision: 3
    data_generator: |
      return entity.attributes.prices.map((record) => {
        return [new Date(record.time).getTime(), record.price];
      });
```

The planner and the price card are independent. By default the planner reads its
forecast from `price_entity` (ENTSO-e/Nordpool). Set the **Price source** option
to **Tibber** to let the planner use the same Tibber prices the sensor exposes —
no `price_entity` needed then. You can also point the card's `price_entity` at
the Tibber sensor, because `prices` uses the same `{time, price}` format.

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

## The deviation card

`custom:goodwe-tou-deviation-card` shows how far reality drifted from the plan,
and what the integration learned from it. Two panels sit at the top: the **learned
house load** (base, temperature coefficient, number of samples and how much of the
spread it explains) with the load the planner assumes **now**, and **today's
totals** (actual, planned, the deviation, heating degree days, the average
feels-like temperature and the PV forecast error). Below them is a table of the
hourly samples: actual and planned energy, the deviation, the outdoor and
feels-like temperature, the heating degree hours and the PV forecast error. A
filter narrows the table to **Vandaag** or **Alles**.

Red means the house used **more** than planned (a cost); green means **less** (a
saving). The card fetches the full log through the `goodwe_tou.get_deviations`
service and refreshes whenever the deviation sensor changes:

```yaml
type: custom:goodwe-tou-deviation-card
title: Afwijking                                  # optional; " (Version x.y.z)" is appended
prefix: goodwe_time_of_use                        # optional entity-id prefix
sensor: sensor.goodwe_time_of_use_afwijking       # optional, refresh trigger
limit: 200                                        # optional, samples fetched
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
`export_hours` at full power **per day**, so the rest of today gets its own
discharge window instead of the whole budget being spent on tomorrow's peak, and
a bad solar period cannot drain the battery so early that the house has to buy
expensive power at the peak.

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
  price_source: entity                                 # entity (ENTSO-e/Nordpool) or tibber
  price_entity: sensor.average_electricity_price
  soc_entity: sensor.battery_state_of_charge
  pv_entity: sensor.energy_production_today_remaining
  capacity_kwh: 24
  charge_kw: 6
  discharge_kw: 6
  standby_kw: 1.2
  adaptive_load: true
  weather_entity: weather.forecast_home
  load_entity: sensor.house_consumption
  pv_power_entity: sensor.pv_power
  temp_coeff: 0.05
  degree_base_c: 15.5
  export_hours: 1
  target_soc: 100
  min_soc: 20
  pv_factor: 0.5
  rte: 0.85
```

| Option | Default | Meaning |
| --- | --- | --- |
| `price_source` | `entity` | Where the planner reads its forecast: `entity` uses `price_entity` below (ENTSO-e/Nordpool style), `tibber` uses the day-ahead prices the integration fetches from Tibber itself |
| `price_entity` | `sensor.average_electricity_price` | Entity exposing the quarter-hourly forecast — ENTSO-e (`prices` with `{time, price}`) or Nordpool (`raw_today`/`raw_tomorrow` with `{start, value}`) |
| `soc_entity` | `sensor.battery_state_of_charge` | Battery state of charge (%) |
| `pv_entity` | `sensor.energy_production_today_remaining` | Remaining PV production today (kWh) |
| `capacity_kwh` | `24` | Usable battery capacity |
| `charge_kw` | `6` | Maximum charge power (GoodWe limit, AC + DC) |
| `discharge_kw` | `6` | Maximum discharge power (GoodWe limit over AC) |
| `standby_kw` | `1.2` | House standby consumption the battery covers in ECO mode; with `adaptive_load` on it is the *base* the learned model starts from |
| `adaptive_load` | `true` | Adjust the assumed house load for the weather and learn it from the deviation log (see [Deviation tracking](#deviation-tracking)) |
| `weather_entity` | `weather.forecast_home` | Weather entity for the outdoor temperature, humidity and wind; the feels-like temperature is computed from them |
| `load_entity` | `sensor.house_consumption` | The sensor with the house's real consumption, compared with the plan |
| `pv_power_entity` | `sensor.pv_power` | PV power sensor, for the forecast-error part of the deviation log |
| `temp_coeff` | `0.05` | Extra house load (kW) per degree the feels-like temperature is below `degree_base_c`, until the log refines it |
| `degree_base_c` | `15.5` | Heating degree days are counted below this feels-like temperature |
| `deviation_size` | `2160` | How many hourly deviation samples to keep (newest first; max `10000`) |
| `export_hours` | `1` | How long the battery may export at full power **per day** before it is kept for the house; `0` disables the cap |
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

## Deviation tracking

The planner assumes a constant house load (`standby_kw`). In reality that load
moves with the weather: a cold day makes the air conditioner work harder, a wrong
weather forecast means the real load drifts away from the plan, and the battery
then runs flat earlier than planned. With `adaptive_load` on (the default) the
integration watches the difference and corrects for it.

Every hour it records one compact sample of the **deviation** between the plan and
reality, together with the variables that influence it:

| Field | Meaning |
| --- | --- |
| `time`, `hours` | The hour the sample covers and how much of it was measured |
| `actual_kwh`, `planned_kwh`, `deviation_kwh` | What the house used, what the planner assumed, and the difference |
| `outdoor_temp`, `feels_like` | The outdoor and the computed **feels-like** (apparent) temperature |
| `degree_hours` | Heating degree hours: how far the feels-like temperature sat below `degree_base_c`, times the hours (`÷ 24` = degree days) |
| `pv_kwh`, `pv_forecast_kwh`, `pv_error_kwh` | The PV produced, the forecast for that hour and the difference (a large negative value means the weather came out worse than predicted) |

Only these **derived** samples are stored (in `.storage`, newest `deviation_size`
= 2160 hours ≈ 90 days); the raw temperature, humidity, wind and load history
already lives in Home Assistant's recorder database, so it is not duplicated. That
is why the derived samples can be kept much longer than the raw sensor history.

From the log the integration fits a small model of the house load:

```
house load (kW) = base + temp_coeff × (degrees the feels-like temp is below degree_base_c)
```

The slope is never negative (cold can only ever *add* load), the coefficients stay
inside safe bounds, and the fitted line is blended with the configured defaults
until enough hours have been logged — so a handful of odd hours never move the
plan on their own. A cold hour therefore makes the planner charge a little more,
keeping the battery off its floor. Turn `adaptive_load` off to keep the plan on the
configured `standby_kw` and just log the deviation.

`goodwe_tou.get_deviations` returns the full log (newest first), the learned model
and today's totals:

```yaml
action: goodwe_tou.get_deviations
data:
  limit: 100        # optional, newest N hourly samples
response_variable: deviation
```

The `sensor.goodwe_time_of_use_afwijking` sensor exposes the sample count as its
state and the learned model (`learned`), the load assumed now (`effective_kw`),
today's totals (`today`) and the newest samples (`entries`) as attributes.

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

A ready-to-paste dashboard is in
[`dashboards/goodwe_tou.yaml`](dashboards/goodwe_tou.yaml). It shows the live
power card, the read-only overview, the planner, the battery SOC history graph,
the solar power-flow card, the solar forecast clock, the Tibber price card, the
manual charge/discharge buttons, the activity log and the deviation log. Create a
dashboard (**Settings → Dashboards → Add dashboard → New dashboard from scratch**),
open the **raw configuration editor** and paste this in:

```yaml
title: GoodWe TOU
views:
  - title: Time of Use
    path: tou
    icon: mdi:battery-clock-outline
    cards:
      - type: custom:goodwe-tou-card
        title: GoodWe Time-of-Use
        soc_entity: sensor.battery_state_of_charge
        hours_back: 24

      # Battery state of charge over the last two days (built-in card).
      - type: history-graph
        title: Batterij SOC
        hours_to_show: 48
        entities:
          - entity: sensor.battery_state_of_charge

      - type: custom:goodwe-tou-planner-card
        title: Laadplanning
        price_entity: sensor.average_electricity_price

      # Live "energy display" - on a wide screen this lands top-right as the
      # third card. See "The power card" above.
      - type: custom:goodwe-tou-power-card
        title: GoodWe vermogen
        grid_entity: sensor.meter_active_power_total
        invert_grid: false
        soc_entity: sensor.battery_state_of_charge
        min_temp_entity: sensor.battery_min_cell_temperature
        max_temp_entity: sensor.battery_max_cell_temperature
        min_volt_entity: sensor.battery_min_cell_voltage
        max_volt_entity: sensor.battery_max_cell_voltage

      # PV input, the same "Solar battery input from panels" gauge as on the
      # start screen (HACS "modern-circular-gauge").
      - type: custom:modern-circular-gauge
        entity: sensor.pv_power
        name: Solar battery input from panels
        gauge_type: half
        min: 0
        max: 7500
        needle: false
        header_position: top
        show_state: true
        show_unit: true
        secondary:
          entity: sensor.pv1_power
          min: 0
          max: 2500
          show_gauge: inner
        tertiary:
          entity: sensor.pv2_power
          min: 0
          max: 2500
          show_gauge: inner

      # Live solar power flow with the production forecast overlaid (HACS
      # "solar-bar-card"). The forecast is the dotted line.
      - type: custom:solar-bar-card
        inverter_size: 10
        production_entity: sensor.pv_power
        self_consumption_entity: sensor.house_consumption
        grid_power_entity: sensor.meter_active_power_total
        invert_grid_power: false
        forecast_entity: sensor.power_production_now
        battery_soc_entity: sensor.battery_state_of_charge
        battery_power_entity: sensor.battery_power
        invert_battery_power: true
        battery_capacity: 24
        import_history_entity: sensor.today_energy_import
        export_history_entity: sensor.today_energy_export
        production_history_entity: sensor.energy_production_today
        show_header: true
        header_title: Solar
        show_stats: true
        show_legend: true
        color_palette: classic-solar

      # Solar production forecast as a clock (HACS "pv-forecast-card").
      - type: custom:clock-pv-forecast-card
        bar_style: gradient
        bar_type: segmented
        color_thresholds_manual: ""
        date_format: short
        display_mode: relative
        entity_remaining: sensor.energy_production_today_remaining
        entity_today: sensor.energy_production_today
        entity_tomorrow: sensor.energy_production_tomorrow
        gradient_fixed: false
        max_value: 75
        relative_plus_one: false
        remaining_blink: false
        remaining_indicator: marker
        remaining_inverted: false
        remaining_threshold: 41
        scale_mode: fixed
        show_attribute_value: false
        show_tooltips: true

      # Manual charge / discharge. Only visible while the automation is paused
      # (Auto re-check off). They flip the direction of one window - window 4 by
      # default, see the "manual_window" addon option - and leave its start, stop
      # and days alone. Pressing the active direction again switches it off.
      - type: conditional
        conditions:
          - entity: switch.goodwe_time_of_use_auto_herberekening
            state_not: "on"
        card:
          type: horizontal-stack
          cards:
            - type: custom:button-card
              entity: button.goodwe_time_of_use_manual_charge
              name: Handmatig laden
              icon: mdi:battery-arrow-up
              show_state: false
              tap_action:
                action: perform-action
                perform_action: button.press
                target:
                  entity_id: button.goodwe_time_of_use_manual_charge
              styles:
                icon:
                  - color: |
                      [[[
                        var p = hass.states['number.goodwe_time_of_use_window_4_power'];
                        return p && Number(p.state) < 0 ? '#4caf50' : 'var(--disabled-text-color)';
                      ]]]
            - type: custom:button-card
              entity: button.goodwe_time_of_use_manual_discharge
              name: Handmatig ontladen
              icon: mdi:battery-arrow-down
              show_state: false
              tap_action:
                action: perform-action
                perform_action: button.press
                target:
                  entity_id: button.goodwe_time_of_use_manual_discharge
              styles:
                icon:
                  - color: |
                      [[[
                        var p = hass.states['number.goodwe_time_of_use_window_4_power'];
                        return p && Number(p.state) > 0 ? '#ff9800' : 'var(--disabled-text-color)';
                      ]]]

      # Day-ahead price card, fed by the integration's Tibber price sensor.
      - type: custom:apexcharts-card
        graph_span: 2d
        span:
          start: day
        now:
          show: true
          label: nu
        header:
          show: true
          show_states: true
          colorize_states: true
          title: Stroomprijs vandaag & morgen (€/kWh)
        update_interval: 5m
        yaxis:
          - id: Prijs
            decimals: 2
        series:
          - entity: sensor.goodwe_time_of_use_stroomprijzen
            name: Tibber
            type: column
            float_precision: 3
            opacity: 1
            stroke_width: 2
            color_threshold:
              - value: -0.1
                color: "#000fff"
              - value: 0
                color: "#00ffd5"
              - value: 0.1
                color: "#ff8000"
              - value: 0.2
                color: "#0080ff"
              - value: 0.3
                color: "#ff2a00"
              - value: 1
                color: "#ff00d4"
            data_generator: |
              return entity.attributes.prices.map((record) => {
                return [new Date(record.time).getTime(), record.price];
              });

      - type: custom:goodwe-tou-log-card
        title: Logboek

      # How far reality drifted from the plan, with the weather variables behind
      # it and the house-load model learned from it.
      - type: custom:goodwe-tou-deviation-card
        title: Afwijking

      - type: entities
        title: Status
        show_header_toggle: false
        entities:
          - entity: button.goodwe_time_of_use_auto_eco_mode
            name: Auto/ECO Mode (automatisch)
          - entity: button.goodwe_time_of_use_manual_override
            name: Manual override (pauzeer automatisering)
          - entity: switch.goodwe_time_of_use_eco_mode
            name: ECO mode (Time-of-Use active)
          - entity: sensor.goodwe_time_of_use_laagste_celspanning
          - entity: sensor.battery_state_of_charge
          - entity: sensor.battery_power
          - entity: sensor.average_electricity_price
          - entity: sensor.energy_production_today_remaining
```

The bundled `custom:goodwe-tou-*` cards are served by the integration itself, so
they need no extra resources. The example also uses the HACS frontend cards
[`button-card`](https://github.com/custom-cards/button-card),
[`apexcharts-card`](https://github.com/RomRider/apexcharts-card),
[`modern-circular-gauge`](https://github.com/selvalt7/modern-circular-gauge),
[`solar-bar-card`](https://github.com/0xAHA/solar-bar-card) and
[`pv-forecast-card`](https://github.com/dropqube/pv-forecast-card).

### Manual charge / discharge buttons

The `conditional` card above shows two buttons **only while the automation is
paused** (the `switch.goodwe_time_of_use_auto_herberekening` is off). They flip
the direction of the window named by the `manual_window` addon option (window 4
by default) and leave its start, stop and days alone; the icon lights up green
while charging and orange while discharging. Pressing the active direction again
switches the window off. Change the two `number..._window_4_power` references if
you picked a different `manual_window`.

If you do not want the HACS `button-card`, a plain `entities` card works too:

```yaml
type: conditional
conditions:
  - entity: switch.goodwe_time_of_use_auto_herberekening
    state_not: "on"
card:
  type: entities
  title: Handmatig
  entities:
    - button.goodwe_time_of_use_manual_charge
    - button.goodwe_time_of_use_manual_discharge
```

### A compact status card

To show the state of the automation next to your battery card, drop this
`vertical-stack` anywhere on a dashboard. It needs the HACS
[`button-card`](https://github.com/custom-cards/button-card) and (optionally)
[`card-mod`](https://github.com/thomasloven/lovelace-card-mod) frontend cards.
The first card shows **Auto/ECO vs Handmatig** (and whether ECO mode is on); the
second derives the **next planned window** and **tomorrow's programming** live
from the four window entities, and adds the planner's **reason per window** from
the `sensor.goodwe_time_of_use_plan` sensor, so no extra helper is needed.

```yaml
type: vertical-stack
cards:
  - type: custom:button-card
    entity: switch.goodwe_time_of_use_auto_herberekening
    name: GoodWe Time-of-Use
    show_name: true
    show_state: true
    show_icon: true
    icon: mdi:battery-clock-outline
    state_display: |
      [[[
        var auto = (entity && entity.state) || 'off';
        var eco = (hass.states['switch.goodwe_time_of_use_eco_mode'] || {}).state;
        var label = auto === 'on' ? 'Auto/ECO' : 'Handmatig';
        var sub = auto === 'on' ? (eco === 'on' ? 'ECO-modus aan' : 'ECO-modus uit')
                                : 'automatische herberekening gepauzeerd';
        return `<div style="text-align:center;line-height:1.35">
          <span style="font-size:34px;font-weight:900;color:#2196f3">${label}</span>
          <div style="font-size:14px;opacity:.85;margin-top:8px">${sub}</div>
        </div>`;
      ]]]
    styles:
      icon:
        - color: |
            [[[ return (entity && entity.state) === 'on' ? '#4caf50' : '#ff9800'; ]]]
    triggers_update:
      - switch.goodwe_time_of_use_auto_herberekening
      - switch.goodwe_time_of_use_eco_mode

  - type: markdown
    content: |
      {% set dns = ['mon','tue','wed','thu','fri','sat','sun'] %}
      {% set nls = {'mon':'ma','tue':'di','wed':'wo','thu':'do','fri':'vr','sat':'za','sun':'zo'} %}
      {% set now_ = now() %}
      {% set cur = now_.hour * 60 + now_.minute %}
      {% set today = dns[now_.weekday()] %}
      {% set tmr = dns[(now_.weekday() + 1) % 7] %}
      {% set reasons = state_attr('sensor.goodwe_time_of_use_plan', 'reasons') or [] %}
      {% set ns = namespace(act=None, td=None, tdk=None, tmr=None, tmrk=None, mlist=[]) %}
      {% for i in range(1, 5) %}
        {% set start = states('time.goodwe_time_of_use_window_' ~ i ~ '_start') %}
        {% set stop = states('time.goodwe_time_of_use_window_' ~ i ~ '_stop') %}
        {% set power = states('number.goodwe_time_of_use_window_' ~ i ~ '_power') | float(0) %}
        {% set days = states('text.goodwe_time_of_use_window_' ~ i ~ '_days') %}
        {% set sp = start.split(':') %}
        {% set ep = stop.split(':') %}
        {% if power != 0 and sp | length >= 2 and ep | length >= 2 %}
          {% set smin = sp[0] | int(0) * 60 + sp[1] | int(0) %}
          {% set emin = ep[0] | int(0) * 60 + ep[1] | int(0) %}
          {% set on_today = days == 'all' or today in days.split(',') %}
          {% set on_tmr = days == 'all' or tmr in days.split(',') %}
          {% set act = 'laden' if power < 0 else 'ontladen' %}
          {% set why = (reasons | selectattr('window', 'eq', i) | map(attribute='text') | list | first) | default('', true) %}
          {% set win = {'act': act, 'start': start[:5], 'stop': stop[:5], 'why': why} %}
          {% if on_today and smin <= cur and cur < emin %}{% set ns.act = win %}{% endif %}
          {% if on_today and smin > cur and (ns.tdk is none or smin < ns.tdk) %}
            {% set ns.td = win %}{% set ns.tdk = smin %}
          {% endif %}
          {% if on_tmr %}
            {% set ns.mlist = ns.mlist + [win] %}
            {% if ns.tmrk is none or smin < ns.tmrk %}{% set ns.tmr = win %}{% set ns.tmrk = smin %}{% endif %}
          {% endif %}
        {% endif %}
      {% endfor %}
      {% if ns.act %}🔋 **Nu: {{ ns.act.act }}** {{ ns.act.start }}–{{ ns.act.stop }}
      {% elif ns.td %}⏱️ **Volgende: {{ ns.td.act }}** {{ ns.td.start }}–{{ ns.td.stop }} (vandaag)
      {% elif ns.tmr %}⏱️ **Volgende: {{ ns.tmr.act }}** {{ ns.tmr.start }}–{{ ns.tmr.stop }} (morgen)
      {% else %}⏱️ Geen gepland venster
      {% endif %}
      {% if ns.mlist %}📅 **Morgen ({{ nls[tmr] }})**{% for w in ns.mlist | sort(attribute='start') %}<br>{{ w.act }} {{ w.start }}–{{ w.stop }}{% if w.why %}<br><span style="font-size:12.5px;opacity:.65">{{ w.why }}</span>{% endif %}{% endfor %}
      {% else %}📅 **Morgen ({{ nls[tmr] }}):** geen vensters
      {% endif %}
```

The `markdown` card re-renders whenever one of the entities it reads changes; the
window times themselves rarely change, so add a hidden reference to a
frequently-updating entity (for example the voltage sensor) if you want the
*Nu* / *Volgende* / *Morgen* labels to roll over within a minute:

```yaml
<span style="display:none">{{ states('sensor.goodwe_time_of_use_laagste_celspanning') }}</span>
```

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

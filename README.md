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

- The 4 Time-of-Use windows as entities: **start time**, **stop time**,
  **mode** (off / charge / discharge), **power %** and **days**.
- A **Read from inverter** button that pulls the live schedule back into HA.
- An **Apply to inverter** button that writes the schedule in HA back to the
  inverter.
- An **ECO mode** switch (`work_mode = 3`); Time-of-Use only runs in ECO mode.
- Local polling with configurable interval, retries and a config flow — no YAML.

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
| `select.goodwe_time_of_use_window_1_mode` … `_4_mode` | `off` / `charge` / `discharge` |
| `number.goodwe_time_of_use_window_1_power` … `_4_power` | Power percentage (0–100) |
| `text.goodwe_time_of_use_window_1_days` … `_4_days` | Active days |
| `switch.goodwe_time_of_use_eco_mode` | ECO work mode on/off |
| `button.goodwe_time_of_use_read_from_inverter` | Read the schedule from the inverter |
| `button.goodwe_time_of_use_apply_to_inverter` | Apply the schedule to the inverter |

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

`mode = charge` stores a **negative** power percentage in the inverter (the
inverter charges the battery); `mode = discharge` stores a positive one. The
integration handles the sign for you.

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
      - action: select.select_option
        target:
          entity_id: select.goodwe_time_of_use_window_4_mode
        data:
          option: charge
      - action: number.set_value
        target:
          entity_id: number.goodwe_time_of_use_window_4_power
        data:
          value: 100
      - action: button.press
        target:
          entity_id: button.goodwe_time_of_use_apply_to_inverter
```

## A dashboard

An example dashboard that mirrors the four windows is in
[`dashboards/goodwe_tou.yaml`](dashboards/goodwe_tou.yaml). It uses
`entities`/`glance`/`grid` cards only, so no custom cards are required.

## Register map

See [`docs/registers.md`](docs/registers.md) for the full register layout and the
day-mask encoding.

## Troubleshooting

- **Cannot connect.** Check the dongle IP, that it is reachable
  (`ping 192.168.20.13`) and that nothing else is holding the dongle. The dongle
  only answers one request at a time; if the official GoodWe integration is also
  polling it, expect occasional timeouts (the client retries automatically).
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

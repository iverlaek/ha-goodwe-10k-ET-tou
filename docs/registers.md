# GoodWe Time-of-Use register map

Everything below was reverse-engineered against a **GoodWe GW10K-ET** hybrid
inverter (serial `9010KETU212W0307`, ARM firmware 17) with a **Wi-Fi/LAN Kit 2.0**
dongle, using the local UDP protocol on port `8899`.

> ⚠️ The dongle protocol is undocumented and the register map is firmware
> dependent. Treat this as a description of what was observed, not as a spec.

## Protocol

The dongle speaks a Modbus/RTU variant over UDP. A request is 8 bytes:

```
addr  cmd  off_hi  off_lo  val_hi  val_lo  crc_lo  crc_hi
```

| Field | Meaning |
| --- | --- |
| `addr` | Communication address (`247` / `0xF7` by default) |
| `cmd` | `0x03` read holding registers, `0x06` write single register |
| `off` | Register address (16-bit, big-endian) |
| `val` | Number of registers to read, or the value to write |
| `crc` | Modbus CRC-16 (poly `0xA001`) over `addr…val` |

A response is framed with a `0xAA 0x55` header:

```
0xAA 0x55  addr  cmd  <payload>  crc_lo  crc_hi
```

* **Read:** `<payload>` is `byte_count` followed by `byte_count` data bytes.
* **Write:** `<payload>` echoes `off_hi off_lo val_hi val_lo`.
* An exception response sets the high bit of `cmd` (`0x83` / `0x86`) and puts the
  Modbus exception code in the next byte.

The dongle is unreliable: it sometimes answers with an oversized datagram, a
fragmented one, or not at all. `goodwe_udp.py` therefore only accepts a
CRC-valid frame and retries each request a few times.

## Work mode

| Register | Meaning |
| --- | --- |
| `47000` | Work mode. `3` = ECO (Time-of-Use active), `0` = general |

Time-of-Use windows are only honoured while the inverter is in **ECO** mode.

## Time-of-Use windows

The four windows live in 16 consecutive holding registers, `47515`–`47530`, four
registers each:

| Window | Base register |
| --- | --- |
| 1 | `47515` |
| 2 | `47519` |
| 3 | `47523` |
| 4 | `47527` |

Within a window:

| Offset | Register | Meaning |
| --- | --- | --- |
| `+0` | `47515` / `47519` / `47523` / `47527` | Start time, `hour * 256 + minute` |
| `+1` | `47516` / `47520` / `47524` / `47528` | Stop time, `hour * 256 + minute` |
| `+2` | `47517` / `47521` / `47525` / `47529` | Signed power percentage |
| `+3` | `47518` / `47522` / `47526` / `47530` | Switch (high byte) + day mask (low byte) |

### Start / stop time

A time is packed big-endian as `hour * 256 + minute`, so `12:30` is
`12 * 256 + 30 = 3102` (`0x0C1E`).

### Power (offset `+2`)

A **signed** 16-bit value; the magnitude is the power percentage (0–100):

* `-100` … `-1` → **charge** at that percentage (e.g. `0xFF9C` = `-100`)
* `0` → idle
* `1` … `100` → **discharge** at that percentage

### Switch + days (offset `+3`)

The high byte is the window switch and the low byte is a day bitmask:

| High byte | Meaning |
| --- | --- |
| `0x00` | Window disabled |
| `0xFF` | Window enabled |

Day bitmask (low byte), one bit per day:

| Day | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Bit | 0 | 1 | 2 | 3 | 4 | 5 | 6 |
| Value | `0x01` | `0x02` | `0x04` | `0x08` | `0x10` | `0x20` | `0x40` |

So `0x7F` is every day, `0x1F` is Monday–Friday, `0x60` is the weekend, and
`0x20` is Saturday only.

The whole register is combined as `(switch << 8) | mask`, for example:

* `0x007F` — enabled? no: switch `0x00`, all days → window off
* `0xFF7F` — enabled, every day (read back as `65407`)
* `0xFF20` — enabled, Saturday only

### Firmware note

On ARM firmware 17 the layout is 8 bytes per window and there is **no
per-window state-of-charge** field. Newer firmware (≥ 19) uses a 12-byte layout
with additional SoC/charge-limit registers that this integration does not touch.

## Example snapshot

The schedule that was live on the test inverter (all four windows enabled in ECO
mode), as decoded by the integration:

| Window | Start | Stop | Mode | Power | Days |
| --- | --- | --- | --- | --- | --- |
| 1 | 12:30 | 15:30 | charge | 30 % | all |
| 2 | 10:45 | 13:45 | charge | 100 % | sat |
| 3 | 22:15 | 23:00 | charge | 100 % | all |
| 4 | 04:00 | 06:00 | charge | 100 % | all |

`work_mode = 3` (ECO).

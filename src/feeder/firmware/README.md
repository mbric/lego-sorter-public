# Conveyor controller firmware (Component 2)

Arduino **UNO R4 WiFi** + TB6600 + STEPPERONLINE 17HS19-2004S1.

Hardware: [hardware/feeder/wiring.md](../../../hardware/feeder/wiring.md) · plan: [02-part-separation.md](../../../plans/02-part-separation.md).

## Status (2026-07-24)

- Belt runs over **WiFi web UI** and USB serial
- Default speed **18820 ms/rev**; microstep treated as **32× (6400 pulse/rev)**
- SoftAP fallback if home WiFi fails
- Host app uses **`src/feeder/http_client.py`** (not serial)
- IR / `PART_PRESENT` not implemented (vision occupancy stops the belt today)

## Flash (Arduino IDE 2)

1. Copy secrets once:
   ```bash
   cp src/feeder/firmware/feeder/wifi_secrets.h.example \
      src/feeder/firmware/feeder/wifi_secrets.h
   ```
   Edit SSID/password. **Never commit** `wifi_secrets.h` (gitignored).
2. Open `feeder/feeder.ino` · board **UNO R4 WiFi**
3. **Tools → Firmware Updater** — WiFi module **0.6.0+** (0.4.0 → `ip=0.0.0.0`)
4. Upload over USB · Serial Monitor **115200** → `WiFi OK ip=…` then `Ready`

### SoftAP fallback

| | |
|--|--|
| SSID | `LegoConveyor` |
| Password | `legosort` |
| UI | http://192.168.4.1 |

On LAN: `http://<board-ip>/`.

## Pins / defaults

| Item | Value |
|------|-------|
| STEP / DIR | **D2** / **D3** (`STEP_ACTIVE_LOW=true`, `DIR_FORWARD_HIGH=false`) |
| `PULSES_PER_REV` | **6400** (~32× DIP) |
| Default speed | **18820 ms/rev** (floor ~6 s/rev) |

## Commands (serial 115200 or web UI)

`START` `STOP` `ADVANCE` `FORWARD` `REVERSE` `SPEED <ms>` `FASTER` `SLOWER` `STATUS` `HELP`

## Still TODO

- [ ] IR break-beam → auto-stop + `PART_PRESENT` (optional; vision already stops)
- [ ] Optional: lower DIP to 8× and set `PULSES_PER_REV=1600` for finer speed control

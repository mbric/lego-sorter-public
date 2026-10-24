# Conveyor hardware (Component 2)

As-built documentation for the 3D-printed chain-belt feeder.

- Photos / BOM capture: **2026-07-18**
- Firmware bring-up (WiFi + motion): **2026-07-24**

Mechanical role: [plans/02-part-separation.md](../../plans/02-part-separation.md).  
Firmware: [src/feeder/firmware/](../../src/feeder/firmware/).

## Identified components

| Role | Part | Notes |
|------|------|-------|
| MCU | **Arduino UNO R4 WiFi** | Custom 3D-printed enclosure; WiFi firmware **0.6.0** |
| Stepper | **STEPPERONLINE 17HS19-2004S1** | NEMA 17 bipolar, 2.0 A/phase, 59 N·cm, 1.8° |
| Driver | **TB6600-style microstep driver** | 9–42 VDC; run at **24 V**; ~**32×** microstep in practice |
| PSU | **BOSYTRO SJK-24V-480W** | 24 V / 20 A (motor + driver rail) |
| Belt | Print-in-place interlocking chain | White slats on black frame; NEMA 17 side-mounted |

Base design (planned): [Fully 3D-printed conveyor (MakerWorld)](https://makerworld.com/en/models/192994-conveyor-belt-fully-3d-printed) — confirm exact fork once STLs are uploaded.

## Control (working)

| Interface | How |
|-----------|-----|
| Web UI | `http://<uno-ip>/` on home WiFi (example: `192.168.4.30`) |
| SoftAP fallback | SSID `LegoConveyor` / `legosort` → http://192.168.4.1 |
| USB serial | 115200 — `START` `STOP` `FORWARD` `REVERSE` `SPEED` … |
| Default belt speed | **18820 ms per motor rev** (~18.8 s) |

Details: [src/feeder/firmware/README.md](../../src/feeder/firmware/README.md).

## Photos

Reference shots in [`photos/`](photos/):

| File | What it shows |
|------|----------------|
| [01-mcu-overview.png](photos/01-mcu-overview.png) | Uno R4 in enclosure — headers + terminal block |
| [02-mcu-power-gnd.png](photos/02-mcu-power-gnd.png) | Power / analog headers; GND jumper |
| [03-mcu-digital-pins.png](photos/03-mcu-digital-pins.png) | Digital header; **D2 + D3** signal wires |
| [04-driver-tb6600.png](photos/04-driver-tb6600.png) | Driver terminals, DIP tables, as-wired coils |
| [05-psu-24v.png](photos/05-psu-24v.png) | 24 V PSU label and output terminals |
| [06-conveyor-assembly.png](photos/06-conveyor-assembly.png) | Full belt + motor mount |
| [07-motor-label.png](photos/07-motor-label.png) | Motor P/N label (17HS19-2004S1) |
| [08-mcu-uno-r4-enclosure.png](photos/08-mcu-uno-r4-enclosure.png) | Enclosure side — `UNO R4` emboss |

Pin maps: [wiring.md](wiring.md). Part numbers: [BOM.md](BOM.md).

## Layout

```
hardware/feeder/
├── README.md
├── BOM.md
├── wiring.md
├── photos/
└── stl/            # (TBD)
```

## Host software

Gradio / orchestrator uses **`src/feeder/http_client.py`** (WiFi HTTP). Serial remains available for bring-up.

## Still TODO

- [ ] Upload final STL/STEP files to `stl/`
- [ ] Confirm MakerWorld / Fusion source link for *this* print
- [ ] Measure as-built belt width, run length, incline, cleat pitch
- [ ] Optional: IR break-beam + pin (vision occupancy already stops the belt)
- [ ] Optional: DIP 8× + `PULSES_PER_REV=1600` for finer speed control

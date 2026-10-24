# Conveyor wiring & pin configuration

As photographed 2026-07-18. Photos: [`photos/`](photos/).

> **WIP:** Signal pin *roles* (which of D2/D3 is STEP vs DIR) and enable wiring are not fully labeled in firmware yet. Treat the MCU pin numbers and motor coil colors as ground truth from the photos/datasheet; re-verify before first power-on with load.

## Block diagram

```
  AC mains ──► BOSYTRO 24V PSU ──► TB6600 VCC / GND
                                         │
                    STEPPERONLINE        │ A+ A- B+ B-
                    17HS19-2004S1 ◄──────┘
                                         │
  Arduino Uno R4 ── PUL / DIR [/ENA] ──► TB6600 signal side
       │
       └── USB serial to Mac (feeder protocol)
```

## Arduino Uno R4

Enclosure embossed `UNO R4` — [08-mcu-uno-r4-enclosure.png](photos/08-mcu-uno-r4-enclosure.png).

### Observed jumper connections

| MCU pin | Wire | Likely role | Photo |
|---------|------|-------------|-------|
| **D2** | Blue | STEP (PUL) *or* DIR — confirm in firmware | [03](photos/03-mcu-digital-pins.png) |
| **D3** (`~`) | Blue | The other of STEP / DIR | [03](photos/03-mcu-digital-pins.png) |
| **GND** (power header, between `Vin` and the other `GND`) | Black | Common ground with driver signal side | [02](photos/02-mcu-power-gnd.png) |

Earlier overview shot also shows activity around the digital header and a green screw-terminal breakout on the enclosure — [01-mcu-overview.png](photos/01-mcu-overview.png). Prefer the close-up in `03` for pin numbers.

### Free / reserved

| Pins | Status |
|------|--------|
| D0 (RX), D1 (TX) | Leave free for USB serial programming / host protocol |
| D4–D13, A0–A5 | Available (IR break-beam candidate: any free digital or A-pin) |
| 5V / 3.3V | Logic for sensors; do **not** power the stepper from these |

### Firmware pin map (`src/feeder/firmware/feeder/`)

| Signal | Uno R4 pin | Driver / notes |
|--------|------------|----------------|
| STEP / PUL | **D2** | `PUL−` with common-anode (`PUL+`/`DIR+` → **5V**). Swap D2↔D3 if no motion. |
| DIR | **D3** | `DIR−`. `DIR_FORWARD_HIGH = false` after on-belt tuning. |
| Step edge | active **LOW** | `STEP_ACTIVE_LOW = true` |
| ENA | unused | leave open on this clone |
| GND | GND | Common with driver/PSU ground |
| Microstep | — | Firmware assumes **6400 pulse/rev (32×)** |
| Speed | — | Default **18820 ms/rev** |

If the motor holds (stiff) but does not turn, swap the D2/D3 jumpers first, then try `STEP_ACTIVE_LOW = false`.

## Stepper motor — coil colors

STEPPERONLINE **17HS19-2004S1** — [07-motor-label.png](photos/07-motor-label.png).

Official bipolar mapping ([datasheet](https://www.omc-stepperonline.com/download/17HS19-2004S1.pdf)):

| Coil | Color |
|------|-------|
| **A+** | Black |
| **A−** | Green |
| **B+** | Red |
| **B−** | Blue |

Motor lead order as it exits the housing (conveyor photo): blue / green / red / black — [06-conveyor-assembly.png](photos/06-conveyor-assembly.png).

## TB6600-style driver

[04-driver-tb6600.png](photos/04-driver-tb6600.png) — label `Microstep Driver`, `DC: 9~42VDC`.

### Power & motor terminals (as-wired)

| Terminal | Expected connection |
|----------|---------------------|
| **VCC** | +24 V from PSU `+V` |
| **GND** | PSU `−V` |
| **A+** | Motor Black |
| **A−** | Motor Green |
| **B+** | Motor Red |
| **B−** | Motor Blue |

If the belt runs the wrong way, swap **DIR** sense in software or reverse one coil pair (swap A+↔A− *or* B+↔B−).

### Signal terminals

| Terminal | Function |
|----------|----------|
| **PUL+** / **PUL−** | Step pulse |
| **DIR+** / **DIR−** | Direction |
| **ENA+** / **ENA−** | Enable (often active-low; leaving ENA open may keep the driver enabled — check your clone) |

Wire colors on the signal side in the photo are thin jumpers (orange / yellow / red / etc.) — treat the **terminal labels** as authoritative, not jumper color.

### Driver DIP switches

Tables printed on the unit (SW1–SW3 = microstep, SW4–SW6 = current). Transcription from the label photo:

**Microstep (SW1, SW2, SW3)**

| Mode | Pulse/rev | SW1 | SW2 | SW3 |
|------|-----------|-----|-----|-----|
| NC | — | ON | ON | ON |
| 1 | 200 | OFF | ON | ON |
| 2/A | 400 | ON | OFF | ON |
| 2/B | 400 | OFF | OFF | ON |
| 4 | 800 | ON | ON | OFF |
| 8 | 1600 | OFF | ON | OFF |
| 16 | 3200 | ON | OFF | OFF |
| 32 | 6400 | OFF | OFF | OFF |

**Current (SW4, SW5, SW6)**

| Current (peak) | SW4 | SW5 | SW6 |
|----------------|-----|-----|-----|
| 0.5 A (0.7 pk) | ON | ON | ON |
| 1.0 A (1.2 pk) | OFF | ON | ON |
| 1.5 A (1.7 pk) | ON | OFF | ON |
| **2.0 A (2.2 pk)** | **OFF** | **OFF** | **ON** |
| 2.5 A (2.7 pk) | ON | ON | OFF |
| 2.8 A (2.9 pk) | OFF | ON | OFF |
| 3.0 A (3.2 pk) | ON | OFF | OFF |
| 3.5 A (4.0 pk) | OFF | OFF | OFF |

**Recommended for 17HS19-2004S1:** current **2.0 A** (`OFF OFF ON`). Start with microstep **8** or **16** for smoother belt motion; firmware pulse rate must match the chosen pulse/rev.

> DIP clone tables sometimes disagree with the silkscreen. Prefer the table **printed on your driver**.

## Power supply

BOSYTRO **SJK-24V-480W** — [05-psu-24v.png](photos/05-psu-24v.png).

| Terminal | Use |
|----------|-----|
| IEC C14 + rocker | AC mains in |
| **+V** (×3) | +24 V to driver `VCC` (and any other 24 V loads) |
| **−V** (×3) | Return / common with driver `GND` |
| **ADJ** | Fine-tune voltage (leave near 24.0 V) |

Do not power the Uno from this 24 V rail without a regulator — use USB or a separate 7–12 V into the Uno barrel jack / `Vin` within Uno R4 limits.

## Safety / bring-up checklist

1. Set driver current DIP **before** enabling motor power.
2. Verify A±/B± against the color table above.
3. Common MCU GND with driver signal ground.
4. First spin: no belt load, low step rate; confirm direction.
5. Then mount belt and tune `STEP_DELAY_US` for ~25 mm/s (see plan).

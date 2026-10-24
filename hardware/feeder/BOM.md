# Conveyor BOM (as-built 2026-07-18)

From photos in [`photos/`](photos/). Quantities are for one feeder.

## Electronics

| Qty | Part | Spec / P/N | Photo |
|-----|------|------------|-------|
| 1 | MCU | Arduino **Uno R4 WiFi** in 3D-printed case | [08](photos/08-mcu-uno-r4-enclosure.png) |
| 1 | Stepper motor | STEPPERONLINE **17HS19-2004S1** — NEMA 17, bipolar, 2.0 A/phase, 59 N·cm (84 oz·in), 1.8°, 4-wire | [07](photos/07-motor-label.png) |
| 1 | Stepper driver | Generic **TB6600-style** microstep driver, DC 9–42 V | [04](photos/04-driver-tb6600.png) |
| 1 | Power supply | BOSYTRO **SJK-24V-480W** — AC 110/220 V in, **DC 24 V / 20 A** out | [05](photos/05-psu-24v.png) |
| 1 | IR break-beam | *Not yet installed* — plan called for 3–5 V digital | — |

### Motor electrical (datasheet)

| Parameter | Value |
|-----------|-------|
| Rated current / phase | 2.0 A |
| Phase resistance | ~1.4–1.6 Ω |
| Holding torque | 0.59 N·m |
| Step angle | 1.8° (200 full steps/rev) |
| Body | 42×42×48 mm, 5 mm D-shaft |

Datasheet: [omc-stepperonline.com — 17HS19-2004S1](https://www.omc-stepperonline.com/nema-17-bipolar-59ncm-84oz-in-2a-42x48mm-4-wires-w-1m-cable-connector-17hs19-2004s1)

### Driver current setting

Motor is rated **2.0 A**. Set driver DIP current to **2.0 A** (or 1.5 A while tuning if it runs hot). See [wiring.md](wiring.md#driver-dip-switches).

### PSU notes

24 V is appropriate for the TB6600 + this motor. The 480 W / 20 A supply is oversized for one NEMA 17 — fine for shared rails later; keep continuous load under ~80% of rating per the PSU label.

## Mechanical (printed)

| Qty | Part | Material | Notes |
|-----|------|----------|-------|
| 1 | Chain belt loop | PLA (white) | Interlocking print-in-place slats |
| 1 set | Side frames + motor mount | PLA/PETG (black) | NEMA 17 side mount |
| 1 | Drive sprocket / shaft | — | Coupled to motor shaft |
| ? | Side T/C mounting tabs | Black | Along frame rails |

Exact STL set and source link still TBD — see README TODO.

## Consumables / interconnect

| Qty | Item | Notes |
|-----|------|-------|
| — | Dupont / jumper wires | MCU ↔ driver (PUL / DIR / ENA / GND) |
| — | Motor extension leads | 4-wire bipolar to driver A± / B± |
| — | Screw terminal / barrel adapters | PSU 24 V → driver VCC/GND |
| 1 | IEC C13 mains cable | For PSU inlet |

---
component: 2 — Part separation
parent: ../PLAN.md
status: in-progress (belt running; STLs + IR still open)
role: "Singulate parts and drop one at a time into the C4 catch tray."
---

# C2 — Part separation

> Part of the [master plan](../PLAN.md). Receives from [C1](01-bulk-storage.md); hands off to [C3](03-scanner-identification.md) / [C4](04-sorted-storage.md).

Integration orchestration: [PLAN.md — Integration flow](../PLAN.md#integration-flow).

## Status (2026-07)

Belt runs under **UNO R4 WiFi** firmware (web UI + serial). Gradio drives it over **HTTP** (`src/feeder/http_client.py`).

**Canonical as-built docs** (don't duplicate here):

- [hardware/feeder/README.md](../../hardware/feeder/README.md) — photos, BOM, control URLs
- [wiring.md](../../hardware/feeder/wiring.md) · [BOM.md](../../hardware/feeder/BOM.md)
- [firmware README](../../src/feeder/firmware/README.md) — flash + commands

### Remaining

- [ ] Upload STLs/STEP to `hardware/feeder/stl/` + source model link
- [ ] As-built dimensions (width, length, incline, cleat pitch)
- [ ] Optional: IR break-beam → `PART_PRESENT` (vision occupancy already stops the belt)
- [ ] Jam / singulation tuning notes

## Job

One part at a time to the belt discharge → drop onto the [C3a scan stage](05-scan-chamber.md) (then trapdoor into the [C4 scoop](04-sorted-storage.md)). C2 does **not** provide the Brickognize background — the fixed stage does.

```
   hand-fed / hopper (C1 later)
            │
            ▼
   ┌───────────────────────┐
   │  CHAIN BELT (~15–20°) │   NEMA 17, cleated
   └──────────┬────────────┘
              │ discharge
        ┌─────▼───────┐
        │ SCAN STAGE  │   lit box + trapdoor (C3a)
        └─────┬───────┘
              │ after ID
        ┌─────▼───────┐
        │ SCOOP @home │   ReBeL (C4)
        └─────────────┘
```

Chain texture wrecks identification — scan on the matte stage, not on the belt.

## Starting dimensions

| Dimension | Value | Rationale |
|-----------|-------|-----------|
| Belt width | ~70 mm | 2×4 brick + margin |
| Belt run | 250–300 mm | Spread bunched parts |
| Cleat height / pitch | 6–8 mm / 35–45 mm | One pocket ≈ one part |
| Incline | 15–20° | De-clump |
| Drop height | As low as practical | Reduce bounce-out |
| Speed | Slow (~25 mm/s start) | Better singulation |

Base print: [MakerWorld fully 3D-printed conveyor](https://makerworld.com/en/models/192994-conveyor-belt-fully-3d-printed).

## Control

WiFi web UI **or** USB serial (115200). Host software uses HTTP:

| Command | Action |
|---------|--------|
| `START` / `STOP` | Run / halt |
| `ADVANCE` | ~1 motor revolution |
| `FORWARD` / `REVERSE` | Direction |
| `SPEED` / `FASTER` / `SLOWER` | ms per rev (default **18820**) |
| `STATUS` | Idle/running + speed + IP |

IR auto-stop (`PART_PRESENT`) not installed yet — the Gradio feed loop stops on vision occupancy instead.

## Reliability (Gate G3)

100 mixed parts. Pass: single-feed ≥ 85%, jams ≤ 10/100. Else semi-manual fallback ([PLAN.md](../PLAN.md#fallback-demo-mode)).

## Done criteria

- One part per cycle to the tray
- Reliable drop (tune with C4)
- Passes G3 or documented fallback

## Alternatives considered

Chosen 2026-06-26: print-in-place chain + NEMA 17. Not chosen: Obeltus, Robot Pi Shop mini conveyor, modular MakerWorld kits, 2-zone belt.

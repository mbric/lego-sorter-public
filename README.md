# lego-sorter

A LEGO sorting machine built in stages: computer vision identifies parts, a 3D-printed conveyor feeds them one at a time, and an igus ReBeL cobot dumps them into bins.

**Status & build order:** [PLAN.md](PLAN.md) is the hub. Component deep-dives live in [`plans/`](plans/).

**Concept model (schematic 3D):** [docs/concept-model/](docs/concept-model/) — browser Three.js viewer, plus an [OpenSCAD twin](docs/concept-model/scad/) for parametric preview / STL export (not fabrication CAD).

![Concept draft](docs/concept-model/preview.jpg)

| # | Component | Status | Plan |
|---|-----------|--------|------|
| 1 | Bulk storage | Planned | [01](plans/01-bulk-storage.md) |
| 2 | Part separation | **In progress** (belt running) | [02](plans/02-part-separation.md) |
| 3 | Scanner / ID | **In progress** (Gradio + Brickognize + feed loop) | [03](plans/03-scanner-identification.md) · [chamber](plans/05-scan-chamber.md) |
| 4 | Sorted storage | **In progress** (CRI verified) | [04](plans/04-sorted-storage.md) |

## Quick start — Scanner UI (C3)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run_brickognize_demo.py
```

Opens a browser UI:

1. **Setup** — pick camera, set conveyor URL, capture empty tray reference, tune landing zone
2. **Feed loop** — belt advances until vision sees a part, captures a still, identifies via Brickognize, waits for clear
3. **Robot teach** — nudge the ReBeL, save home/rest/bin poses (needs `requirements-robot.txt`)

Needs internet for Brickognize. Conveyor talks over **WiFi HTTP** (UNO R4), not USB serial.

| | |
|---|---|
| **Code** | `src/demo_brickognize/`, `src/orchestrator/`, `src/feeder/http_client.py` |
| **Hardware** | [hardware/feeder/](hardware/feeder/) |
| **Firmware** | [src/feeder/firmware/](src/feeder/firmware/) |

## Robot (C4)

```bash
source .venv/bin/activate && pip install -r requirements-robot.txt
export ROBOT_IP=192.168.3.11
python3 run_robot_smoke_test.py       # no motion
python3 run_robot_move_test.py        # arm moves — keep clear
python3 run_brickognize_demo.py       # Robot teach tab (or run_robot_teach_ui.py)
python3 run_robot_dump_test.py --park # park at rest + disable brakes
```

Close **iRC** first (one CRI client only). Networking: [docs/robot-networking.md](docs/robot-networking.md). Teaching + idle safety: [docs/robot-teaching.md](docs/robot-teaching.md).

## Layout

```
lego-sorter/
├── PLAN.md                 # status, build order, gates
├── plans/                  # C1–C4 + scan-chamber research
├── docs/concept-model/     # Three.js schematic viewer (docs visuals)
├── run_brickognize_demo.py # C3 UI
├── run_robot_*.py          # C4
├── hardware/feeder/        # C2 as-built (BOM, wiring, photos)
├── data/                   # sessions, empty_tray, landing_zone
└── src/
    ├── demo_brickognize/   # Gradio app (→ rename scanner/ later)
    ├── orchestrator/       # sort_cycle, routing
    ├── feeder/             # http_client + firmware/
    └── robot/              # CRI helpers
```

## Acknowledgments

- [Brickognize](https://brickognize.com/) — part identification API
- [BrickSortingMachine](https://bricksortingmachine.com/) — architecture inspiration

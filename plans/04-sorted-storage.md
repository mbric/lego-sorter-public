---
component: 4 — Sorted storage
parent: ../PLAN.md
status: in-progress (ReBeL on hand; Mac/CRI control verified)
role: "Catch the identified part from the scan-stage trapdoor and dump into the correct bin."
---

# C4 — Sorted storage

> Part of the [LEGO Sorter master plan](../PLAN.md). Receives a bin assignment from [C3 — Scanner / ID](03-scanner-identification.md).

The **igus ReBeL is on hand** and **Python control from macOS is verified** (USB ethernet → CRI port 3920; see [docs/robot-networking.md](../docs/robot-networking.md) and `run_robot_*.py`). Catch tray + dump poses still to teach; software feed loop already stops/starts the belt from Gradio.

Integration: [PLAN.md — Integration flow](../PLAN.md#integration-flow).

## MVP approach — scoop catcher, not a gripper

The end effector is a **fixed printed scoop** on the tool flange — no vacuum, no fingers. It does **not** need to be the scan surface: ID happens on the [fixed stage](05-scan-chamber.md); the scoop only has to be **home under the trapdoor** when it opens.

Cycle (target B):

1. Part is identified on the scan stage (arm may still be returning from the previous dump)
2. Scoop at **home**, level under the trapdoor → door opens → part drops in
3. Arm moves to the target bin and **dumps** (wrist tilt)
4. Arm returns home while the next part can already be scanning on the stage
5. Trapdoor stays closed until the next `id_done ∧ arm_home`

### Why this is the simpler MVP

| Concern | Vacuum/gripper pick | Scoop catcher |
|---------|---------------------|---------------|
| Grasp arbitrary shapes | Hard | **N/A — part falls in** |
| Camera→robot calibration | Pixel→XY | **None — camera never locates for the arm** |
| Taught poses | Per-pick + per-bin | **Home under trapdoor + one dump per bin** |
| End effector | Pump, cup, tubing | **One printed scoop** |
| Failure mode | Missed pick | Bounce-out / incomplete dump / trapdoor jam |

### Tray / scoop design

Dumb bucket optimized for **catch + dump**, not photography. Coordinate drop height with [C3a trapdoor](05-scan-chamber.md).

- **Back wall + side lips** — retain part during motion
- **Open front** toward dump
- **Matte floor** optional (no longer the Brickognize background)
- **Home** = centered under the closed trapdoor, level, clear of the door swing
- **Dump** by wrist tilt; brief shake/tap if needed

### Control

- CRI-Python-Lib: `connect` → `set_active_control(True)` → `enable()` → `get_referencing_info()` → moves. **Verified on macOS.**
- Motion is a fixed pose sequence: `home` → `bin_N` (approach + tilt) → `home`. Teach each pose once via jog/nudge; store joint targets in [`data/robot/poses.json`](../data/robot/poses.json). See [docs/robot-teaching.md](../docs/robot-teaching.md).
- Also teach **`rest`**: idle park clear of furniture; `park` / script exit disables drives (brakes). E-stop is last resort, not the primary idle strategy.
- Belt may feed the **next** part onto the stage while the arm dumps (trapdoor closed). Do not open the trapdoor until `arm_home`.
- ROS 2 / MoveIt — unnecessary for a fixed pose set.

**Fallback:** servo rotary bin slide ([BrickSortingMachine](https://bricksortingmachine.com/diy-lego-sorting-machine)) if the arm route stalls — not the current plan.

## Bin addressing

- Fixed bins within reach; one taught dump pose each
- Start with the MVP color set (4–5 bins) + an `unknown` bin
- **Set bins:** reserve one bin per target set for [set-aware sorting](03-scanner-identification.md#set-aware-sorting-set-harvesting), or one shared "set parts" bin if bins are scarce
- Bin assignment comes from the [C3 routing policy](03-scanner-identification.md#bin-routing-policy); C4 only executes the dump pose for the assigned bin
- Low-confidence parts → dump into `unknown` (never stall the loop)

## TODO — before first integrated run

- [ ] Print + mount scoop (catcher under trapdoor)
- [ ] Teach `home` under the scan-stage trapdoor (level, clear of door) — **Robot teach** tab in `run_brickognize_demo.py`
- [ ] Teach `rest` park pose clear of the finished space
- [ ] Teach one dump pose per bin (approach + dump)
- [ ] Drop test from trapdoor: 20 parts land and stay
- [ ] Dump test: each bin clears (`test dump <id>` / `run_robot_dump_test.py`)
- [ ] Orchestrator: `trapdoor_open` only if `id_done ∧ arm_home`; belt advance after stage clear
- [ ] Build chamber Phases 1→3 ([05](05-scan-chamber.md))

## Tooling (Mac / CRI)

| Script | Purpose |
|--------|---------|
| `run_robot_teach_ui.py` | Standalone Gradio teach (same UI as main app tab) |
| `run_robot_teach.py` | CLI teach (same `poses.json`) |
| `run_robot_dump_test.py` | One-shot dump or `--park` |
| `src/robot/poses.py` | Pose JSON load/save/capture |
| `src/robot/dump.py` | Real `dump_cycle` / `dump_to_bin` / `park_and_release` |
| `src/robot/dump_stub.py` | Offline placeholder for Gradio feed loop |
| `src/robot/teach_session.py` / `teach_app.py` | Teach session + UI |

## Done criteria

- Each classified part is dumped into its correct bin
- Low-confidence / problem parts → `unknown` bin
- Sustained run without parts bouncing out or dumps missing the bin

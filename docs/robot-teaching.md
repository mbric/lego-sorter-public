# igus ReBeL — teaching home, rest, and bin dump poses

How to map physical locations for Component 4. Networking: [robot-networking.md](robot-networking.md). Design: [plans/04-sorted-storage.md](../plans/04-sorted-storage.md).

## How industry does this

Fixed-bin cells use **teach pendant** practice, not path planning:

1. Jog the arm to a location
2. Record that pose as a named waypoint
3. Replay a fixed sequence at runtime

We do the same from the Mac over CRI: nudge → `save` → `test dump`. Joint angles are the replay source (repeatable for a fixed cell). Cartesian XYZ is stored only as a human-readable snapshot.

## Pose map

| Pose | Meaning |
|------|---------|
| **home** | Scoop level under the scan-stage trapdoor (working catch pose) |
| **rest** | Idle park — clear of bins, furniture, and walkways |
| **bin / approach** | Clear of the bin, ready to tilt (same reach, less pour) |
| **bin / dump** | Wrist tilted to pour into that bin |

Dump sequence: `home → approach → dump → (hold) → approach → home`.

When you finish a session, prefer **rest**, then **disable drives** (brakes engage).

## Safety in a finished space

**E-stop alone is not enough.** It is the last resort. Layer these:

1. **Teach a clear `rest` pose** — arm tucked away from walls, lamps, shelves. Use `park` / quit so every teach session ends there.
2. **Disable drives when idle** — our scripts call `disable()` on exit; brakes hold position. Do not leave the arm enabled and hovering over furniture.
3. **Low override while teaching** — default ~20% in `poses.json` (`override_pct`). Raise only after paths look clean.
4. **Approach before dump** — never teach a dump that swings through a shelf; approach should be collision-free from home.
5. **Clear the envelope** — nothing in the arm’s reach you care about during teach/test.
6. **E-stop within reach** — hardware emergency stop on the ReBeL / pendant. Use it if motion surprises you; then re-enable and re-reference as needed.
7. **One CRI client** — close Windows iRC before Python scripts.

Optional later (iRC on Windows): virtual boxes / workspace limits on the controller for hard soft-stops. Not required for the MVP teach loop.

```
Working:     … → home (catch) → dump cycle → home → …
Idle:        home → rest → disable (brakes) → power can stay on
Done for day: park → power off if desired
```

## Quick start

### In the main sorter UI (recommended)

```bash
source .venv/bin/activate
pip install -r requirements.txt -r requirements-robot.txt
export ROBOT_IP=192.168.3.11
python3 run_brickognize_demo.py
```

Open the **Robot teach** tab → **Connect** → nudge with XYZ / joint buttons → **Save** → **Test dump** → **Park + disable**.

Does not auto-connect (CRI is one client; close iRC first). Feed loop and teach share the app but teach stays opt-in.

### Standalone teach UI / CLI

```bash
python3 run_robot_teach_ui.py   # same Gradio controls, own window
python3 run_robot_teach.py      # text commands
```

Suggested teach order:

1. Nudge the scoop **under the trapdoor, level** → Save **home**
2. Move to a tucked clear pose → Save **rest**
3. For each bin (`red` `blue` `yellow` `green` `unknown`):
   - clear approach → Save **approach**
   - tilt to pour → Save **dump**
4. **Test dump** for each bin at low speed; adjust and re-save
5. **Park + disable** when finished

### CLI commands (if using `run_robot_teach.py`)

| Command | Action |
|---------|--------|
| `nudge x\|y\|z +\|- [mm]` | Cartesian step |
| `nudge a1..a6 +\|- [deg]` | Joint step (wrist `a5`/`a6` for dump) |
| `save home` / `save rest` | Capture current pose |
| `save bin <id> approach\|dump` | Capture bin waypoint |
| `goto home\|rest` | Replay a saved pose |
| `goto bin <id> approach\|dump` | Replay bin waypoint |
| `test dump <id>` | Full dump cycle |
| `park` | Go to rest (or home) and disable |
| `override <pct>` / `vel <pct>` | Speed limits |
| `stop` | Halt motion |

Poses file: [`data/robot/poses.json`](../data/robot/poses.json).

### One-shot scripts

```bash
python3 run_robot_dump_test.py red           # dump cycle, leave at home, disable
python3 run_robot_dump_test.py red --park-after
python3 run_robot_dump_test.py --park        # rest + disable only
```

## Re-teaching

If you move the stage, bins, or scoop, re-teach affected poses (`home` / that bin’s approach+dump). `rest` only needs a refresh if the parked clearance changes. After power-on, referencing is handled by `prepare_for_motion` (absolute encoders).

# igus ReBeL — networking notes

How to reach the ReBeL from a Mac for CRI (Component 4). **Verified:** ping, smoke test, and moves via USB ethernet. Sorting use case: [04-sorted-storage.md](../plans/04-sorted-storage.md).

## Quick reference

| Item | Value |
|------|-------|
| Factory ethernet IP (`eth0`) | **`192.168.3.11`** (static — no DHCP) |
| CRI port | **`3920`** |
| Typical Mac address on robot subnet | **`192.168.3.1`** / `255.255.255.0` |
| CRI simulator | `127.0.0.1:3921` |

```bash
export ROBOT_IP=192.168.3.11
export ROBOT_CRI_PORT=3920
```

The robot does **not** join home WiFi/DHCP. One CRI client at a time — **close iRC** before Python scripts.

## Recommended: USB ethernet → Mac

```
ReBeL (192.168.3.11) ──ethernet──► USB adapter ──► Mac
Mac WiFi ───────────────────────────────────────► internet
```

**Mac** (System Settings → Network → USB LAN): Configure IPv4 **Manually**, IP `192.168.3.1`, mask `255.255.255.0`, **Router blank**. Keep Wi‑Fi on DHCP; put Wi‑Fi above the USB adapter in Service Order if needed.

```bash
networksetup -setmanual "USB 10/100/1000 LAN" 192.168.3.1 255.255.255.0
ping -c 4 192.168.3.11
```

## What not to bother with

| Approach | Outcome |
|----------|---------|
| USB ethernet → Mac | **Works** (use this) |
| Plug robot into Eero LAN | Painful — robot stays `192.168.3.11`; needs a Mac alias on that subnet. Abandoned for USB ethernet |
| RavPower / travel-router bridge | **Breaks home WiFi** (port auto-switches to WAN) |
| SSH into ReBeL Pi | No root password; not needed for CRI |

**Later:** static WiFi IP via iRC on Windows (e.g. `192.168.4.200`). Until then assume ethernet `192.168.3.11`.

## Verify

```bash
ping -c 4 192.168.3.11
ROBOT_SCAN_SUBNET=192.168.3.0/24 python3 run_find_robot.py
source .venv/bin/activate && pip install -r requirements-robot.txt
export ROBOT_IP=192.168.3.11
python3 run_robot_smoke_test.py    # close iRC first
```

## Scripts

| Script | Purpose |
|--------|---------|
| `run_find_robot.py` | Scan for CRI/SSH |
| `run_robot_status.py` | Read-only status |
| `run_robot_smoke_test.py` | Connect, no motion |
| `run_robot_move_test.py` | Small Z move |
| `run_robot_move_test.py --reference` | Force reference + move |

| `run_robot_teach_ui.py` | Standalone Gradio teach (also a tab in the main demo) |
| `run_robot_teach.py` | CLI teach (same poses file) |
| `run_robot_dump_test.py` | One-shot dump or park |
| `src/robot/config.py`, `cri_session.py`, `poses.py`, `dump.py`, `teach_*.py` | Shared helpers |

Pose teaching + idle safety (rest pose, brakes, E-stop): **[robot-teaching.md](robot-teaching.md)**.

## CRI notes

- ReBeL uses **absolute encoders** — refresh with `GetReferencingInfo`, not `ReferenceAllJoints`
- Typical: `connect` → `set_active_control(True)` → `reset()` → `enable()` → `get_referencing_info()` → move
- `enable()` clicks brakes; long-running sorter should enable once and keep the session open
- Status may show `operation_mode=NOT_ENABLED` while moves work — trust `referencing=REFERENCED` + `kinematics_state=NO_ERROR`
- iRC (Windows) for teach/jog/WiFi setup; day-to-day sorting is CRI from the Mac

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Ping timeout | Wait after power-on; check link LED; confirm USB ethernet IP |
| No device in Eero app | Normal — robot ignores DHCP |
| CRI connect fails | Close iRC |
| Internet dies with travel router | Use USB ethernet instead |
| `python` not found | Use `python3` or `.venv/bin/python` |

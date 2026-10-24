---
component: 3a — Scan chamber (fixed stage + light box + trapdoor)
parent: 03-scanner-identification.md
status: research done; build not started
role: "Fixed lit scan stage under the belt tip; after ID, trapdoor drops the part into the ReBeL scoop."
---

# C3a — Scan chamber: fixed stage + light box + trapdoor

> Research plan for the [C3 scan station](03-scanner-identification.md#scan-station). Motivated by open-air tray issues: AE drift, hand/shadow false triggers, and single-angle Brickognize misses on small or awkwardly oriented parts.

## Architecture decision (locked) — option B

**Scan on a fixed stage; scoop only catches after ID.**

1. Conveyor drops one part onto a **matte fixed floor** inside a light box at the belt tip
2. Belt stops → part settles → **overhead + 45°** cameras identify (Brickognize)
3. Meanwhile the ReBeL can **return home** under the stage (pipeline)
4. When ID is done **and** scoop is home + level → **trapdoor opens** → part falls into scoop
5. Arm dumps to bin; trapdoor closes; belt advances the next part onto the stage

```
   chain belt ──discharge──► ┌─ LIGHT BOX ─────────────────┐
                             │  cams (top + 45°) + LEDs    │
                             │  ┌─────────────────────┐    │
                             │  │ fixed scan floor    │    │
                             │  │     [trapdoor]      │    │
                             │  └──────────┬──────────┘    │
                             └─────────────┼───────────────┘
                                           ▼  after ID + arm home
                                    ┌────────────┐
                                    │ scoop @home│  C4 — dumb bucket
                                    └────────────┘
                                           │ dump → bins
```

**Why B (not A or C):**

| | A — scan in scoop @ home | **B — fixed stage + trapdoor (chosen)** | C — scan on belt tip |
|---|---|---|---|
| Photo background | Matte scoop | **Matte fixed floor** | Chain texture (bad) |
| Light isolation | Arm aperture leaks | **Nearly sealed box** | Open belt path |
| Multi-cam / 45° | Scoop walls fight FOV | **Easy — nothing moves** | Easy, but bad floor |
| Pipelining | Serial (arm waits for scan) | **Scan ∥ arm return**; scoop only needed before trapdoor | Serial; spacing-sensitive |
| Extra mechanism | No | **Trapdoor servo** | Stop lip if part must hold at tip |

**A stays the fallback** if the trapdoor becomes a jam magnet. **C is rejected** for Brickognize capture (chain background + tip spacing); a belt-tip cam may still help C2 singulation later, not ID.

Closest prior art: [basically Sorter V2](https://basically.website/sorter-v2) (lit chamber, then drop to handling) and [BrickSortingMachine](https://bricksortingmachine.com/sorting-machine-diy-build-instructions-scanner) (mirror / multi-view in a fixed scanner).

## What the research says (prior art)

Every serious LEGO sorter converges on the same recipe: a **small enclosed chamber**, **overwhelming diffuse light**, and **more than one viewing angle**.

| Project | Multi-angle approach | Lighting | Camera | Notes |
|---|---|---|---|---|
| [Daniel West's Universal Sorter](https://www.youtube.com/watch?v=04JkdHEX3Yk) ([CV write-up](https://medium.com/data-science/a-high-speed-computer-vision-pipeline-for-the-universal-lego-sorting-machine-253f5a690ef4)) | Single top camera | Blast tiny chamber full of light → fast shutter | Pi Camera V2 | Fixed pose/focus first |
| [BrickSortingMachine](https://bricksortingmachine.com/sorting-machine-diy-build-instructions-scanner) | **Camera + 45° mirror = two views** | Strong, non-flickering, isolated | Single cam | Sync + bandwidth win |
| [basically Sorter V2](https://basically.website/sorter-v2) | Multi-angle in lit chamber | Enclosed chamber | Multi-cam | **Brickognize**; rotary / clear platform |
| [OpenBlok](https://github.com/blokbot-io/OpenBlok) | Single station | Enclosed | MOKOSE 4K + C-mount | Pricier camera path |
| [HackMakeMod backlight](https://hackmakemod.com/blogs/projects/parts-counter-blob-counter) | n/a | **Backlight won** over ring/front | ESP32-CAM | Fixed camera height critical |
| [Brickognize paper](https://www.mdpi.com/1424-8220/23/4/1898) | n/a | Controlled light, plain white bg | Phone | Match training distribution |

More: [awesome-lego-machine-learning](https://github.com/360er0/awesome-lego-machine-learning). Lighting: [Roboflow](https://blog.roboflow.com/machine-vision-lighting-types/), [industrial guide](https://detectdefects.com/blog/2026/03/22/machine-vision-lighting-complete-guide/) — glossy ABS → diffuse/dome; cross-pol for leftover glare.

### Key takeaways

1. **Enclosure beats software** — fixed geometry + light that overwhelms ambient.
2. **Plain matte floor** — never identify on the chain belt.
3. **Overhead + 45°** (or camera + mirror) before exotic arrays.
4. **Pipelining** — Brickognize (~0.7 s) overlaps arm return; trapdoor waits on `arm_home ∧ id_done`.
5. **Backlight on the fixed floor** → silhouette presence; retire empty-ref blob hacks.
6. **Global shutter optional** — parts are settled on the stage.

## Design

### Geometry

- **Box:** ~250–300 mm cube under the belt tip; matte white interior; conveyor inlet chute; service lid.
- **Scan floor:** matte light-gray (or translucent for backlight), sized for largest part + margin; **not** the chain.
- **Trapdoor:** **servo tipping stage** (see below); short guided drop into scoop.
- **Scoop clearance:** teach home under the tip **exit edge**; clear of hinge/servo.
- **Views:** overhead primary + 45° secondary (or 45° mirror into one frame). Cameras fixed to the **box**.

### Construction

| Option | Cost | Notes |
|---|---|---|
| Foam board / MDF box | $15-25 | **v1 recommendation** |
| Photo tent cut to fit tip | $15-30 | Fast light, floppy mounts |
| 2020 extrusion + panels | $40-60 | Best if iterating camera rails |

Cameras bolted to the box; lock focus after calibration.

### Lighting

- 24 V **COB CRI 90+** in diffused channels (bounce off white walls) and/or an **overhead ring** aimed at the stage.
- ~50 mm LED→diffuser spacing if direct; bounce relaxes this.
- Cross-pol film + CPL if glossy glare remains.
- **Backlight (later):** LEDs under opal acrylic scan floor → silhouette occupancy.
- Power: shared 24 V with conveyor (2–3 A for lights).

### Cameras

Manual focus, lockable exposure/WB, ≥1080p, ~150–250 mm working distance.

| Option | Price | Verdict |
|---|---|---|
| **ELP 1080p USB + M12** ×2 | ~$25-35 ea | **Recommended** for overhead + 45° |
| Webcam ×1 | $0 | Phase 1 (lighting only) |
| Mirror instead of 2nd cam | ~$10 | BrickSortingMachine path |
| Global shutter ELP/Arducam | $50-90 | Only if we ever shoot moving parts |

Capture stills sequentially (MJPEG); no need to stream both at full rate.

### Trapdoor mechanism (chosen) — tipping stage

**Simplest reliable dump:** a continuous matte platform on a hinge, tipped by **one hobby servo** after ID. No hole, no sliding rails, no seam for a 1×1 to bridge.

```
  [cams looking down]
       │
  ┌────┴────┐   flat = scan floor (Phase 1: lock flat / disable servo)
  │  matte  │
  └────┬────┘
       │ servo tips ~50–70° after id_done ∧ arm_home
       ▼
   guides → scoop @ home
```

| Piece | Spec |
|---|---|
| Stage | Printed or acrylic, matte light-gray top (slight tooth so plates don't stick) |
| Actuator | One SG90 / MG90S (~$5); metal-gear if it gets abused |
| Motion | Tip toward scoop; foam bump at end of stroke; optional short “kick” pulse |
| Guides | Short side walls / chute so the part falls into the scoop, not beside it |
| Scoop home | Under the **exit edge**, clear of the hinge and servo horn |

**Why not a bomb-bay flap or sliding drawer for v1:** flaps leave a seam/jam lip and swing into the scoop envelope; drawers need rails that bind. Tip only if sticky parts refuse to leave — then try a steeper angle or switch to a sliding floor.

**Phase 1:** build the stage **locked flat** (servo unplugged or horn removed) and clear parts by hand after ID. **Phase 3:** enable tip + interlock.

### Interlock

| Signal | Meaning |
|---|---|
| `stage_occupied` | Vision (or IR) — part settled on floor |
| `id_done` | Brickognize (or HSV) returned a routing decision |
| `arm_home` | Scoop level under the tip exit |
| `stage_tip` | Only if `id_done ∧ arm_home`; else wait |
| `stage_clear` | After tip + return flat; allow next belt advance |

Sequence: flat → occupy → settle → ID (arm may return in parallel) → tip → ~300–500 ms → flat → confirm empty → next part.  
Jams: timeout → return flat, flag clear needed, **do not** advance belt.

### Software (`src/orchestrator` + Gradio)

- Split today's "landing zone in tray" into **stage ROI** (occupancy + capture) vs **scoop** (robot only).
- Parallel: start Brickognize when settled; arm may still be returning from previous dump.
- Multi-view: crop top + 45° (or mirror halves) → vote / best confidence; keep 2 s retry.
- Locked UVC exposure/WB inside the box.
- Failure gallery per view.

## Phased plan with gates

| Phase | Scope | Budget | Gate |
|---|---|---|---|
| **0. Baseline** | Open-air 100-part match/retry gallery | $0 | Numbers recorded |
| **1. Box + lit stage** | Foam/MDF box at belt tip, **matte tipping floor locked flat**, COB/ring, 1 webcam; hand-clear after ID | ~$50-70 | Match rate up; AE drift gone |
| **2. Two-view** | Overhead + 45° (ELP×2 or cam+mirror) + vote | ~$10-70 | No-match < 5% |
| **3. Tip + pipeline** | Enable servo tip; interlock `id_done ∧ arm_home`; scoop = catcher | ~$10-25 | Sustained run; arm returns during scan |
| **4. Backlight** | Opal floor + under-LEDs; silhouette presence | ~$25 | False stops/clears gone |
| **Fallback A** | If trapdoor unreliable: scan in scoop under a home light box | — | Only if Phase 3 fails Gate G3-style jam budget |

Total Phases 1–3: **roughly $85–180**.

## Watch list

- [Daniel West](https://www.youtube.com/watch?v=04JkdHEX3Yk) · [AI explainer](https://www.youtube.com/watch?v=-UGl0ZOCgwQ)
- [BrickSortingMachine](https://www.youtube.com/@BrickSortingMachine) / [scanner plans](https://bricksortingmachine.com/sorting-machine-diy-build-instructions-scanner)
- [basically Sorter V2](https://basically.website/sorter-v2) — Brickognize + chamber; March 2026 footage
- [OpenBlok](https://github.com/blokbot-io/OpenBlok) · [HackMakeMod backlight](https://hackmakemod.com/blogs/projects/parts-counter-blob-counter)
- [awesome-lego-machine-learning](https://github.com/360er0/awesome-lego-machine-learning)

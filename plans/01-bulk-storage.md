---
component: 1 — Bulk storage
parent: ../PLAN.md
status: pending
role: "Hold the unsorted pile and meter a controlled trickle of parts into Part Separation."
---

# C1 — Bulk storage

> Part of the [LEGO Sorter master plan](../PLAN.md). Feeds into [Component 2 — Part Separation](02-part-separation.md).

## Job of this component

Take a dumped pile of mixed LEGO and release it into the machine **at a controlled, roughly constant rate** — without dumping the whole pile at once. This is the "metering" stage. It does NOT singulate (that's Component 2); it just trickles.

## Design options

| Approach | How it works | Effort | Notes |
|----------|--------------|--------|-------|
| **Gravity hopper (recommended v1)** | Funnel narrows to a slot; parts fall onto the feed belt by gravity | Low | Simplest; good enough for a 2–3 min demo |
| Belt-beneath-pile | Pile sits directly on a slow belt; belt drags a layer out | Medium | Used by Daniel West / BrickSortingMachine; smoother flow, more build |
| Step feeder | A vertical step rises through the pile, lifting a small portion per stroke | High | Akiyuki/Garcia design; excellent metering, most complex |

References: [BrickSortingMachine bulk storage](https://bricksortingmachine.com/diy-lego-sorting-machine), [sorter survey](https://bricksortingmachine.com/exploring-lego-sorting-machines-a-survey-of-designs).

## v1: Gravity hopper (POC)

Keep it dead simple — no active agitation in v1.

### Geometry rules

- Narrow to a slot **~1.5x the largest part width** (too wide = clumps fall through; too narrow = bridging/jams)
- Steep walls **> 45°** so PLA friction doesn't hold parts back
- Slot empties directly onto the slow feed belt of Component 2
- Capacity **~50–100 parts** is plenty for a demo run
- A **manual tap** to clear bridging is acceptable for the pitch

### Anti-jam (only if needed)

- Add a small vibration motor (offset-weight DC motor) on the hopper wall
- Or a printed "agitator finger" on a hobby servo that nudges the slot every few seconds
- Defer both unless the gravity chute proves unreliable in the 100-part test

## Buy vs print

- **Print:** [doppelgrau PnP `hopper.scad`](https://github.com/doppelgrau/PnP/tree/master/HeapFeeder) — parametric funnel; adapt slot width to LEGO.
- **Improvise:** a kitchen funnel / cut plastic bottle works for the very first bring-up.
- **Buy:** nothing LEGO-specific exists off-the-shelf; hopper is always DIY.

## Done criteria

- Releases parts onto the feed belt without dumping the whole pile
- No more than occasional manual taps during a 100-part run
- Hand-off point aligns with Component 2 feed belt inlet

# OpenSCAD concept model

Parametric **schematic** assembly of the LEGO sorter (same hybrid layout as the
[Three.js viewer](../)). Not fabrication CAD.

## Open

```bash
# Open GUI (macOS path on this machine)
"/Applications/OpenSCAD-2021.01.app/Contents/MacOS/OpenSCAD" docs/concept-model/scad/sorter.scad

# or from docs/concept-model/scad/
OpenSCAD sorter.scad   # if OpenSCAD is on PATH
```

In the GUI: **F5** preview (colors), **F6** render (for STL). Toggle modules via
booleans in [`config.scad`](config.scad). Set `tip_stage_mode` in
[`sorter.scad`](sorter.scad) to `0` (level), `1` (→ scoop), or `-1` (→ unidentified).

## Export PNG / STL (CLI)

From `docs/concept-model/scad/`:

```bash
OSC="/Applications/OpenSCAD-2021.01.app/Contents/MacOS/OpenSCAD"

# Colored preview image for docs
"$OSC" -o preview.png --imgsize=1920,1080 --preview --viewall --autocenter sorter.scad

# Mesh (loses color; useful for import into Blender / MeshLab)
"$OSC" -o sorter.stl sorter.scad
```

## Files

```
scad/
├── sorter.scad           # main assembly + layout
├── config.scad           # shared dims / module toggles
└── modules/
    ├── hopper.scad       # cascading rotary cylinders (basically V2 style)
    ├── conveyor.scad
    ├── chamber.scad
    ├── rebel.scad
    └── bins.scad
```

Dimensions track [`model.js`](../model.js) planning values (belt ~70×280 mm,
chamber ~260 mm). Hopper cascade and ReBeL pose are conceptual.

/**
 * Modular schematic geometry for the LEGO sorter concept model.
 *
 * Units: millimeters. Each subsystem is a named THREE.Group so a real GLB
 * can later replace any module without changing the viewer wiring.
 *
 * Known planning dims (belt, chamber) are approximate; robot / layout
 * spacing is conceptual until as-built measurements land.
 */

import * as THREE from "three";

/** @typedef {{ id: string, label: string, color: number, col?: number, level?: number }} BinSpec */

export const CONFIG = {
  units: "mm",
  // Scale reference: chamber is a true ~6" cube; hoppers are bulk-sized relative to it.
  belt: {
    width: 80,
    length: 280,
    height: 36,
    inclineDeg: 0, // deck runs flat; the drop chute provides the gravity handoff
    standH: 280, // deck elevation above the table, so the belt clears the light box
    cleatPitch: 40,
    cleatHeight: 7,
  },
  chamber: {
    size: 152, // 6 inches
    wall: 5,
    height: 152,
  },
  // basically V2-scale bowls (~12–14" class) vs 6" light box
  hopper: {
    stages: [
      { radius: 180, wallH: 160, floorConeH: 0, fill: true },
      { radius: 150, wallH: 70, floorConeH: 55, fill: false },
      { radius: 130, wallH: 60, floorConeH: 48, fill: false },
    ],
    wallThickness: 6,
    cutoutDeg: 65,
    pillarH: 50,
  },
  // igus ReBeL 6DOF (datasheet): footprint Ø180, reach max 664, nominal 400
  // https://www.igus.eu/product/REBEL-6DOF-03 — Robolink ReBeL DINA5 tech data
  rebel: {
    baseDia: 180,
    baseH: 155,
    shoulderRise: 45,
    upperArm: 255, // schematic link lengths (not exact CAD)
    forearm: 270,
    wrist: 95,
    reachMax: 664,
    nominalReach: 400,
    jointR: [55, 42, 36, 28],
  },
  // Vertical stacks in an angular arc (col = around arm, level = stacked up)
  bins: /** @type {(BinSpec & { col: number, level: number })[]} */ ([
    { id: "red", label: "red", color: 0xc62828, col: 0, level: 0 },
    { id: "blue", label: "blue", color: 0x1565c0, col: 1, level: 0 },
    { id: "yellow", label: "yellow", color: 0xf9a825, col: 2, level: 0 },
    { id: "green", label: "green", color: 0x2e7d32, col: 3, level: 0 },
    { id: "set_a", label: "set A", color: 0x6a1b9a, col: 0, level: 1 },
    { id: "set_b", label: "set B", color: 0x00838f, col: 1, level: 1 },
    { id: "plates", label: "plates", color: 0xef6c00, col: 2, level: 1 },
    { id: "technic", label: "technic", color: 0x546e7a, col: 3, level: 1 },
    { id: "unknown", label: "unknown", color: 0x78909c, col: 0, level: 2 },
    { id: "misc", label: "misc", color: 0x5d4037, col: 1, level: 2 },
    { id: "overflow", label: "overflow", color: 0x455a64, col: 2, level: 2 },
    { id: "reject", label: "reject", color: 0x37474f, col: 3, level: 2 },
  ]),
  binStack: {
    radius: 360, // within max reach; larger bins need more clearance
    startDeg: -48,
    endDeg: 48,
    cols: 4,
    levels: 3,
    // Sized for ReBeL dump (arm base ~155 mm; bins should feel substantial)
    binW: 140,
    binH: 130,
    binD: 160,
    shelfGap: 16,
  },
};

const mat = {
  frame: () =>
    new THREE.MeshStandardMaterial({
      color: 0x2a2d34,
      roughness: 0.55,
      metalness: 0.15,
    }),
  plastic: (color, extras = {}) =>
    new THREE.MeshStandardMaterial({
      color,
      roughness: 0.7,
      metalness: 0.05,
      ...extras,
    }),
  matte: (color) =>
    new THREE.MeshStandardMaterial({
      color,
      roughness: 0.9,
      metalness: 0.0,
    }),
  metal: (color = 0xb0b8c0) =>
    new THREE.MeshStandardMaterial({
      color,
      roughness: 0.35,
      metalness: 0.65,
    }),
  glass: (color = 0xdce8f5) =>
    new THREE.MeshStandardMaterial({
      color,
      roughness: 0.2,
      metalness: 0.0,
      transparent: true,
      opacity: 0.22,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
};

function box(w, h, d, material, opts = {}) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  if (opts.position) mesh.position.copy(opts.position);
  if (opts.rotation) mesh.rotation.copy(opts.rotation);
  return mesh;
}

function cyl(radiusTop, radiusBottom, height, material, opts = {}) {
  const mesh = new THREE.Mesh(
    new THREE.CylinderGeometry(radiusTop, radiusBottom, height, opts.segments ?? 24),
    material,
  );
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  if (opts.position) mesh.position.copy(opts.position);
  if (opts.rotation) mesh.rotation.copy(opts.rotation);
  return mesh;
}

function makeTable() {
  const g = new THREE.Group();
  g.name = "table";
  const top = box(1600, 22, 1300, mat.matte(0xe8e4dc));
  top.position.set(120, -11, 280);
  g.add(top);
  return g;
}

/**
 * One basically-style rotary bowl: black wall with exit cutout, white conical
 * floor, center cap + radial guide, side NEMA drive, optional brick pile.
 * @param {{ radius: number, wallH: number, floorConeH: number, fill?: boolean }} spec
 * @param {number} exitAngleRad world yaw of the exit cutout
 */
function makeRotaryBowl(spec, exitAngleRad = 0, legH = CONFIG.hopper.pillarH) {
  const bowl = new THREE.Group();
  bowl.name = "rotaryBowl";
  const { radius, wallH, floorConeH } = spec;
  const t = CONFIG.hopper.wallThickness;
  const cutout = THREE.MathUtils.degToRad(CONFIG.hopper.cutoutDeg);
  const wallMat = mat.matte(0x1a1a1a);
  const floorMat = mat.matte(0xf2f2f2);

  // Outer wall as a lathe-ish ring with a missing exit sector (many boxes).
  const segs = 36;
  const span = Math.PI * 2 - cutout;
  for (let i = 0; i < segs; i++) {
    const a0 = -span / 2 + (i / segs) * span;
    const a1 = -span / 2 + ((i + 1) / segs) * span;
    const mid = (a0 + a1) / 2;
    const arc = a1 - a0;
    const chord = 2 * radius * Math.sin(arc / 2);
    const panel = box(t, wallH, Math.max(chord, 2), wallMat);
    panel.position.set(Math.cos(mid) * radius, wallH / 2, Math.sin(mid) * radius);
    panel.rotation.y = -mid;
    bowl.add(panel);
  }

  // Rim lip
  const rimSegs = 40;
  for (let i = 0; i < rimSegs; i++) {
    const a0 = -span / 2 + (i / rimSegs) * span;
    const a1 = -span / 2 + ((i + 1) / rimSegs) * span;
    const mid = (a0 + a1) / 2;
    const chord = 2 * (radius + 2) * Math.sin((a1 - a0) / 2);
    const rim = box(8, 4, Math.max(chord, 2), wallMat);
    rim.position.set(Math.cos(mid) * (radius + 1), wallH + 1, Math.sin(mid) * (radius + 1));
    rim.rotation.y = -mid;
    bowl.add(rim);
  }

  // Rotating white cone floor: apex up in the center, parts ride outward to
  // the wall and leave through the cutout. Bucket stages (coneH 0) stay flat.
  const rotor = new THREE.Group();
  rotor.name = "bowlRotor";
  const isCone = floorConeH > 0;
  if (isCone) {
    const cone = cyl(5, radius - 4, floorConeH, floorMat, { segments: 56 });
    cone.position.y = floorConeH / 2 + 3;
    rotor.add(cone);
    // Radial print-line hints on the cone face
    for (let i = 0; i < 16; i++) {
      const a = (i / 16) * Math.PI * 2;
      const spoke = box(radius - 12, 0.8, 1.4, mat.matte(0xe4e4e4));
      spoke.position.set(
        (Math.cos(a) * (radius - 12)) / 2,
        floorConeH * 0.52,
        (Math.sin(a) * (radius - 12)) / 2,
      );
      spoke.rotation.y = -a;
      spoke.rotation.z = -Math.atan2(floorConeH, radius - 4);
      rotor.add(spoke);
    }
  } else {
    const flat = cyl(radius - 4, radius - 4, 5, floorMat, { segments: 48 });
    flat.position.y = 4;
    rotor.add(flat);
  }
  bowl.add(rotor);

  const capY = isCone ? floorConeH + 8 : wallH * 0.75;

  // Center light cap (black dome over the cone apex)
  bowl.add(
    cyl(20, 9, 14, mat.matte(0x111111), {
      position: new THREE.Vector3(0, capY, 0),
    }),
  );
  const glow = new THREE.PointLight(0xfff5e0, 0.5, radius * 2.4, 2);
  glow.position.set(0, capY - 6, 0);
  bowl.add(glow);

  // Stationary radial exit guide (local +X = cutout center)
  const guide = box(radius - 10, 9, 7, wallMat);
  guide.position.set((radius - 10) / 2 + 8, capY - 2, 0);
  guide.rotation.z = isCone ? Math.atan2(floorConeH * 0.8, radius) : 0;
  bowl.add(guide);
  // Small fiducial plate near the rim
  bowl.add(
    box(13, 13, 1.5, mat.matte(0xffffff), {
      position: new THREE.Vector3(radius - 14, wallH + 8, 0),
    }),
  );
  bowl.add(
    box(6, 6, 1.6, mat.matte(0x111111), {
      position: new THREE.Vector3(radius - 14, wallH + 8, 0.2),
    }),
  );

  // Side-mounted NEMA drive
  const motor = new THREE.Group();
  motor.name = "bowlMotor";
  motor.add(
    box(36, 36, 36, mat.plastic(0x101010), {
      position: new THREE.Vector3(0, 22, radius + 28),
    }),
  );
  motor.add(
    cyl(8, 8, 10, mat.metal(0xcccccc), {
      position: new THREE.Vector3(0, 22, radius + 8),
      rotation: new THREE.Euler(Math.PI / 2, 0, 0),
    }),
  );
  // Blue gear hint under floor
  motor.add(
    cyl(18, 18, 6, mat.plastic(0x1e88e5), {
      position: new THREE.Vector3(0, 4, 0),
    }),
  );
  bowl.add(motor);

  // Support leg down to the table
  if (legH > 0) {
    bowl.add(
      box(26, legH, 18, mat.matte(0x1d1d1d), {
        position: new THREE.Vector3(0, -legH / 2, 0),
      }),
    );
    bowl.add(
      box(70, 6, 60, mat.matte(0x1d1d1d), {
        position: new THREE.Vector3(0, -legH + 3, 0),
      }),
    );
  }

  if (spec.fill) {
    const pile = new THREE.Group();
    pile.name = "hopperPile";
    const colors = [0xc62828, 0x1565c0, 0xf9a825, 0x2e7d32, 0xffffff, 0x37474f, 0xff9800];
    for (let i = 0; i < 40; i++) {
      const brick = box(
        14 + (i % 3) * 5,
        6 + (i % 2) * 4,
        10 + (i % 4) * 3,
        mat.plastic(colors[i % colors.length]),
      );
      const ang = (i / 40) * Math.PI * 1.8 - 0.5;
      const rr = radius * (0.15 + (i % 6) * 0.1);
      brick.position.set(
        Math.cos(ang) * rr,
        18 + (i % 6) * 9,
        Math.sin(ang) * rr,
      );
      brick.rotation.y = ang + i * 0.3;
      pile.add(brick);
    }
    bowl.add(pile);
  }

  // Local +X is exit; yaw whole bowl so cutout faces exitAngleRad
  bowl.rotation.y = exitAngleRad;
  return bowl;
}

function makeHopper() {
  const g = new THREE.Group();
  g.name = "hopper";
  const stages = CONFIG.hopper.stages;
  const rotors = [];

  // Compact triangular cascade packed in Z so the three support posts
  // form a tight cluster instead of a long line. Exits still overhang.
  const placements = [
    { x: 40, y: 520, z: 120, exit: -Math.PI / 2 }, // high / +Z
    { x: 50, y: 415, z: -130, exit: 0.62 }, // mid / −Z (under first)
    { x: 230, y: 310, z: 10, exit: 0.0 }, // low / toward belt
  ];

  stages.forEach((spec, i) => {
    const p = placements[i] ?? placements[placements.length - 1];
    const bowl = makeRotaryBowl(spec, p.exit, p.y);
    bowl.position.set(p.x, p.y, p.z);
    bowl.name = `rotaryBowl_${i}`;
    const rotor = bowl.getObjectByName("bowlRotor");
    if (rotor) rotors.push(rotor);
    g.add(bowl);
  });

  // Shared deck under the clustered posts — sells the tight array footprint
  g.add(box(220, 8, 320, mat.matte(0x2a2a2a), { position: new THREE.Vector3(80, 4, 0) }));
  g.add(box(200, 6, 300, mat.matte(0x1a1a1a), { position: new THREE.Vector3(80, 9, 0) }));

  // Short bridge from the last bowl onto the elevated belt
  g.add(
    box(70, 6, CONFIG.belt.width + 12, mat.matte(0x2b2b2b), {
      position: new THREE.Vector3(330, 285, 10),
      rotation: new THREE.Euler(0, 0, -0.3),
    }),
  );

  g.userData.bowlRotors = rotors;
  return g;
}

function makeConveyor() {
  const g = new THREE.Group();
  g.name = "conveyor";
  const { width, length, height, inclineDeg, cleatPitch, cleatHeight, standH } = CONFIG.belt;

  const frame = box(length + 20, height, width + 16, mat.frame());
  frame.position.y = height / 2;
  g.add(frame);

  const railMat = mat.plastic(0x1b1e24);
  for (const z of [-(width / 2 + 6), width / 2 + 6]) {
    g.add(box(length + 24, 14, 8, railMat, { position: new THREE.Vector3(0, height - 2, z) }));
  }

  const slatMat = mat.matte(0xf5f5f5);
  const slatCount = Math.floor(length / 12);
  for (let i = 0; i < slatCount; i++) {
    const slat = box(10, 4, width - 4, slatMat);
    slat.position.set(-length / 2 + 8 + i * 12, height + 2, 0);
    g.add(slat);
  }

  for (let x = -length / 2 + 30; x < length / 2 - 20; x += cleatPitch) {
    g.add(
      box(4, cleatHeight, width - 10, mat.plastic(0xe0e0e0), {
        position: new THREE.Vector3(x, height + 2 + cleatHeight / 2, 0),
      }),
    );
  }

  const motor = new THREE.Group();
  motor.name = "nema17";
  motor.add(
    box(42, 42, 42, mat.plastic(0x111111), {
      position: new THREE.Vector3(length / 2 - 24, height / 2, width / 2 + 32),
    }),
  );
  motor.add(
    cyl(11, 11, 8, mat.metal(0xcccccc), {
      position: new THREE.Vector3(length / 2 - 24, height / 2, width / 2 + 10),
      rotation: new THREE.Euler(Math.PI / 2, 0, 0),
    }),
  );
  g.add(motor);

  // Support bents: paired legs each side, joined by a head beam and mid brace,
  // tied together lengthwise and cross-braced. Legs run to the table surface.
  const legMat = mat.frame();
  const braceMat = mat.plastic(0x546e7a);
  const footMat = mat.plastic(0x37474f);
  const legZ = width / 2 + 10;
  const bentX = [-length / 2 + 45, length / 2 - 45];

  for (const x of bentX) {
    for (const z of [-legZ, legZ]) {
      g.add(box(20, standH, 20, legMat, { position: new THREE.Vector3(x, -standH / 2, z) }));
      g.add(box(46, 8, 46, footMat, { position: new THREE.Vector3(x, -standH + 4, z) }));
    }
    // Head beam under the deck + mid-height cross brace
    g.add(box(20, 14, legZ * 2 + 20, legMat, { position: new THREE.Vector3(x, -10, 0) }));
    g.add(box(12, 10, legZ * 2, braceMat, { position: new THREE.Vector3(x, -standH * 0.55, 0) }));
  }

  // Lengthwise stringers tying the bents together
  const span = bentX[1] - bentX[0];
  for (const z of [-legZ, legZ]) {
    g.add(box(span, 12, 12, braceMat, { position: new THREE.Vector3(0, -standH * 0.55, z) }));
  }

  // Diagonal knee braces for a believable stand
  const diagLen = Math.hypot(span, standH * 0.45);
  const diagAngle = Math.atan2(standH * 0.45, span);
  for (const z of [-legZ, legZ]) {
    for (const dir of [1, -1]) {
      g.add(
        box(diagLen, 9, 9, braceMat, {
          position: new THREE.Vector3(0, -standH * 0.32, z),
          rotation: new THREE.Euler(0, 0, dir * diagAngle),
        }),
      );
    }
  }

  const onBelt = new THREE.Group();
  onBelt.name = "beltParts";
  const partColors = [0xc62828, 0x1565c0, 0xf9a825];
  for (let i = 0; i < 3; i++) {
    const p = box(16, 8, 10, mat.plastic(partColors[i]));
    p.position.set(-length / 2 + 80 + i * 70, height + 10, (i - 1) * 8);
    onBelt.add(p);
  }
  g.add(onBelt);

  g.userData.inclineDeg = inclineDeg;
  if (inclineDeg) g.rotation.z = THREE.MathUtils.degToRad(inclineDeg);
  return g;
}

function makeScanChamber() {
  const g = new THREE.Group();
  g.name = "scanChamber";
  const { size, wall, height } = CONFIG.chamber;
  const half = size / 2;
  const boxH = height;
  const wallMat = mat.plastic(0xf7f7f7);
  const glassMat = mat.glass(0xcfd8dc);
  const exitH = 48; // open lower band on ±Z for tilt-plate exits
  const upperH = boxH - exitH;

  // Outer shell — walls meet at corners; open feed notch on −X (no mid ledge)
  const slotW = 56;
  const notchW = CONFIG.belt.width + 16;
  const sideStrip = (size - notchW) / 2;

  // +X full wall
  g.add(box(wall, boxH, size, wallMat, { position: new THREE.Vector3(half - wall / 2, boxH / 2, 0) }));
  // −X: only side pillars — leave the belt-width notch open top to bottom of the shell
  g.add(box(wall, boxH, sideStrip, wallMat, { position: new THREE.Vector3(-half + wall / 2, boxH / 2, -(notchW / 2 + sideStrip / 2)) }));
  g.add(box(wall, boxH, sideStrip, wallMat, { position: new THREE.Vector3(-half + wall / 2, boxH / 2, notchW / 2 + sideStrip / 2) }));
  // Low sill under the notch (below the tilt plate) so the face still reads closed
  g.add(box(wall, 18, notchW, wallMat, { position: new THREE.Vector3(-half + wall / 2, 9, 0) }));

  // ±Z faces: full-width solid panels + lower sill rails (open exit band between)
  g.add(
    box(size, upperH, wall, wallMat, {
      position: new THREE.Vector3(0, exitH + upperH / 2, half - wall / 2),
    }),
  );
  // Small viewing window on the scoop face (keeps the shell reading solid)
  g.add(
    box(size - 50, upperH - 50, wall + 1, glassMat, {
      position: new THREE.Vector3(0, exitH + upperH / 2, half - wall / 2 + 0.5),
    }),
  );
  g.add(
    box(size, upperH, wall, wallMat, {
      position: new THREE.Vector3(0, exitH + upperH / 2, -half + wall / 2),
    }),
  );
  // Lower sill rails so the open exits still read as framed openings
  g.add(box(size, 10, wall, wallMat, { position: new THREE.Vector3(0, 5, half - wall / 2) }));
  g.add(box(size, 10, wall, wallMat, { position: new THREE.Vector3(0, 5, -half + wall / 2) }));

  // Lid: solid over most of the box, open rectangle above the feed notch
  const lidMainW = size - slotW;
  g.add(
    box(lidMainW, wall, size, wallMat, {
      position: new THREE.Vector3(half - lidMainW / 2, boxH - wall / 2, 0),
    }),
  );
  const stripD = sideStrip;
  for (const zSign of [-1, 1]) {
    g.add(
      box(slotW, wall, stripD, wallMat, {
        position: new THREE.Vector3(-half + slotW / 2, boxH - wall / 2, zSign * (notchW / 2 + stripD / 2)),
      }),
    );
  }

  // Gravity drop chute: short open trough ending at the −X notch
  const chute = new THREE.Group();
  chute.name = "feedDropChute";
  const chuteW = notchW - 14;
  // Deck continuation off the belt
  chute.add(
    box(40, 3, chuteW, mat.matte(0x455a64), {
      position: new THREE.Vector3(-half - 42, boxH + 36, 0),
    }),
  );
  // Steep slide into the box
  chute.add(
    box(36, 3, chuteW, mat.matte(0x546e7a), {
      position: new THREE.Vector3(-half - 12, boxH + 18, 0),
      rotation: new THREE.Euler(0, 0, -0.85),
    }),
  );
  for (const zSign of [-1, 1]) {
    chute.add(
      box(52, 14, 3, mat.matte(0x37474f), {
        position: new THREE.Vector3(-half - 28, boxH + 30, zSign * (chuteW / 2 + 1.5)),
        rotation: new THREE.Euler(0, 0, -0.45),
      }),
    );
  }
  g.add(chute);

  // Center-pivoting tilt plate: +X tip → scoop (+Z), −X tip → reject (−Z)
  // (rotation about local X in setStageTip)
  const stage = new THREE.Group();
  stage.name = "tippingStage";
  const floorW = size - 2 * wall - 12;
  const floorD = size - 2 * wall - 8;
  const pivot = new THREE.Group();
  pivot.name = "tippingPivot";
  pivot.add(box(floorW, 4, floorD, mat.matte(0xd0d0d0), { position: new THREE.Vector3(0, 2, 0) }));
  pivot.add(box(floorW, 8, 3, mat.plastic(0x90a4ae), { position: new THREE.Vector3(0, 7, -floorD / 2 + 2) }));
  pivot.add(box(floorW, 8, 3, mat.plastic(0x90a4ae), { position: new THREE.Vector3(0, 7, floorD / 2 - 2) }));
  pivot.add(
    cyl(4, 4, floorW + 8, mat.metal(0x607d8b), {
      position: new THREE.Vector3(0, 2, 0),
      rotation: new THREE.Euler(0, 0, Math.PI / 2),
    }),
  );
  stage.add(pivot);
  stage.position.set(0, exitH - 6, 0);
  g.add(stage);
  g.userData.tippingPivot = pivot;

  // Exit lips outside the open bands
  g.add(
    box(floorW - 8, 3, 30, mat.matte(0xb0bec5), {
      position: new THREE.Vector3(0, exitH - 14, half + 12),
      rotation: new THREE.Euler(0.4, 0, 0),
    }),
  );
  g.add(
    box(floorW - 8, 3, 32, mat.matte(0x90a4ae), {
      position: new THREE.Vector3(0, exitH - 12, -half - 12),
      rotation: new THREE.Euler(-0.28, 0, 0),
    }),
  );

  // Unidentified-parts bin behind the light box (−Z)
  const reject = new THREE.Group();
  reject.name = "rejectBin";
  reject.add(box(110, 4, 90, mat.matte(0x455a64), { position: new THREE.Vector3(0, 2, 0) }));
  reject.add(box(4, 55, 90, mat.matte(0x546e7a), { position: new THREE.Vector3(-53, 28, 0) }));
  reject.add(box(4, 55, 90, mat.matte(0x546e7a), { position: new THREE.Vector3(53, 28, 0) }));
  reject.add(box(110, 55, 4, mat.matte(0x546e7a), { position: new THREE.Vector3(0, 28, -43) }));
  reject.add(box(110, 12, 4, mat.plastic(0xff8f00), { position: new THREE.Vector3(0, 52, 43) }));
  reject.position.set(0, -20, -half - 70);
  g.add(reject);

  // Cameras / LEDs tucked under the lid
  const camBody = (pos, rot) => {
    const c = new THREE.Group();
    c.add(box(22, 14, 18, mat.plastic(0x212121)));
    c.add(
      cyl(5, 7, 10, mat.metal(0x9e9e9e), {
        position: new THREE.Vector3(0, -8, 0),
        rotation: new THREE.Euler(Math.PI / 2, 0, 0),
      }),
    );
    c.position.copy(pos);
    c.rotation.copy(rot);
    return c;
  };
  const cams = new THREE.Group();
  cams.name = "cameras";
  cams.add(camBody(new THREE.Vector3(18, boxH - 28, 0), new THREE.Euler(0, 0, 0)));
  cams.add(camBody(new THREE.Vector3(25, boxH - 50, half - 28), new THREE.Euler(-Math.PI / 4, 0, 0)));
  g.add(cams);

  const ledMat = mat.plastic(0xfff8e1, { emissive: 0xfff3c4, emissiveIntensity: 0.7 });
  for (const z of [-half + 18, half - 18]) {
    g.add(box(size - 70, 5, 6, ledMat, { position: new THREE.Vector3(20, boxH - 22, z) }));
  }

  // Elevated stand
  g.add(box(size + 20, 8, size + 40, mat.plastic(0x455a64), { position: new THREE.Vector3(0, -4, -8) }));
  for (const x of [-half + 10, half - 10]) {
    for (const z of [-half + 10, half - 10]) {
      g.add(box(18, 80, 18, mat.plastic(0x37474f), { position: new THREE.Vector3(x, -44, z) }));
    }
  }

  const stagePart = box(12, 7, 9, mat.plastic(0x1565c0));
  stagePart.name = "stagePart";
  stagePart.position.set(0, exitH + 8, 0);
  g.add(stagePart);

  return g;
}

function makeLink(length, radius, material) {
  const link = new THREE.Group();
  link.add(
    cyl(radius, radius, length, material, {
      position: new THREE.Vector3(0, length / 2, 0),
    }),
  );
  link.add(
    cyl(radius * 1.2, radius * 1.2, radius * 1.5, mat.metal(0x78909c), {
      position: new THREE.Vector3(0, length, 0),
    }),
  );
  return link;
}

function makeRebel() {
  const g = new THREE.Group();
  g.name = "rebel";
  const R = CONFIG.rebel;
  // igus standard look: dark base + light / orange accents
  const body = mat.plastic(0xf5f5f5);
  const accent = mat.plastic(0xff6a00);
  const dark = mat.plastic(0x1a1a1a);

  // Base footprint Ø180 with integrated control housing
  g.add(cyl(R.baseDia / 2, R.baseDia / 2, R.baseH, dark, { position: new THREE.Vector3(0, R.baseH / 2, 0) }));
  g.add(
    cyl(R.baseDia / 2 - 8, R.baseDia / 2 - 14, 28, accent, {
      position: new THREE.Vector3(0, R.baseH - 10, 0),
    }),
  );

  // Compact ReBeL-like working pose: shoulder rises, elbow folds back down,
  // then the wrist points toward the light box. The link chain uses local +Y;
  // with rebel.rotation.y = +π/2, forward motion maps to world −Z.
  const j1 = new THREE.Group();
  j1.name = "j1";
  j1.position.set(0, R.baseH, 0);
  j1.add(
    cyl(R.jointR[0], R.jointR[0], R.shoulderRise, body, {
      position: new THREE.Vector3(0, R.shoulderRise / 2, 0),
    }),
  );

  const j2 = new THREE.Group();
  j2.name = "j2";
  j2.position.set(0, R.shoulderRise, 0);
  j2.rotation.z = -1.05; // shoulder lower so scoop sits under the exit lip
  j2.add(makeLink(R.upperArm, R.jointR[1], body));
  j2.add(
    cyl(R.jointR[1] + 4, R.jointR[1] + 4, 30, accent, {
      position: new THREE.Vector3(0, 0, 0),
    }),
  );

  const j3 = new THREE.Group();
  j3.name = "j3";
  j3.position.set(0, R.upperArm, 0);
  j3.rotation.z = -1.9; // elbow — scoop floor ~60 mm, rim below the lip
  j3.add(makeLink(R.forearm, R.jointR[2], body));

  const j4 = new THREE.Group();
  j4.name = "j4";
  j4.position.set(0, R.forearm, 0);
  // Keep the scoop level and open-up; total pitch = −π/2.
  j4.rotation.z = 1.05 + 1.9 - Math.PI / 2;
  j4.add(makeLink(R.wrist, R.jointR[3], body));

  // Flange at the distal face; scoop cantilevered OUT along tool +Y (not back along the link).
  const tip = new THREE.Group();
  tip.name = "toolTip";
  tip.position.set(0, R.wrist, 0);

  tip.add(box(40, 14, 40, dark, { position: new THREE.Vector3(0, 7, 0) }));

  // Short neck so the tray clearly leaves the flange instead of reading as
  // lying on the last link.
  tip.add(
    cyl(11, 11, 22, accent, {
      position: new THREE.Vector3(0, 22, 0),
    }),
  );

  // Tray frame: length along +Y (out from flange). Open toward −X (= world up
  // when the wrist is level). Catching pose under the light-box trapdoor.
  const scoop = new THREE.Group();
  scoop.name = "scoop";
  const floorMat = mat.matte(0xb0bec5);
  const wallMatS = mat.matte(0x90a4ae);
  const scoopLen = 150;
  const scoopW = 100;
  const wallH = 44;
  const y0 = 36; // past flange + neck
  scoop.add(
    box(3, scoopLen, scoopW, floorMat, {
      position: new THREE.Vector3(0, y0 + scoopLen / 2, 0),
    }),
  );
  scoop.add(
    box(wallH, 4, scoopW, wallMatS, {
      position: new THREE.Vector3(-wallH / 2, y0 + 2, 0),
    }),
  ); // back wall near neck
  scoop.add(
    box(wallH, scoopLen, 4, wallMatS, {
      position: new THREE.Vector3(-wallH / 2, y0 + scoopLen / 2, -scoopW / 2),
    }),
  );
  scoop.add(
    box(wallH, scoopLen, 4, wallMatS, {
      position: new THREE.Vector3(-wallH / 2, y0 + scoopLen / 2, scoopW / 2),
    }),
  );
  tip.add(scoop);
  j4.add(tip);

  j3.add(j4);
  j2.add(j3);
  j1.add(j2);
  g.add(j1);

  // Reach envelope ring (nominal 400 mm) — subtle floor mark for scale
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(R.nominalReach, 1.5, 8, 64),
    new THREE.MeshStandardMaterial({
      color: 0xff6a00,
      transparent: true,
      opacity: 0.25,
      roughness: 1,
    }),
  );
  ring.rotation.x = Math.PI / 2;
  ring.position.y = 1;
  ring.name = "nominalReachRing";
  g.add(ring);

  return g;
}

function makeBins() {
  const g = new THREE.Group();
  g.name = "bins";
  const { radius, startDeg, endDeg, cols, binW, binH, binD, shelfGap } = CONFIG.binStack;

  // Vertical shelves: one angular column, bins stacked up by level
  const maxCol = Math.max(...CONFIG.bins.map((b) => b.col ?? 0), cols - 1);
  const maxLevel = Math.max(...CONFIG.bins.map((b) => b.level ?? 0));

  for (let c = 0; c <= maxCol; c++) {
    const t = maxCol === 0 ? 0.5 : c / maxCol;
    const deg = startDeg + t * (endDeg - startDeg);
    const rad = THREE.MathUtils.degToRad(deg);

    // Column frame / upright
    const stackH = (maxLevel + 1) * (binH + shelfGap) + 20;
    const upright = box(8, stackH, 8, mat.plastic(0x37474f));
    upright.position.set(
      Math.sin(rad) * (radius - binD / 2 - 15),
      stackH / 2,
      Math.cos(rad) * (radius - binD / 2 - 15),
    );
    g.add(upright);
  }

  for (const spec of CONFIG.bins) {
    const col = spec.col ?? 0;
    const level = spec.level ?? 0;
    const t = maxCol === 0 ? 0.5 : col / maxCol;
    const deg = startDeg + t * (endDeg - startDeg);
    const rad = THREE.MathUtils.degToRad(deg);
    const y = level * (binH + shelfGap);

    const bin = new THREE.Group();
    bin.name = `bin_${spec.id}`;
    const shell = box(binW, binH, binD, mat.plastic(spec.color));
    shell.position.y = binH / 2;
    bin.add(shell);
    bin.add(
      box(binW - 8, binH - 8, binD - 8, mat.matte(0x263238), {
        position: new THREE.Vector3(0, binH / 2 + 4, 0),
      }),
    );
    bin.add(
      box(binW - 14, 9, 2, mat.plastic(0xffffff), {
        position: new THREE.Vector3(0, binH - 7, binD / 2 + 1),
      }),
    );
    // Shelf plate under each bin
    bin.add(
      box(binW + 8, 4, binD + 10, mat.matte(0x455a64), {
        position: new THREE.Vector3(0, -2, 0),
      }),
    );

    bin.position.set(Math.sin(rad) * radius, y, Math.cos(rad) * radius);
    bin.rotation.y = rad + Math.PI;
    bin.userData.binId = spec.id;
    bin.userData.label = spec.label;
    bin.userData.col = col;
    bin.userData.level = level;
    g.add(bin);
  }

  return g;
}

function makeFlowMarkers() {
  const g = new THREE.Group();
  g.name = "flowMarkers";

  const path = new THREE.CatmullRomCurve3([
    new THREE.Vector3(-410, 580, 100),
    new THREE.Vector3(-400, 470, -110),
    new THREE.Vector3(-220, 360, 10),
    new THREE.Vector3(-80, 310, 10),
    new THREE.Vector3(60, 300, 10),
    new THREE.Vector3(160, 295, 0),
    new THREE.Vector3(260, 285, 0),
    new THREE.Vector3(310, 200, 0),
    new THREE.Vector3(320, 140, 20),
    new THREE.Vector3(320, 100, 90),
    new THREE.Vector3(340, 70, 180),
    new THREE.Vector3(360, 90, 280),
  ]);

  const tube = new THREE.Mesh(
    new THREE.TubeGeometry(path, 64, 3.5, 8, false),
    new THREE.MeshStandardMaterial({
      color: 0xff7043,
      emissive: 0xff5722,
      emissiveIntensity: 0.35,
      roughness: 0.4,
      transparent: true,
      opacity: 0.85,
    }),
  );
  tube.name = "flowTube";
  g.add(tube);

  const beads = new THREE.Group();
  beads.name = "flowBeads";
  const beadMat = mat.plastic(0xffab91, { emissive: 0xff6e40, emissiveIntensity: 0.5 });
  for (let i = 0; i < 6; i++) {
    const bead = new THREE.Mesh(new THREE.SphereGeometry(8, 16, 16), beadMat);
    bead.castShadow = true;
    beads.add(bead);
  }
  g.add(beads);
  g.userData.flowPath = path;

  return g;
}

/**
 * Build the full machine scene graph.
 * Layout: hoppers → flat feed belt → small elevated light box → scoop waiting
 * in front of the tilt-plate exit → bins in an arc around the arm.
 * Unidentified parts tip the other way into a reject bin behind the box.
 */
export function buildMachine() {
  const root = new THREE.Group();
  root.name = "machineRoot";

  const table = makeTable();
  const hopper = makeHopper();
  const conveyor = makeConveyor();
  const scanChamber = makeScanChamber();
  const rebel = makeRebel();
  const bins = makeBins();
  const flow = makeFlowMarkers();

  // Elevated feed line: hoppers + belt sit above the light box so parts drop in
  hopper.position.set(-450, 0, 0);
  conveyor.position.set(60, CONFIG.belt.standH, 0);
  scanChamber.position.set(320, 90, 0);
  // Scoop waits below the +Z tilt-plate exit so parts drop in by gravity
  rebel.position.set(320, 0, 500);
  rebel.rotation.y = Math.PI / 2;
  bins.position.set(320, 0, 500);

  root.add(table, hopper, conveyor, scanChamber, rebel, bins, flow);

  const tippingStage = /** @type {THREE.Group} */ (scanChamber.getObjectByName("tippingStage"));

  const labelAnchors = [
    { id: "hopper", label: "C1 Rotary hoppers", position: new THREE.Vector3(-400, 680, 40) },
    { id: "conveyor", label: "C2 Flat feed belt", position: new THREE.Vector3(60, 360, 60) },
    { id: "scanChamber", label: "C3 Light box (6\")", position: new THREE.Vector3(320, 280, 0) },
    { id: "reject", label: "Unidentified bin", position: new THREE.Vector3(320, 80, -180) },
    { id: "rebel", label: "C4 ReBeL (Ø180 / 664 mm)", position: new THREE.Vector3(320, 400, 500) },
    { id: "bins", label: "Bins (stacked)", position: new THREE.Vector3(320, 320, 860) },
  ];

  return {
    root,
    modules: {
      table,
      hopper,
      conveyor,
      scanChamber,
      rebel,
      bins,
      flowMarkers: flow,
    },
    tippingStage,
    flowPath: flow.userData.flowPath,
    flowBeads: /** @type {THREE.Group} */ (flow.getObjectByName("flowBeads")),
    bowlRotors: /** @type {THREE.Object3D[]} */ (hopper.userData.bowlRotors ?? []),
    labelAnchors,
  };
}

/**
 * Set the tilt-plate pose.
 * - "level": flat for imaging
 * - "scoop": tip toward +Z so the part slides forward into the ReBeL scoop
 * - "reject": tip toward −Z into the unidentified-parts bin
 * @param {THREE.Group} stage tippingStage group (contains tippingPivot)
 * @param {"level"|"scoop"|"reject"} mode
 * @param {number} [amount=0.55] tip angle in radians
 */
export function setStageTip(stage, mode, amount = 0.55) {
  if (!stage) return;
  const pivot = stage.getObjectByName("tippingPivot") ?? stage;
  // Rotate about local X so the plate tips in ±Z
  if (mode === "scoop") pivot.rotation.x = amount;
  else if (mode === "reject") pivot.rotation.x = -amount;
  else pivot.rotation.x = 0;
  pivot.rotation.z = 0;
}

/** @deprecated use setStageTip */
export function setStageOpen(stage, open, amount = 0.55) {
  setStageTip(stage, open ? "scoop" : "level", amount);
}

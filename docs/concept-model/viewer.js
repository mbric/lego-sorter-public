/**
 * Documentation-oriented Three.js viewer for the LEGO sorter concept model.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { buildMachine, setStageTip } from "./model.js";

const EXPORT_W = 1920;
const EXPORT_H = 1080;

const canvas = /** @type {HTMLCanvasElement} */ (document.getElementById("viewport"));
const hud = document.getElementById("hud");
const labelLayer = document.getElementById("labels");

/** Show an in-page message when WebGL is unavailable, instead of a blank canvas. */
function showFatal(message) {
  const wrap = canvas.parentElement;
  if (!wrap || wrap.querySelector(".fatal")) return;
  const el = document.createElement("div");
  el.className = "fatal";
  el.innerHTML =
    `<strong>3D view unavailable</strong><span>${message}</span>` +
    `<span>Try a normal browser tab (Chrome/Safari) at this same URL, or reload after closing other WebGL pages.</span>`;
  wrap.appendChild(el);
}

let renderer;
try {
  renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    preserveDrawingBuffer: true,
  });
} catch (err) {
  showFatal(String(err?.message ?? err));
  throw err;
}

canvas.addEventListener("webglcontextlost", (e) => {
  e.preventDefault();
  showFatal("The WebGL context was lost.");
});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xd7e0ea);
scene.fog = new THREE.Fog(0xd7e0ea, 1800, 3200);

const camera = new THREE.PerspectiveCamera(42, 1, 1, 8000);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.target.set(100, 140, 140);
controls.maxPolarAngle = Math.PI * 0.49;
controls.minDistance = 200;
controls.maxDistance = 3200;

const machine = buildMachine();
scene.add(machine.root);

// Lights
scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const hemi = new THREE.HemisphereLight(0xf0f5ff, 0xb0a090, 0.45);
scene.add(hemi);

const key = new THREE.DirectionalLight(0xffffff, 1.05);
key.position.set(400, 700, 350);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
key.shadow.camera.near = 50;
key.shadow.camera.far = 2000;
key.shadow.camera.left = -800;
key.shadow.camera.right = 800;
key.shadow.camera.top = 800;
key.shadow.camera.bottom = -800;
scene.add(key);

const fill = new THREE.DirectionalLight(0xb3e5fc, 0.35);
fill.position.set(-300, 250, -200);
scene.add(fill);

const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(3000, 3000),
  new THREE.MeshStandardMaterial({ color: 0xc5ced8, roughness: 1, metalness: 0 }),
);
ground.rotation.x = -Math.PI / 2;
ground.position.y = -0.5;
ground.receiveShadow = true;
scene.add(ground);

/** @type {Record<string, { pos: THREE.Vector3, target: THREE.Vector3 }>} */
const PRESETS = {
  iso: {
    pos: new THREE.Vector3(1000, 720, 1000),
    target: new THREE.Vector3(100, 140, 140),
  },
  front: {
    pos: new THREE.Vector3(100, 320, 1400),
    target: new THREE.Vector3(100, 140, 100),
  },
  top: {
    pos: new THREE.Vector3(100, 1500, 140),
    target: new THREE.Vector3(100, 0, 140),
  },
};

function applyPreset(name) {
  const p = PRESETS[name] ?? PRESETS.iso;
  camera.position.copy(p.pos);
  controls.target.copy(p.target);
  controls.update();
}

applyPreset("iso");

// HTML labels
/** @type {Map<string, HTMLElement>} */
const labelEls = new Map();

function buildLabels() {
  labelLayer.replaceChildren();
  labelEls.clear();
  for (const anchor of machine.labelAnchors) {
    const el = document.createElement("div");
    el.className = "label";
    el.dataset.id = anchor.id;
    el.textContent = anchor.label;
    labelLayer.appendChild(el);
    labelEls.set(anchor.id, el);
  }
}

buildLabels();

const state = {
  showLabels: true,
  animateFlow: true,
  stageTip: /** @type {"level"|"scoop"|"reject"} */ ("level"),
  flowT: 0,
};

function resize() {
  const wrap = canvas.parentElement;
  const w = wrap?.clientWidth ?? window.innerWidth;
  const h = wrap?.clientHeight ?? window.innerHeight;
  camera.aspect = w / Math.max(h, 1);
  camera.updateProjectionMatrix();
  renderer.setSize(w, h, false);
}

window.addEventListener("resize", resize);
resize();

function updateLabels() {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  for (const anchor of machine.labelAnchors) {
    const el = labelEls.get(anchor.id);
    if (!el) continue;
    if (!state.showLabels) {
      el.style.display = "none";
      continue;
    }
    const v = anchor.position.clone().project(camera);
    const behind = v.z > 1;
    const x = (v.x * 0.5 + 0.5) * w;
    const y = (-v.y * 0.5 + 0.5) * h;
    el.style.display = behind ? "none" : "block";
    el.style.transform = `translate(-50%, -100%) translate(${x}px, ${y}px)`;
  }
}

function setModuleVisible(name, visible) {
  const mod = machine.modules[name];
  if (mod) mod.visible = visible;
}

function downloadPng() {
  const prevW = canvas.width;
  const prevH = canvas.height;
  const prevAspect = camera.aspect;

  renderer.setPixelRatio(1);
  renderer.setSize(EXPORT_W, EXPORT_H, false);
  camera.aspect = EXPORT_W / EXPORT_H;
  camera.updateProjectionMatrix();
  // Hide UI labels for clean export; draw once
  const prevLabels = state.showLabels;
  state.showLabels = false;
  updateLabels();
  renderer.render(scene, camera);

  const url = canvas.toDataURL("image/png");
  const a = document.createElement("a");
  a.href = url;
  a.download = "lego-sorter-concept.png";
  a.click();

  state.showLabels = prevLabels;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  resize();
  camera.aspect = prevAspect;
  camera.updateProjectionMatrix();
  // restore canvas buffer size via resize; silence unused
  void prevW;
  void prevH;
}

function wireControls() {
  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyPreset(btn.getAttribute("data-preset") ?? "iso");
      document.querySelectorAll("[data-preset]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
    });
  });
  document.querySelector('[data-preset="iso"]')?.classList.add("active");

  const labelsToggle = /** @type {HTMLInputElement} */ (document.getElementById("toggle-labels"));
  labelsToggle?.addEventListener("change", () => {
    state.showLabels = labelsToggle.checked;
  });

  const flowToggle = /** @type {HTMLInputElement} */ (document.getElementById("toggle-flow"));
  flowToggle?.addEventListener("change", () => {
    state.animateFlow = flowToggle.checked;
    machine.modules.flowMarkers.visible = flowToggle.checked;
  });

  const stageSelect = /** @type {HTMLSelectElement} */ (document.getElementById("stage-tip"));
  stageSelect?.addEventListener("change", () => {
    const mode = /** @type {"level"|"scoop"|"reject"} */ (stageSelect.value);
    state.stageTip = mode;
    setStageTip(machine.tippingStage, mode);
  });

  document.querySelectorAll("[data-module]").forEach((input) => {
    const el = /** @type {HTMLInputElement} */ (input);
    el.addEventListener("change", () => {
      setModuleVisible(el.getAttribute("data-module") ?? "", el.checked);
    });
  });

  document.getElementById("btn-export")?.addEventListener("click", downloadPng);
}

wireControls();

const clock = new THREE.Clock();

function animate() {
  requestAnimationFrame(animate);
  const dt = clock.getDelta();
  controls.update();

  if (state.animateFlow && machine.flowBeads && machine.flowPath) {
    state.flowT = (state.flowT + dt * 0.12) % 1;
    const n = machine.flowBeads.children.length;
    machine.flowBeads.children.forEach((bead, i) => {
      const t = (state.flowT + i / n) % 1;
      machine.flowPath.getPointAt(t, bead.position);
    });
  }

  // Slow rotary-bowl floors (basically V2 schematic motion)
  if (machine.bowlRotors?.length) {
    for (const rotor of machine.bowlRotors) {
      rotor.rotation.y += dt * 0.45;
    }
  }

  updateLabels();
  renderer.render(scene, camera);

  if (hud) {
    hud.textContent = `${canvas.clientWidth}×${canvas.clientHeight} · orbit drag · scroll zoom`;
  }
}

animate();

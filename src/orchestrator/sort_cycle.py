"""Vision-triggered conveyor feed loop: detect → STOP → capture → optional match → wait clear."""

from __future__ import annotations

import concurrent.futures
import enum
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from feeder.http_client import ConveyorClient, ConveyorError
from orchestrator.routing import virtual_bin

try:
    from scipy import ndimage as _ndimage
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    _ndimage = None

logger = logging.getLogger(__name__)

# Project-root paths (cwd-independent).
_ROOT = Path(__file__).resolve().parents[2]
COMPARE_SIZE = (160, 120)  # (width, height) for local occupancy
# Padding around the largest blob when building the Brickognize crop.
API_CROP_PAD_FRAC = 0.25
MIN_API_CROP_SIDE = 32  # pixels; fall back to landing ROI if smaller
# Capture quality gates (reject → re-settle; after too many rejects → resume).
MULTI_BLOB_MIN_FRAC = 0.35  # 2nd blob ≥ this × largest → "multiple parts"
MULTI_BLOB_MIN_PIXELS = 12  # absolute floor for a competing blob
# Compare-space gap: shadow / split silhouettes usually touch the part bbox
# (gap 0–2). Two real parts sit much farther apart.
MULTI_BLOB_NEAR_GAP_PX = 2
BLOB_EDGE_MARGIN_PX = 1  # ROI-space margin: touching edge ⇒ part still entering
# Padded crop vs landing ROI. Real parts are usually well under this; full-zone
# fallback / multi-part floods land near 100%+.
MAX_CROP_ROI_AREA_FRAC = 0.85
MAX_CROP_ASPECT = 4.0  # reject ultra-wide/tall strips (e.g. 5:1 leaf edge-on)
CAPTURE_GATE_MAX_REJECTS = 4  # then resume belt instead of settling forever
# Edge-touch below this occupancy is feeder fringe / noise — never edge-override
# capture (that was shooting the left housing at ~1% occ).
MIN_EDGE_OVERRIDE_OCC = 2.5
# Occupancy = largest connected blob of changed pixels as % of landing ROI.
# Total changed-% confuses empty-frame speckles (2–4%) with small bricks (1–2%);
# a real part is one coherent blob, noise is many tiny ones.
PIXEL_DIFF_THR = 16.0  # lower catches dark/black bricks on light trays
DEFAULT_OCCUPIED_THRESHOLD = 0.8  # blob % of ROI to STOP
DEFAULT_EMPTY_THRESHOLD = 0.35  # blob % — clear when largest blob is tiny
DEFAULT_OCCUPIED_FRAMES = 2
DEFAULT_EMPTY_FRAMES = 3
DEFAULT_SETTLE_S = 0.5
DEFAULT_MOTION_THRESHOLD = 8.0
# After capturing empty reference, sample N frames to learn residual blob noise.
NOISE_CAL_FRAMES = 10
EMPTY_NOISE_MARGIN = 0.25  # add on top of measured noise floor for WaitClear
# Soft re-baseline of empty ref while advancing with a clear tray (lighting drift).
EMPTY_REF_EMA = 0.04
EMPTY_REF_EMA_MIN_STREAK = 6
# WaitClear: webcam AE often shifts the whole tray after removing a part.
# Widespread pixel change (not a compact brick) → pull empty ref toward live.
LIGHTING_CHANGED_PCT = 22.0
LIGHTING_BLOB_PCT = 6.0
WAIT_CLEAR_LIGHTING_EMA = 0.20
# Mild adapt when WaitClear looks "almost empty" (residual AE / shadows).
WAIT_CLEAR_RESIDUAL_EMA = 0.12
# Removal detection: compare the live frame to the frozen capture instead of the
# (possibly stale) empty reference. Once the scene no longer matches the captured
# part and has gone still, the operator has cleared the tray — resume regardless
# of what the drifted empty reference thinks.
WAIT_CLEAR_STABLE_S = 0.6  # frame must be still this long before trusting removal
REMOVAL_MATCH_FRAC = 0.45  # live must differ from capture by ≥ this × capture blob
# Absolute floor for "looks different from capture" — keep low so tiny parts still
# count as removed when they leave (their whole blob may only be ~1%).
REMOVAL_MIN_PCT = 0.5
# If occupancy drops well below the WaitClear peak while the scene is still
# AND the live frame no longer matches the frozen capture, treat as cleared.
PEAK_DROP_FRAC = 0.55
# Soft-pull empty ref toward live only after removal evidence (never while a
# small sitting part still matches the capture — that bakes it into the ref).
WAIT_CLEAR_STUCK_EMA = 0.22
# Soft-pull ceiling after removal evidence. AE blotches often land ~15–25%.
WAIT_CLEAR_STUCK_MAX_PCT = 28.0
# Failed shape match: the piece may still have been rolling at capture time.
# Re-settle, wait, take a fresh still, and try again before giving up.
MATCH_RETRY_DELAY_S = 2.0
MATCH_MAX_RETRIES = 1
# No Brickognize match + weak occupancy → treat as empty/noise (not WaitClear).
# Corner AE/shadow often lands in the ~2–6% band and wedges the loop otherwise.
GHOST_NO_MATCH_MAX_OCC = 12.0
# How hard to blend a ghost frame into the empty reference before resuming.
GHOST_EMPTY_REF_EMA = 0.55
# API-crop content gate (structure). Soft AE blotches can raise luma std without
# edges; flat trays have neither. Parts usually have both edge energy and lap var.
CROP_MIN_EDGE_MEAN = 2.0  # mean |∇| on luma
CROP_MIN_LAP_VAR = 10.0  # variance of discrete Laplacian
CROP_MIN_STRONG_EDGE_FRAC = 0.015  # fraction of pixels with |∇| ≥ strong thr
CROP_STRONG_EDGE_THR = 8.0
# Min blob pixels (absolute) so a 1-pixel speck never counts as a part.
MIN_BLOB_PIXELS = 8
# Landing / capture zone as fractions of the frame (0–1).
DEFAULT_ROI_TOP = 0.40
DEFAULT_ROI_BOTTOM = 0.92
DEFAULT_ROI_LEFT = 0.15
DEFAULT_ROI_RIGHT = 0.85
REFERENCE_PATH = _ROOT / "data" / "reference" / "empty_tray.jpg"
ROI_CONFIG_PATH = _ROOT / "data" / "reference" / "landing_zone.json"
CAPTURES_DIR = _ROOT / "data" / "captures"


@dataclass
class IdentifyOutcome:
    """Result of one Brickognize (or other) identify call for the sort loop."""

    candidate: object | None = None
    color: object | None = None
    error: str | None = None
    match_panel: str | None = None
    alt_html: str | None = None
    status: str | None = None
    preview: np.ndarray | None = None
    api_calls: int = 0


IdentifyFn = Callable[[np.ndarray], IdentifyOutcome]
# event name + optional key/value fields (reason, occ, paths, …).
EventFn = Callable[[str, dict], None]


class SortState(enum.Enum):
    IDLE = "Idle"
    ADVANCING = "Advancing"
    STOPPING = "Stopping"
    SETTLING = "Settling"
    CAPTURED = "Captured"
    MATCHING = "Matching"
    WAIT_CLEAR = "WaitClear"


@dataclass
class SortCycleUpdate:
    state: SortState
    status: str
    virtual_bin: str = ""
    occupancy: float | None = None
    preview: np.ndarray | None = None
    captured: bool = False
    matched: bool = False
    identify: IdentifyOutcome | None = None
    error: str | None = None


def _normalize_frame(frame: np.ndarray) -> np.ndarray:
    """Ensure HxWx3 uint8 RGB (or HxW) for comparison / saving."""
    arr = np.asarray(frame)
    if arr.dtype != np.uint8:
        max_v = float(np.nanmax(arr)) if arr.size else 0.0
        if max_v <= 1.0 + 1e-6:
            arr = (np.clip(arr, 0, 1) * 255.0).astype(np.uint8)
        else:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
    if arr.ndim == 3 and arr.shape[2] >= 4:
        arr = arr[..., :3]
    return arr


def _to_gray_small(frame: np.ndarray) -> np.ndarray:
    frame = _normalize_frame(frame)
    if frame.ndim == 3:
        gray = np.mean(frame[..., :3].astype(np.float32), axis=2)
    else:
        gray = frame.astype(np.float32)
    pil = Image.fromarray(gray.astype(np.uint8))
    pil = pil.resize(COMPARE_SIZE, Image.Resampling.BILINEAR)
    return np.asarray(pil, dtype=np.float32)


@dataclass(frozen=True)
class LandingRoi:
    """Detect zone as fractions of frame height/width (0–1)."""

    top: float = DEFAULT_ROI_TOP
    bottom: float = DEFAULT_ROI_BOTTOM
    left: float = DEFAULT_ROI_LEFT
    right: float = DEFAULT_ROI_RIGHT

    def clamped(self) -> LandingRoi:
        top = float(np.clip(self.top, 0.0, 0.95))
        bottom = float(np.clip(self.bottom, top + 0.05, 1.0))
        left = float(np.clip(self.left, 0.0, 0.95))
        right = float(np.clip(self.right, left + 0.05, 1.0))
        return LandingRoi(top=top, bottom=bottom, left=left, right=right)

    def as_dict(self) -> dict[str, float]:
        c = self.clamped()
        return {
            "top": c.top,
            "bottom": c.bottom,
            "left": c.left,
            "right": c.right,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> LandingRoi:
        return cls(
            top=float(raw.get("top", DEFAULT_ROI_TOP)),
            bottom=float(raw.get("bottom", DEFAULT_ROI_BOTTOM)),
            left=float(raw.get("left", DEFAULT_ROI_LEFT)),
            right=float(raw.get("right", DEFAULT_ROI_RIGHT)),
        ).clamped()


def load_landing_roi() -> LandingRoi:
    """Load last-used landing zone, or built-in defaults."""
    if not ROI_CONFIG_PATH.is_file():
        return LandingRoi()
    try:
        raw = json.loads(ROI_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return LandingRoi()
        return LandingRoi.from_dict(raw)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Could not load landing zone config: %s", exc)
        return LandingRoi()


def save_landing_roi(roi: LandingRoi, *, force: bool = False) -> Path | None:
    """Persist landing zone for the next app session.

    Skips the write when the on-disk value is already identical (unless force).
    """
    roi = roi.clamped()
    payload = roi.as_dict()
    if not force and ROI_CONFIG_PATH.is_file():
        try:
            existing = json.loads(ROI_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing == payload:
                return ROI_CONFIG_PATH
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    ROI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ROI_CONFIG_PATH.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return ROI_CONFIG_PATH


def _roi_bounds_px(shape_hw: tuple[int, int], roi: LandingRoi) -> tuple[int, int, int, int]:
    """Pixel rect (x0, y0, x1, y1) for ``roi`` on an array with shape (h, w)."""
    h, w = shape_hw
    roi = roi.clamped()
    y0 = int(h * roi.top)
    y1 = max(y0 + 1, int(h * roi.bottom))
    x0 = int(w * roi.left)
    x1 = max(x0 + 1, int(w * roi.right))
    return x0, y0, x1, y1


def _crop_roi(gray: np.ndarray, roi: LandingRoi) -> np.ndarray:
    x0, y0, x1, y1 = _roi_bounds_px(gray.shape[:2], roi)
    return gray[y0:y1, x0:x1]


def _largest_blob_label(
    changed: np.ndarray,
) -> tuple[np.ndarray | None, int, int]:
    """Return (labels, best_label, best_size). labels is None when empty."""
    if _ndimage is not None:
        labels, n = _ndimage.label(changed)  # default structure = 4-connectivity
        if n == 0:
            return None, 0, 0
        counts = np.bincount(labels.ravel())
        best = int(counts[1:].argmax()) + 1
        return labels, best, int(counts[best])

    h, w = changed.shape
    labels = np.zeros((h, w), dtype=np.int32)
    best_label = 0
    best_size = 0
    next_label = 1
    ys, xs = np.where(changed)
    for y0, x0 in zip(ys.tolist(), xs.tolist(), strict=False):
        if labels[y0, x0] != 0:
            continue
        stack = [(y0, x0)]
        labels[y0, x0] = next_label
        size = 0
        while stack:
            cy, cx = stack.pop()
            size += 1
            for ny, nx in (
                (cy - 1, cx),
                (cy + 1, cx),
                (cy, cx - 1),
                (cy, cx + 1),
            ):
                if (
                    0 <= ny < h
                    and 0 <= nx < w
                    and changed[ny, nx]
                    and labels[ny, nx] == 0
                ):
                    labels[ny, nx] = next_label
                    stack.append((ny, nx))
        if size > best_size:
            best_size = size
            best_label = next_label
        next_label += 1
    if best_size == 0:
        return None, 0, 0
    return labels, best_label, best_size


def _largest_blob_pixels(changed: np.ndarray) -> int:
    """4-connected component size of the largest True region."""
    _, _, size = _largest_blob_label(changed)
    return size


def _largest_blob_bbox(changed: np.ndarray) -> tuple[int, int, int, int] | None:
    """Axis-aligned bbox (x0, y0, x1, y1) of the largest blob, or None if tiny."""
    labels, best, size = _largest_blob_label(changed)
    if labels is None or size < MIN_BLOB_PIXELS:
        return None
    ys, xs = np.where(labels == best)
    if ys.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _largest_blob_bbox_overlapping(
    changed: np.ndarray,
    *,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
) -> tuple[int, int, int, int] | None:
    """Blob with the most pixels *inside* ``[x0:x1, y0:y1]`` (compare-space).

    Prefers a part sitting in the landing zone over a large feeder/housing blob
    that only nicks the ROI edge.
    """
    labels, _best, size = _largest_blob_label(changed)
    if labels is None or size < MIN_BLOB_PIXELS:
        return None
    counts = np.bincount(labels.ravel())
    best_label = 0
    best_inside = 0
    for lab, count in enumerate(counts):
        if lab == 0 or count < MIN_BLOB_PIXELS:
            continue
        ys, xs = np.where(labels == lab)
        if ys.size == 0:
            continue
        bx0, by0 = int(xs.min()), int(ys.min())
        bx1, by1 = int(xs.max()) + 1, int(ys.max()) + 1
        if bx1 <= x0 or bx0 >= x1 or by1 <= y0 or by0 >= y1:
            continue
        inside = int(
            ((xs >= x0) & (xs < x1) & (ys >= y0) & (ys < y1)).sum()
        )
        if inside < MIN_BLOB_PIXELS:
            continue
        if inside > best_inside:
            best_inside = inside
            best_label = lab
    if best_label == 0:
        return None
    ys, xs = np.where(labels == best_label)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _blob_sizes(changed: np.ndarray) -> list[int]:
    """Connected-component sizes (descending), excluding background."""
    labels, _best, size = _largest_blob_label(changed)
    if labels is None or size <= 0:
        return []
    counts = np.bincount(labels.ravel())
    if counts.size <= 1:
        return []
    sizes = [int(c) for c in counts[1:] if c > 0]
    sizes.sort(reverse=True)
    return sizes


def _bbox_separation(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> int:
    """Min gap between two AABBs; 0 if they overlap or touch."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = max(0, max(ax0 - bx1, bx0 - ax1))
    dy = max(0, max(ay0 - by1, by0 - ay1))
    if dx == 0 and dy == 0:
        return 0
    if dx == 0:
        return dy
    if dy == 0:
        return dx
    return int(round(float(np.hypot(dx, dy))))


def _competing_blob_sizes(
    changed: np.ndarray,
    *,
    near_gap: int = MULTI_BLOB_NEAR_GAP_PX,
) -> list[int]:
    """Blob sizes after absorbing shadow-like neighbors into the largest.

    A hard shadow often labels as a second component but its bbox touches the
    part. Real second parts sit farther away in compare-space.
    """
    labels, best, size = _largest_blob_label(changed)
    if labels is None or size < MIN_BLOB_PIXELS:
        return []
    counts = np.bincount(labels.ravel())
    items: list[tuple[int, int, tuple[int, int, int, int]]] = []
    for lab, count in enumerate(counts):
        if lab == 0 or count < MIN_BLOB_PIXELS:
            continue
        ys, xs = np.where(labels == lab)
        if ys.size == 0:
            continue
        bb = (
            int(xs.min()),
            int(ys.min()),
            int(xs.max()) + 1,
            int(ys.max()) + 1,
        )
        items.append((lab, int(count), bb))
    if not items:
        return []
    items.sort(key=lambda t: t[1], reverse=True)
    _lab0, primary_size, primary_bb = items[0]
    competitors: list[int] = []
    for _lab, sz, bb in items[1:]:
        if _bbox_separation(primary_bb, bb) <= near_gap:
            primary_size += sz
            continue
        competitors.append(sz)
    return sorted([primary_size, *competitors], reverse=True)


def significant_secondary_blob(
    changed: np.ndarray,
    *,
    min_frac: float = MULTI_BLOB_MIN_FRAC,
    min_pixels: int = MULTI_BLOB_MIN_PIXELS,
) -> bool:
    """True when a second blob is large enough to be another part (not noise).

    Ignores components whose bbox is adjacent to the largest (typical part +
    cast shadow / split silhouette at compare resolution).
    """
    sizes = _competing_blob_sizes(changed)
    if len(sizes) < 2:
        return False
    largest, second = sizes[0], sizes[1]
    if largest < MIN_BLOB_PIXELS:
        return False
    return second >= min_pixels and second >= min_frac * largest


def blob_touches_roi_edge(
    bbox: tuple[int, int, int, int] | None,
    roi_shape: tuple[int, int],
    *,
    margin: int = BLOB_EDGE_MARGIN_PX,
) -> bool:
    """True when the blob bbox touches the landing-ROI border (still entering)."""
    if bbox is None:
        return False
    x0, y0, x1, y1 = bbox
    rh, rw = roi_shape
    if rh < 1 or rw < 1:
        return False
    m = max(0, int(margin))
    return (
        x0 <= m
        or y0 <= m
        or x1 >= rw - m
        or y1 >= rh - m
    )


def crop_geometry_reject_reason(
    crop_rect: tuple[int, int, int, int],
    roi_rect: tuple[int, int, int, int],
    *,
    max_area_frac: float = MAX_CROP_ROI_AREA_FRAC,
    max_aspect: float = MAX_CROP_ASPECT,
) -> str | None:
    """Return a reject reason when the API crop is too big or absurdly shaped."""
    x0, y0, x1, y1 = crop_rect
    rx0, ry0, rx1, ry1 = roi_rect
    cw, ch = max(1, x1 - x0), max(1, y1 - y0)
    rw, rh = max(1, rx1 - rx0), max(1, ry1 - ry0)
    area_frac = (cw * ch) / float(rw * rh)
    if area_frac > max_area_frac:
        return f"crop too large ({area_frac:.0%} of zone)"
    aspect = max(cw / ch, ch / cw)
    if aspect > max_aspect:
        return f"crop aspect {aspect:.1f}:1 (max {max_aspect:.1f})"
    return None


def _pad_rect(
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    pad_frac: float,
    frame_h: int,
    frame_w: int,
) -> tuple[int, int, int, int]:
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    pad_x = max(2, int(round(bw * pad_frac)))
    pad_y = max(2, int(round(bh * pad_frac)))
    return (
        max(0, x0 - pad_x),
        max(0, y0 - pad_y),
        min(frame_w, x1 + pad_x),
        min(frame_h, y1 + pad_y),
    )


def compute_blob_frame_rect(
    empty_ref: np.ndarray,
    gray: np.ndarray,
    roi: LandingRoi,
    frame_hw: tuple[int, int],
    *,
    pad_frac: float = 0.0,
    expand_outside_roi: bool = False,
) -> tuple[int, int, int, int] | None:
    """Largest-blob bbox in full-frame pixels, or None if no significant blob.

    ``empty_ref`` / ``gray`` are COMPARE_SIZE float grays. ``frame_hw`` is
    ``(height, width)`` of the source RGB frame.

    When ``expand_outside_roi`` is True, blob search uses the full compare frame
    and picks the largest component that still overlaps the landing ROI — so a
    part straddling the green zone edge is cropped whole, not ROI-clipped.
    """
    fh, fw = frame_hw
    if fh < 1 or fw < 1:
        return None
    cw, ch = COMPARE_SIZE  # width, height of compare gray
    rx0, ry0, rx1, ry1 = _roi_bounds_px((ch, cw), roi)

    if expand_outside_roi:
        matched = _match_exposure(empty_ref, gray)
        if matched.shape != empty_ref.shape:
            return None
        changed = np.abs(empty_ref - matched) >= PIXEL_DIFF_THR
        bbox = _largest_blob_bbox_overlapping(
            changed, x0=rx0, y0=ry0, x1=rx1, y1=ry1
        )
        if bbox is None:
            return None
        sx0, sy0, sx1, sy1 = bbox
    else:
        _, changed = _roi_diff_mask(empty_ref, gray, roi=roi)
        bbox_in_roi = _largest_blob_bbox(changed)
        if bbox_in_roi is None:
            return None
        bx0, by0, bx1, by1 = bbox_in_roi
        sx0, sy0 = rx0 + bx0, ry0 + by0
        sx1, sy1 = rx0 + bx1, ry0 + by1

    x0 = int(sx0 / cw * fw)
    y0 = int(sy0 / ch * fh)
    x1 = int(np.ceil(sx1 / cw * fw))
    y1 = int(np.ceil(sy1 / ch * fh))
    if pad_frac > 0:
        x0, y0, x1, y1 = _pad_rect(
            x0, y0, x1, y1, pad_frac=pad_frac, frame_h=fh, frame_w=fw
        )
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def crop_content_scores(crop: np.ndarray) -> tuple[float, float, float, float]:
    """Return (luma_std, edge_mean, laplacian_var, strong_edge_frac)."""
    rgb = _normalize_frame(crop)
    if rgb.size == 0:
        return 0.0, 0.0, 0.0, 0.0
    if rgb.ndim == 3:
        luma = np.mean(rgb[..., :3].astype(np.float32), axis=2)
    else:
        luma = rgb.astype(np.float32)
    if luma.size < 16 or luma.shape[0] < 3 or luma.shape[1] < 3:
        return 0.0, 0.0, 0.0, 0.0

    std = float(np.std(luma))
    gy = np.abs(np.diff(luma, axis=0))
    gx = np.abs(np.diff(luma, axis=1))
    # Align shapes for a per-pixel gradient magnitude on the shared (H-1)×(W-1).
    g = 0.5 * (gy[:, 1:] + gx[1:, :])
    edge = float(g.mean()) if g.size else 0.0
    strong_frac = (
        float((g >= CROP_STRONG_EDGE_THR).mean()) if g.size else 0.0
    )

    center = luma[1:-1, 1:-1]
    lap = (
        luma[:-2, 1:-1]
        + luma[2:, 1:-1]
        + luma[1:-1, :-2]
        + luma[1:-1, 2:]
        - 4.0 * center
    )
    lap_var = float(np.var(lap))
    return std, edge, lap_var, strong_frac


def crop_looks_empty(crop: np.ndarray) -> bool:
    """True when the API crop is basically a blank tray (no part structure).

    Soft AE / shadow blotches can raise luma std without sharp edges. A real
    part (or its silhouette against the tray) usually has edge energy + lap var.
    """
    _std, edge, lap_var, strong_frac = crop_content_scores(crop)
    return (
        edge < CROP_MIN_EDGE_MEAN
        and lap_var < CROP_MIN_LAP_VAR
        and strong_frac < CROP_MIN_STRONG_EDGE_FRAC
    )


def compute_padded_blob_crop(
    frame: np.ndarray,
    empty_ref: np.ndarray,
    gray: np.ndarray,
    roi: LandingRoi,
    *,
    pad_frac: float = API_CROP_PAD_FRAC,
    allow_roi_fallback: bool = True,
    expand_outside_roi: bool = False,
) -> tuple[np.ndarray | None, tuple[int, int, int, int] | None]:
    """Crop RGB around the largest changed blob (padded).

    ``empty_ref`` / ``gray`` are COMPARE_SIZE float grays. Returns
    ``(crop_rgb, (x0, y0, x1, y1))`` in full-frame pixels.

    When no usable blob exists, falls back to the landing ROI if
    ``allow_roi_fallback`` is True; otherwise returns ``(None, None)`` so the
    sort loop can reject the capture instead of uploading a huge empty zone.
    """
    frame = _normalize_frame(frame)
    fh, fw = frame.shape[:2]
    rect = compute_blob_frame_rect(
        empty_ref,
        gray,
        roi,
        (fh, fw),
        pad_frac=pad_frac,
        expand_outside_roi=expand_outside_roi,
    )
    if rect is None or (rect[2] - rect[0]) < MIN_API_CROP_SIDE or (
        rect[3] - rect[1]
    ) < MIN_API_CROP_SIDE:
        if not allow_roi_fallback:
            return None, None
        x0, y0, x1, y1 = _roi_bounds_px((fh, fw), roi)
    else:
        x0, y0, x1, y1 = rect

    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        if not allow_roi_fallback:
            return None, None
        return frame.copy(), (0, 0, fw, fh)
    return crop.copy(), (x0, y0, x1, y1)


def capture_reject_reason(
    empty_ref: np.ndarray,
    gray: np.ndarray,
    roi: LandingRoi,
    frame_hw: tuple[int, int],
    crop: np.ndarray | None,
    crop_rect: tuple[int, int, int, int] | None,
    *,
    allow_edge_contact: bool = False,
    skip_empty_crop: bool = False,
    skip_geometry: bool = False,
    allow_multi_part: bool = False,
) -> str | None:
    """Pre-Brickognize gates: multi-part, edge contact, geometry, empty crop.

    ``allow_edge_contact`` skips the edge-touch gate (used after max edge rejects
    so a parked part on the ROI border can still be photographed).
    """
    _, changed = _roi_diff_mask(empty_ref, gray, roi=roi)
    if not allow_multi_part and significant_secondary_blob(changed):
        sizes = _competing_blob_sizes(changed)
        second = sizes[1] if len(sizes) > 1 else 0
        return (
            f"multiple parts in zone "
            f"(blobs {sizes[0]}+{second} px)"
        )

    bbox = _largest_blob_bbox(changed)
    # Ignore edge contact when the "blob" already fills most of the zone
    # (exposure-flood / AE) — geometry/empty gates handle those.
    if not allow_edge_contact and bbox is not None:
        rh, rw = changed.shape
        box_area = max(1, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        fills_zone = box_area >= 0.45 * rh * rw
        if not fills_zone and blob_touches_roi_edge(bbox, changed.shape):
            return "part touching zone edge — waiting to center"

    if crop is None or crop_rect is None:
        return "no tight blob crop"

    if not skip_geometry:
        fh, fw = frame_hw
        roi_rect = _roi_bounds_px((fh, fw), roi)
        geom = crop_geometry_reject_reason(crop_rect, roi_rect)
        if geom:
            return geom

    if not skip_empty_crop and crop_looks_empty(crop):
        return "empty crop"

    return None


def _is_edge_touch_reject(reason: str) -> bool:
    return reason.startswith("part touching zone edge")


def _is_left_feeder_fringe_rect(
    rect: tuple[int, int, int, int] | None,
    frame_hw: tuple[int, int],
    *,
    roi: LandingRoi | None = None,
) -> bool:
    """True for a skinny blob glued to the left frame/ROI edge (feeder housing).

    The green zone often starts inset from the frame (``roi.left`` ~0.18), so the
    feeder tip shows up on the ROI's left border — not only at x≈0.
    """
    if rect is None:
        return False
    fh, fw = frame_hw
    if fh < 1 or fw < 1:
        return False
    x0, _y0, x1, _y1 = rect
    width = x1 - x0
    if width > int(0.22 * fw):
        return False
    # Absolute left frame edge (housing in the raw frame).
    if x0 <= max(4, int(0.06 * fw)):
        return True
    # Left edge of the landing ROI (housing jutting into the green zone).
    if roi is not None:
        rx0 = int(round(float(roi.left) * fw))
        if x0 <= rx0 + max(4, int(0.04 * fw)):
            return True
    return False


def _crop_from_frame_rect(
    frame: np.ndarray,
    rect: tuple[int, int, int, int],
    *,
    pad_frac: float = API_CROP_PAD_FRAC,
) -> tuple[np.ndarray | None, tuple[int, int, int, int] | None]:
    """Pad ``rect`` and slice RGB; returns ``(crop, padded_rect)``."""
    frame = _normalize_frame(frame)
    fh, fw = frame.shape[:2]
    x0, y0, x1, y1 = rect
    x0, y0, x1, y1 = _pad_rect(
        x0, y0, x1, y1, pad_frac=pad_frac, frame_h=fh, frame_w=fw
    )
    if x1 - x0 < MIN_API_CROP_SIDE or y1 - y0 < MIN_API_CROP_SIDE:
        return None, None
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return None, None
    return crop.copy(), (x0, y0, x1, y1)


def _match_exposure(ref: np.ndarray, live: np.ndarray) -> np.ndarray:
    """Gain-match live to ref mean so webcam AE does not look like occupancy."""
    ma = float(np.mean(ref))
    mb = float(np.mean(live))
    if mb < 1.0 or ma < 1.0:
        return live
    gain = float(np.clip(ma / mb, 0.55, 1.85))
    if abs(gain - 1.0) < 0.02:
        return live
    return np.clip(live * gain, 0.0, 255.0)


def _roi_diff_mask(
    empty_ref: np.ndarray,
    gray: np.ndarray,
    *,
    roi: LandingRoi,
    pixel_thr: float = PIXEL_DIFF_THR,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (ref_crop, boolean changed mask) after exposure match."""
    a = _crop_roi(empty_ref, roi)
    b = _crop_roi(gray, roi)
    if a.shape != b.shape or a.size == 0:
        a, b = empty_ref, gray
    b = _match_exposure(a, b)
    return a, np.abs(a - b) >= pixel_thr


@dataclass
class _DiffStats:
    occupancy: float  # largest-blob % of ROI
    changed_pct: float  # % of ROI pixels changed


def _diff_stats(
    empty_ref: np.ndarray,
    gray: np.ndarray,
    *,
    roi: LandingRoi,
    pixel_thr: float = PIXEL_DIFF_THR,
) -> _DiffStats:
    """One ROI crop/threshold/blob pass → occupancy + changed-%."""
    a, changed = _roi_diff_mask(empty_ref, gray, roi=roi, pixel_thr=pixel_thr)
    blob = _largest_blob_pixels(changed)
    occupancy = 0.0 if blob < MIN_BLOB_PIXELS else blob * 100.0 / a.size
    changed_pct = float(changed.mean() * 100.0) if changed.size else 0.0
    return _DiffStats(occupancy, changed_pct)


def _occupancy_percent(
    empty_ref: np.ndarray,
    gray: np.ndarray,
    *,
    roi: LandingRoi,
    pixel_thr: float = PIXEL_DIFF_THR,
) -> float:
    """Largest changed blob as % of landing ROI (ignores scattered noise)."""
    return _diff_stats(empty_ref, gray, roi=roi, pixel_thr=pixel_thr).occupancy


def _changed_pixel_percent(
    empty_ref: np.ndarray,
    gray: np.ndarray,
    *,
    roi: LandingRoi,
    pixel_thr: float = PIXEL_DIFF_THR,
) -> float:
    """% of landing-ROI pixels that differ (AE moves many; a brick moves few)."""
    return _diff_stats(empty_ref, gray, roi=roi, pixel_thr=pixel_thr).changed_pct


def _motion_score(
    prev_gray: np.ndarray,
    curr_gray: np.ndarray,
    *,
    roi: LandingRoi,
) -> float:
    """Mean abs diff on landing ROI (0–255) for settle detection."""
    a = _crop_roi(prev_gray, roi)
    b = _crop_roi(curr_gray, roi)
    if a.shape != b.shape or a.size == 0:
        a, b = prev_gray, curr_gray
    return float(np.mean(np.abs(a - b)))


_ZONE_COLOR = np.array([0, 220, 90], dtype=np.uint8)  # green landing ROI
_BLOB_COLOR = np.array([255, 200, 40], dtype=np.uint8)  # amber live blob


def _draw_box(
    out: np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    color: np.ndarray,
    thickness: int,
) -> None:
    h, w = out.shape[:2]
    x0, x1 = max(0, x0), min(w, x1)
    y0, y1 = max(0, y0), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return
    t = max(1, thickness)
    out[y0 : y0 + t, x0:x1] = color
    out[y1 - t : y1, x0:x1] = color
    out[y0:y1, x0 : x0 + t] = color
    out[y0:y1, x1 - t : x1] = color


def annotate_landing_roi(
    frame: np.ndarray,
    roi: LandingRoi,
    *,
    dim_outside: bool = True,
    blob_rect: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """Draw the landing/detect zone (green) and optional live blob outline (amber)."""
    out = _normalize_frame(frame).copy()
    roi = roi.clamped()
    h, w = out.shape[:2]
    y0 = int(h * roi.top)
    y1 = max(y0 + 1, int(h * roi.bottom))
    x0 = int(w * roi.left)
    x1 = max(x0 + 1, int(w * roi.right))

    if dim_outside:
        mask = np.ones((h, w), dtype=bool)
        mask[y0:y1, x0:x1] = False
        dimmed = (out.astype(np.float32) * 0.35).astype(np.uint8)
        out[mask] = dimmed[mask]

    thickness = max(3, min(h, w) // 160)
    _draw_box(out, x0, y0, x1, y1, color=_ZONE_COLOR, thickness=thickness)

    # Corner ticks for easier sizing.
    tick = max(8, thickness * 4)
    for yy, xx in ((y0, x0), (y0, x1), (y1, x0), (y1, x1)):
        out[max(0, yy - thickness) : min(h, yy + thickness), max(0, xx - tick) : min(w, xx + tick)] = _ZONE_COLOR
        out[max(0, yy - tick) : min(h, yy + tick), max(0, xx - thickness) : min(w, xx + thickness)] = _ZONE_COLOR

    if blob_rect is not None:
        bx0, by0, bx1, by1 = blob_rect
        blob_t = max(2, thickness - 1)
        _draw_box(out, bx0, by0, bx1, by1, color=_BLOB_COLOR, thickness=blob_t)
    return out


def annotate_capture_frame(
    frame: np.ndarray,
    roi: LandingRoi,
    *,
    dim_outside: bool = True,
    blob_rect: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """Annotate a frozen capture with the landing zone (call before/after bbox overlay)."""
    return annotate_landing_roi(
        frame, roi, dim_outside=dim_outside, blob_rect=blob_rect
    )


def _save_still(frame: np.ndarray) -> Path:
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = CAPTURES_DIR / f"still_{stamp}.jpg"
    Image.fromarray(_normalize_frame(frame)).convert("RGB").save(path, quality=92)
    return path


class SortCycle:
    """START → vision stop → freeze still → optional match → wait manual clear → repeat."""

    def __init__(
        self,
        feeder: ConveyorClient | None = None,
        *,
        identify_fn: IdentifyFn | None = None,
        event_fn: EventFn | None = None,
        occupied_threshold: float = DEFAULT_OCCUPIED_THRESHOLD,
        empty_threshold: float = DEFAULT_EMPTY_THRESHOLD,
        occupied_frames: int = DEFAULT_OCCUPIED_FRAMES,
        empty_frames: int = DEFAULT_EMPTY_FRAMES,
        settle_s: float = DEFAULT_SETTLE_S,
        motion_threshold: float = DEFAULT_MOTION_THRESHOLD,
        auto_match: bool = False,
    ) -> None:
        self.feeder = feeder or ConveyorClient()
        self.identify_fn = identify_fn
        self.event_fn = event_fn
        self.occupied_threshold = occupied_threshold
        self.empty_threshold = empty_threshold
        self.occupied_frames = occupied_frames
        self.empty_frames = empty_frames
        self.settle_s = settle_s
        self.motion_threshold = motion_threshold
        self.auto_match = auto_match
        self.roi = load_landing_roi()

        self._lock = threading.RLock()
        self.state = SortState.IDLE
        self.last_bin = ""
        self.last_still: np.ndarray | None = None
        self.last_still_path: Path | None = None
        # Padded blob (or ROI) crop sent to Brickognize; full still kept separately.
        self.last_api_crop: np.ndarray | None = None
        self.last_crop_rect: tuple[int, int, int, int] | None = None
        # Tight live blob outline in full-frame pixels (for feed preview).
        self.last_live_blob_rect: tuple[int, int, int, int] | None = None
        self._empty_ref: np.ndarray | None = None
        self._last_still_gray: np.ndarray | None = None
        self._capture_occupancy: float = 0.0
        self._wait_peak_occupancy: float = 0.0
        self._match_retries_left = 0
        self._retry_not_before = 0.0
        self._prev_gray: np.ndarray | None = None
        self._stable_since: float | None = None
        self._occupied_streak = 0
        self._empty_streak = 0
        self._pending_match = False
        self._noise_floor = 0.0
        self._noise_cal_remaining = 0
        self._clear_streak_advancing = 0
        self._match_future: concurrent.futures.Future[IdentifyOutcome] | None = None
        self._match_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sort-identify"
        )
        # True when last identify finished with no candidate (ghost / noise path).
        self._identify_failed = False
        # True after capture-gate give-up: WaitClear must see real empty occupancy
        # (no ghost shortcut — that was thrashing edge-stuck parts).
        self._capture_give_up_wait = False
        # Consecutive pre-Brickognize capture gate rejects while Settling.
        self._capture_gate_rejects = 0

    @property
    def active(self) -> bool:
        return self.state != SortState.IDLE

    @property
    def has_empty_reference(self) -> bool:
        return self._empty_ref is not None

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    def set_roi(self, roi: LandingRoi, *, persist: bool = True) -> None:
        self.roi = roi.clamped()
        if persist:
            try:
                save_landing_roi(self.roi)
            except OSError as exc:
                logger.warning("Could not save landing zone config: %s", exc)

    def set_empty_reference(self, frame: np.ndarray, *, save: bool = True) -> None:
        with self._lock:
            frame = _normalize_frame(frame)
            self._empty_ref = _to_gray_small(frame)
            self._noise_floor = 0.0
            self._noise_cal_remaining = NOISE_CAL_FRAMES
            self._clear_streak_advancing = 0
            if save:
                REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(frame).convert("RGB").save(REFERENCE_PATH)

    def load_empty_reference(self) -> bool:
        with self._lock:
            if not REFERENCE_PATH.is_file():
                return False
            image = Image.open(REFERENCE_PATH).convert("RGB")
            self._empty_ref = _to_gray_small(np.asarray(image))
            # Recalibrate noise against live frames when the loop/camera runs.
            self._noise_cal_remaining = max(self._noise_cal_remaining, NOISE_CAL_FRAMES)
            return True

    def effective_empty_threshold(self, user_thr: float) -> float:
        """Empty thr must sit above residual camera/lighting noise."""
        return max(float(user_thr), self._noise_floor + EMPTY_NOISE_MARGIN)

    def _set_live_blob_rect(
        self,
        frame: np.ndarray,
        gray: np.ndarray,
        empty_ref: np.ndarray,
        roi: LandingRoi,
    ) -> None:
        fh, fw = frame.shape[:2]
        # Expand outside ROI so the amber box matches edge-straddling parts
        # (same blob we crop after edge-override capture).
        self.last_live_blob_rect = compute_blob_frame_rect(
            empty_ref,
            gray,
            roi,
            (fh, fw),
            pad_frac=0.0,
            expand_outside_roi=True,
        )

    def observe_frame(self, frame: np.ndarray, roi: LandingRoi | None = None) -> float | None:
        """Update noise floor / empty EMA from a live frame (works while Idle too)."""
        with self._lock:
            if self._empty_ref is None or not isinstance(frame, np.ndarray):
                self.last_live_blob_rect = None
                return None
            use_roi = (roi or self.roi).clamped()
            gray = _to_gray_small(frame)
            stats = _diff_stats(self._empty_ref, gray, roi=use_roi)
            self._note_occupancy_sample(
                stats.occupancy, gray, changed_pct=stats.changed_pct
            )
            self._set_live_blob_rect(frame, gray, self._empty_ref, use_roi)
            return stats.occupancy

    def _note_occupancy_sample(
        self, occupied_score: float, gray: np.ndarray, *, changed_pct: float
    ) -> bool:
        """Learn empty-frame noise; soft-update empty ref while tray looks clear.

        Returns True when ``self._empty_ref`` was modified (caller should remeasure).
        """
        empty_thr = self.effective_empty_threshold(self.empty_threshold)
        # Count down calibration always; only raise the floor on empty-looking frames.
        if self._noise_cal_remaining > 0:
            if occupied_score <= empty_thr:
                self._noise_floor = max(self._noise_floor, occupied_score)
            self._noise_cal_remaining -= 1

        if occupied_score <= empty_thr:
            self._clear_streak_advancing += 1
        else:
            self._clear_streak_advancing = 0

        if self._empty_ref is None:
            return False

        # Lighting drift: slowly blend empty reference toward a clear live frame.
        if (
            self.state == SortState.ADVANCING
            and self._clear_streak_advancing >= EMPTY_REF_EMA_MIN_STREAK
            and occupied_score <= empty_thr
        ):
            a = EMPTY_REF_EMA
            self._empty_ref = (1.0 - a) * self._empty_ref + a * gray
            return True

        # After remove: AE leaves a sticky high occupancy vs the old empty ref.
        # Adapt only for widespread change (lighting) or below-stop residual —
        # never while a compact brick-sized blob still dominates.
        if self.state == SortState.WAIT_CLEAR:
            if (
                changed_pct >= LIGHTING_CHANGED_PCT
                and occupied_score >= LIGHTING_BLOB_PCT
            ):
                a = WAIT_CLEAR_LIGHTING_EMA
                self._empty_ref = (1.0 - a) * self._empty_ref + a * gray
                return True
            if (
                occupied_score > empty_thr
                and occupied_score < self.occupied_threshold
            ):
                a = WAIT_CLEAR_RESIDUAL_EMA
                self._empty_ref = (1.0 - a) * self._empty_ref + a * gray
                return True

        return False
    def force_clear(self, frame: np.ndarray | None = None) -> SortCycleUpdate:
        """Operator escape: re-baseline empty ref and resume if waiting to clear.

        Always writes a new empty-tray reference when a live frame is available,
        so Reset also refreshes the on-disk baseline.
        """
        with self._lock:
            state = self.state

            saved = False
            occupied = 0.0
            if frame is not None and isinstance(frame, np.ndarray):
                # Full path: in-memory gray + on-disk JPEG (same as Setup capture).
                self.set_empty_reference(frame, save=True)
                saved = True
            elif self._prev_gray is not None:
                self._empty_ref = self._prev_gray.copy()
                self._noise_floor = 0.0
                self._noise_cal_remaining = NOISE_CAL_FRAMES
                self._clear_streak_advancing = 0
            else:
                return SortCycleUpdate(
                    state,
                    "No camera frame to re-baseline — enable the camera first.",
                    virtual_bin=self.last_bin,
                    error="no_frame",
                    occupancy=None,
                )

            self._empty_streak = 0
            self._wait_peak_occupancy = 0.0
            saved_note = " (saved new empty reference)" if saved else " (memory only)"

            if state != SortState.WAIT_CLEAR:
                return SortCycleUpdate(
                    state,
                    f"Empty reference re-baselined{saved_note} (occupancy≈{occupied:.2f}%).",
                    virtual_bin=self.last_bin,
                    occupancy=occupied,
                )

            try:
                self.feeder.start()
            except ConveyorError as exc:
                self.state = SortState.IDLE
                return SortCycleUpdate(
                    SortState.IDLE, str(exc), error=str(exc), occupancy=occupied
                )

            self.state = SortState.ADVANCING
            self._occupied_streak = 0
            self._empty_streak = 0
            self._capture_give_up_wait = False
            self._identify_failed = False
            self._reset_motion()
            self._pending_match = False
            return SortCycleUpdate(
                SortState.ADVANCING,
                f"Forced clear{saved_note} (occ≈{occupied:.2f}%). Advancing…",
                virtual_bin=self.last_bin,
                occupancy=occupied,
            )

    def set_feeder_url(self, base_url: str) -> None:
        self.feeder = ConveyorClient(base_url)

    def _clear_match_future(self) -> None:
        """Drop an in-flight identify future (result ignored if still running)."""
        fut = self._match_future
        self._match_future = None
        if fut is not None and not fut.done():
            fut.cancel()

    def _emit_event(self, event: str, **fields: object) -> None:
        """Fire optional session/debug callback; never break the sort loop."""
        if self.event_fn is None:
            return
        payload = {"state": self.state.value, **fields}
        try:
            self.event_fn(event, payload)
        except Exception:  # noqa: BLE001 — logging must not stall the feeder
            logger.exception("SortCycle event_fn failed for %s", event)

    def start_loop(self) -> SortCycleUpdate:
        with self._lock:
            if self._empty_ref is None and not self.load_empty_reference():
                return SortCycleUpdate(
                    SortState.IDLE,
                    "Set empty view first (clear tray, then click Set empty view).",
                    error="no_empty_ref",
                )
            try:
                self.feeder.start()
            except ConveyorError as exc:
                self.state = SortState.IDLE
                return SortCycleUpdate(SortState.IDLE, str(exc), error=str(exc))
            self._reset_motion()
            self._occupied_streak = 0
            self._empty_streak = 0
            self._pending_match = False
            self._identify_failed = False
            self._capture_give_up_wait = False
            self._match_retries_left = 0
            self._retry_not_before = 0.0
            self._clear_match_future()
            self.state = SortState.ADVANCING
            self._emit_event("session_start")
            return SortCycleUpdate(self.state, "Belt advancing — waiting for a part…")

    def stop_loop(self) -> SortCycleUpdate:
        with self._lock:
            try:
                self.feeder.stop()
            except ConveyorError as exc:
                self.state = SortState.IDLE
                self._clear_match_future()
                self._capture_give_up_wait = False
                self._emit_event("loop_stop", reason=str(exc))
                return SortCycleUpdate(SortState.IDLE, f"Stopped (belt error: {exc})")
            self.state = SortState.IDLE
            self._capture_give_up_wait = False
            self._reset_motion()
            self._pending_match = False
            self._clear_match_future()
            self._emit_event("loop_stop")
            return SortCycleUpdate(self.state, "Feed loop stopped — belt idle.")

    def on_frame(
        self,
        frame: np.ndarray | None,
        *,
        settle_s: float | None = None,
        motion_threshold: float | None = None,
        occupied_threshold: float | None = None,
        empty_threshold: float | None = None,
        occupied_frames: int | None = None,
        empty_frames: int | None = None,
        auto_match: bool | None = None,
        roi_top: float | None = None,
        roi_bottom: float | None = None,
        roi_left: float | None = None,
        roi_right: float | None = None,
    ) -> SortCycleUpdate:
        if frame is None or not isinstance(frame, np.ndarray):
            return SortCycleUpdate(self.state, "Waiting for camera")

        settle = settle_s if settle_s is not None else self.settle_s
        motion_thr = (
            motion_threshold if motion_threshold is not None else self.motion_threshold
        )
        occ_thr = (
            occupied_threshold
            if occupied_threshold is not None
            else self.occupied_threshold
        )
        empty_thr = (
            empty_threshold if empty_threshold is not None else self.empty_threshold
        )
        occ_frames = (
            occupied_frames if occupied_frames is not None else self.occupied_frames
        )
        empty_n = empty_frames if empty_frames is not None else self.empty_frames
        do_match = self.auto_match if auto_match is None else auto_match

        with self._lock:
            roi = LandingRoi(
                top=self.roi.top if roi_top is None else roi_top,
                bottom=self.roi.bottom if roi_bottom is None else roi_bottom,
                left=self.roi.left if roi_left is None else roi_left,
                right=self.roi.right if roi_right is None else roi_right,
            ).clamped()
            self.roi = roi

            state = self.state
            empty_ref = self._empty_ref

            if state == SortState.IDLE:
                return SortCycleUpdate(state, "Idle", virtual_bin=self.last_bin)

            if empty_ref is None:
                return self.stop_loop()

            gray = _to_gray_small(frame)
            stats = _diff_stats(empty_ref, gray, roi=roi)
            occupied_score = stats.occupancy
            ref_updated = self._note_occupancy_sample(
                occupied_score, gray, changed_pct=stats.changed_pct
            )
            # WaitClear may have soft-updated empty_ref for AE — remeasure this frame.
            if state == SortState.WAIT_CLEAR and ref_updated and self._empty_ref is not None:
                empty_ref = self._empty_ref
                occupied_score = _diff_stats(empty_ref, gray, roi=roi).occupancy
            self._set_live_blob_rect(frame, gray, empty_ref, roi)
            empty_thr = self.effective_empty_threshold(empty_thr)

            if state == SortState.ADVANCING:
                # Feeder housing tip inside the green zone must not STOP the belt.
                if self._is_low_occ_feeder_fringe(frame, occupied_score, roi=roi):
                    self._absorb_ghost_into_empty_ref(gray)
                    occupied_score = _diff_stats(
                        self._empty_ref, gray, roi=roi
                    ).occupancy
                return self._handle_advancing(
                    gray, occupied_score, occ_thr, occ_frames
                )

            if state == SortState.SETTLING:
                return self._handle_settling(
                    frame, gray, settle, motion_thr, occupied_score, roi
                )

            if state == SortState.CAPTURED:
                return self._handle_captured(do_match, occupied_score)

            if state == SortState.MATCHING:
                # Release the lock before identify_fn (network) — handled below.
                pass
            elif state == SortState.WAIT_CLEAR:
                return self._handle_wait_clear(
                    frame, gray, occupied_score, empty_thr, empty_n, roi
                )
            else:
                return SortCycleUpdate(
                    state,
                    f"State {state.value}",
                    virtual_bin=self.last_bin,
                    occupancy=occupied_score,
                )

        # MATCHING only: identify_fn must run outside the lock.
        return self._handle_matching(occupied_score)

    def _reset_motion(self) -> None:
        self._prev_gray = None
        self._stable_since = None

    def _enter_wait_clear(self, occupied_score: float) -> None:
        self.state = SortState.WAIT_CLEAR
        self._empty_streak = 0
        self._wait_peak_occupancy = max(self._wait_peak_occupancy, occupied_score)
        self._reset_motion()

    def _resume_advancing(
        self, occupied_score: float, *, reason: str
    ) -> SortCycleUpdate:
        """Restart the belt after a false trigger / ghost capture."""
        self._clear_match_future()
        self._pending_match = False
        self._identify_failed = False
        self._capture_give_up_wait = False
        self._occupied_streak = 0
        self._empty_streak = 0
        self._wait_peak_occupancy = 0.0
        self._capture_occupancy = 0.0
        self._last_still_gray = None
        self._reset_motion()
        self._emit_event("resume", reason=reason, occ=round(occupied_score, 2))
        try:
            self.feeder.start()
        except ConveyorError as exc:
            self.state = SortState.IDLE
            return SortCycleUpdate(
                SortState.IDLE, str(exc), error=str(exc), occupancy=occupied_score
            )
        self.state = SortState.ADVANCING
        return SortCycleUpdate(
            SortState.ADVANCING,
            reason,
            virtual_bin=self.last_bin,
            occupancy=occupied_score,
        )

    def _absorb_ghost_into_empty_ref(self, gray: np.ndarray) -> None:
        """Blend a noise/empty false-trigger frame into the empty baseline."""
        if self._empty_ref is None or gray is None:
            return
        if self._empty_ref.shape != gray.shape:
            self._empty_ref = gray.copy()
            return
        a = GHOST_EMPTY_REF_EMA
        self._empty_ref = (1.0 - a) * self._empty_ref + a * gray

    def _is_low_occ_feeder_fringe(
        self,
        frame: np.ndarray,
        occupied_score: float,
        *,
        roi: LandingRoi | None = None,
    ) -> bool:
        """Skinny left-edge feeder tip at low occupancy (not a real part)."""
        if occupied_score >= MIN_EDGE_OVERRIDE_OCC:
            return False
        rect = self.last_live_blob_rect
        if rect is None:
            return False
        still = _normalize_frame(frame)
        fh, fw = still.shape[:2]
        return _is_left_feeder_fringe_rect(
            rect, (fh, fw), roi=roi if roi is not None else self.roi
        )

    def _live_blob_looks_empty(self, frame: np.ndarray) -> bool:
        """True when there is no amber blob, or its crop has no part structure."""
        if self.last_live_blob_rect is None:
            return True
        crop, _rect = _crop_from_frame_rect(
            _normalize_frame(frame), self.last_live_blob_rect
        )
        if crop is None:
            return True
        return crop_looks_empty(crop)

    def _capture_vs_empty(self) -> float:
        if self._empty_ref is not None and self._last_still_gray is not None:
            return _occupancy_percent(
                self._empty_ref, self._last_still_gray, roi=self.roi
            )
        return self._capture_occupancy

    def _handle_advancing(
        self,
        gray: np.ndarray,
        occupied_score: float,
        occ_thr: float,
        occ_frames: int,
    ) -> SortCycleUpdate:
        if occupied_score >= occ_thr:
            self._occupied_streak += 1
        else:
            self._occupied_streak = 0

        if self._occupied_streak < occ_frames:
            return SortCycleUpdate(
                SortState.ADVANCING,
                f"Advancing — occupancy={occupied_score:.2f}% thr={occ_thr:.2f}% "
                f"({self._occupied_streak}/{occ_frames})",
                virtual_bin=self.last_bin,
                occupancy=occupied_score,
            )

        self.state = SortState.STOPPING
        try:
            self.feeder.stop()
        except ConveyorError as exc:
            self.state = SortState.IDLE
            return SortCycleUpdate(
                SortState.IDLE, str(exc), error=str(exc), occupancy=occupied_score
            )

        self.state = SortState.SETTLING
        self._reset_motion()
        self._occupied_streak = 0
        self._capture_gate_rejects = 0
        # Fresh part → fresh retry budget for failed shape matches.
        self._match_retries_left = MATCH_MAX_RETRIES
        self._retry_not_before = 0.0
        return SortCycleUpdate(
            SortState.SETTLING,
            f"Part landed — STOP. Settling… (occupancy={occupied_score:.2f}%)",
            virtual_bin=self.last_bin,
            occupancy=occupied_score,
        )

    def _handle_settling(
        self,
        frame: np.ndarray,
        gray: np.ndarray,
        settle_s: float,
        motion_thr: float,
        occupied_score: float,
        roi: LandingRoi,
    ) -> SortCycleUpdate:
        # Hand/shadow often trips STOP then leaves. Abort before capturing an
        # empty tray (that wedges WaitClear: live≈capture≈empty ⇒ no removal).
        # Skip this while a failed-match retry delay is pending — the brick may
        # still be rolling under the threshold briefly.
        now = time.monotonic()
        if (
            occupied_score < self.occupied_threshold
            and now >= self._retry_not_before
        ):
            return self._resume_advancing(
                occupied_score,
                reason=(
                    f"False trigger — nothing left in zone "
                    f"(occ={occupied_score:.2f}% < stop {self.occupied_threshold:.2f}%). "
                    f"Resuming…"
                ),
            )

        # Left feeder tip at ~1% holds Settling forever (above stop thr, no part).
        if (
            now >= self._retry_not_before
            and self._is_low_occ_feeder_fringe(frame, occupied_score, roi=roi)
        ):
            self._absorb_ghost_into_empty_ref(gray)
            self._emit_event(
                "capture_reject",
                reason="feeder fringe",
                occ=round(float(occupied_score), 2),
                fringe_ignore=1,
            )
            return self._resume_advancing(
                occupied_score,
                reason=(
                    f"Feeder fringe ignored (occ={occupied_score:.2f}%) — resuming…"
                ),
            )

        if self._prev_gray is not None:
            motion = _motion_score(self._prev_gray, gray, roi=roi)
        else:
            motion = motion_thr + 1.0
        self._prev_gray = gray

        if motion > motion_thr:
            self._stable_since = None
            return SortCycleUpdate(
                SortState.SETTLING,
                f"Settling — motion={motion:.1f} occupancy={occupied_score:.2f}%",
                virtual_bin=self.last_bin,
                occupancy=occupied_score,
            )

        if self._stable_since is None:
            self._stable_since = now

        stable_for = now - self._stable_since
        if stable_for < settle_s:
            return SortCycleUpdate(
                SortState.SETTLING,
                f"Settling… {stable_for:.1f}/{settle_s:.1f}s "
                f"(occupancy={occupied_score:.2f}%)",
                virtual_bin=self.last_bin,
                occupancy=occupied_score,
            )

        # Hold off re-capture after a failed match so the piece can finish moving.
        if now < self._retry_not_before:
            return SortCycleUpdate(
                SortState.SETTLING,
                f"Retrying photo in {self._retry_not_before - now:.1f}s "
                f"(occupancy={occupied_score:.2f}%)",
                virtual_bin=self.last_bin,
                occupancy=occupied_score,
            )

        # Freeze still now (dedicated capture step).
        still = _normalize_frame(frame).copy()
        fh, fw = still.shape[:2]
        # Tight padded blob only — never upload the full landing ROI.
        if self._empty_ref is not None:
            crop, rect = compute_padded_blob_crop(
                still,
                self._empty_ref,
                gray,
                roi,
                allow_roi_fallback=False,
            )
            reject = capture_reject_reason(
                self._empty_ref, gray, roi, (fh, fw), crop, rect
            )
        else:
            crop, rect = still, (0, 0, fw, fh)
            reject = "empty crop" if crop_looks_empty(crop) else None

        if reject and self._empty_ref is not None and _is_edge_touch_reject(reject):
            # Only ignore the skinny left-edge feeder tip — not real small parts.
            fringe_rect = rect or self.last_live_blob_rect
            if (
                occupied_score < MIN_EDGE_OVERRIDE_OCC
                and _is_left_feeder_fringe_rect(
                    fringe_rect, (fh, fw), roi=roi
                )
            ):
                self._absorb_ghost_into_empty_ref(gray)
                self._capture_gate_rejects = 0
                self._emit_event(
                    "capture_reject",
                    reason=reject,
                    occ=round(float(occupied_score), 2),
                    rejects=0,
                    fringe_ignore=1,
                )
                return self._resume_advancing(
                    occupied_score,
                    reason=(
                        f"Feeder fringe ignored (occ={occupied_score:.2f}%) — resuming…"
                    ),
                )

            # After enough edge rejects the part is parked on the border — shoot
            # the full blob (may extend outside the green zone).
            next_n = self._capture_gate_rejects + 1
            if next_n >= CAPTURE_GATE_MAX_REJECTS:
                exp_crop, exp_rect = compute_padded_blob_crop(
                    still,
                    self._empty_ref,
                    gray,
                    roi,
                    allow_roi_fallback=False,
                    expand_outside_roi=True,
                )
                use_crop = exp_crop if exp_crop is not None else crop
                use_rect = exp_rect if exp_rect is not None else rect
                # Edge-straddle crops often fail empty/geometry vs the ROI even
                # though the amber blob is a real part — skip those, keep multi-part.
                override = capture_reject_reason(
                    self._empty_ref,
                    gray,
                    roi,
                    (fh, fw),
                    use_crop,
                    use_rect,
                    allow_edge_contact=True,
                    skip_empty_crop=True,
                    skip_geometry=True,
                )
                can_shoot = (
                    override is None
                    and use_crop is not None
                    and use_rect is not None
                )
                self._emit_event(
                    "capture_reject",
                    reason=reject,
                    occ=round(float(occupied_score), 2),
                    rejects=next_n,
                    edge_override=1 if can_shoot else 0,
                    expand_crop=1 if exp_crop is not None else 0,
                )
                if can_shoot:
                    return self._accept_capture(
                        still,
                        gray,
                        use_crop,
                        use_rect,
                        occupied_score,
                        roi,
                        edge_override=True,
                    )
                # Multi-part or no crop — park, don't belt-thrash.
                self._capture_gate_rejects = 0
                if occupied_score >= self.occupied_threshold:
                    return self._wait_clear_after_capture_give_up(
                        gray,
                        occupied_score,
                        reason=override or reject,
                        rejects=next_n,
                    )
                return self._resume_advancing(
                    occupied_score,
                    reason=(
                        f"Capture rejected ({override or reject}) ×{next_n} "
                        f"— giving up, resuming…"
                    ),
                )

        if (
            reject
            and reject.startswith("crop too large")
            and self.last_live_blob_rect is not None
        ):
            # Flooded ROI crop (bad empty-ref / AE) while amber outline is a real
            # part — shoot the live blob instead of rejecting.
            live_crop, live_rect = _crop_from_frame_rect(
                still, self.last_live_blob_rect
            )
            geom_ok = False
            if live_rect is not None:
                roi_rect = _roi_bounds_px((fh, fw), roi)
                geom_ok = crop_geometry_reject_reason(live_rect, roi_rect) is None
            can_shoot = (
                live_crop is not None
                and live_rect is not None
                and geom_ok
                and not crop_looks_empty(live_crop)
            )
            if can_shoot:
                self._emit_event(
                    "capture_reject",
                    reason=reject,
                    occ=round(float(occupied_score), 2),
                    rejects=self._capture_gate_rejects,
                    live_blob_override=1,
                )
                return self._accept_capture(
                    still,
                    gray,
                    live_crop,
                    live_rect,
                    occupied_score,
                    roi,
                    edge_override=True,
                )

        if (
            reject
            and self._empty_ref is not None
            and reject.startswith("multiple parts")
            and occupied_score >= MIN_EDGE_OVERRIDE_OCC
        ):
            # Feeder tip + real part often looks like two blobs. After enough
            # rejects, shoot the blob with the most mass inside the zone.
            next_n = self._capture_gate_rejects + 1
            if next_n >= CAPTURE_GATE_MAX_REJECTS:
                exp_crop, exp_rect = compute_padded_blob_crop(
                    still,
                    self._empty_ref,
                    gray,
                    roi,
                    allow_roi_fallback=False,
                    expand_outside_roi=True,
                )
                # Keep geometry: hand/shadow floods become "crop too large" and
                # must NOT be force-shot (that poisoned empty-ref + blocked the
                # next real part).
                can_shoot = exp_crop is not None and exp_rect is not None
                still_ok = None
                if can_shoot:
                    still_ok = capture_reject_reason(
                        self._empty_ref,
                        gray,
                        roi,
                        (fh, fw),
                        exp_crop,
                        exp_rect,
                        allow_edge_contact=True,
                        skip_empty_crop=False,
                        skip_geometry=False,
                        allow_multi_part=True,
                    )
                    can_shoot = still_ok is None
                self._emit_event(
                    "capture_reject",
                    reason=reject,
                    occ=round(float(occupied_score), 2),
                    rejects=next_n,
                    multi_override=1 if can_shoot else 0,
                    override_fail=still_ok or "",
                )
                if can_shoot:
                    return self._accept_capture(
                        still,
                        gray,
                        exp_crop,
                        exp_rect,
                        occupied_score,
                        roi,
                        edge_override=True,
                    )

        if reject:
            return self._on_capture_gate_reject(
                gray, occupied_score, reject, crop=crop
            )

        assert crop is not None and rect is not None
        return self._accept_capture(
            still, gray, crop, rect, occupied_score, roi
        )

    def _accept_capture(
        self,
        still: np.ndarray,
        gray: np.ndarray,
        crop: np.ndarray,
        rect: tuple[int, int, int, int],
        occupied_score: float,
        roi: LandingRoi,
        *,
        edge_override: bool = False,
    ) -> SortCycleUpdate:
        """Commit a frozen still + API crop and enter CAPTURED."""
        path = _save_still(still)
        self.last_still = still
        self.last_still_path = path
        self.last_api_crop = crop
        self.last_crop_rect = rect
        # Cache the captured scene for removal detection in WaitClear.
        self._last_still_gray = gray.copy()
        # Keep the true blob size (don't inflate) so removal thresholds stay honest.
        self._capture_occupancy = float(occupied_score)
        self._wait_peak_occupancy = occupied_score
        self._pending_match = True
        self._capture_gate_rejects = 0
        self._capture_give_up_wait = False

        self.state = SortState.CAPTURED
        self._empty_streak = 0
        self._reset_motion()
        x0, y0, x1, y1 = rect
        self._emit_event(
            "capture",
            still=str(path),
            crop="captures/pending",
            crop_rect=f"{x0},{y0},{x1},{y1}",
            occ=round(float(occupied_score), 2),
            edge_override=1 if edge_override else 0,
        )
        status = f"Captured still → {path.name}"
        if edge_override:
            status = f"Captured (edge override) → {path.name}"

        return SortCycleUpdate(
            SortState.CAPTURED,
            status,
            virtual_bin=self.last_bin,
            occupancy=occupied_score,
            preview=annotate_landing_roi(still, roi),
            captured=True,
        )

    def _on_capture_gate_reject(
        self,
        gray: np.ndarray,
        occupied_score: float,
        reason: str,
        *,
        crop: np.ndarray | None = None,
    ) -> SortCycleUpdate:
        """Empty/ghost → resume; framing/multi-part → re-settle (then give up).

        Edge-touch give-up is handled in ``_handle_settling`` (capture override).
        Flood / multi-part still park in WaitClear.
        """
        if reason == "empty crop":
            self._absorb_ghost_into_empty_ref(gray)
            self._capture_gate_rejects = 0
            detail = reason
            if crop is not None:
                _std, edge, lap, strong = crop_content_scores(crop)
                detail = (
                    f"empty crop (edge={edge:.1f} lap={lap:.1f} "
                    f"strong={strong:.3f})"
                )
            self._emit_event(
                "capture_reject",
                reason=detail,
                occ=round(float(occupied_score), 2),
                rejects=0,
            )
            return self._resume_advancing(
                occupied_score,
                reason=f"{detail} — false trigger, resuming…",
            )

        self._capture_gate_rejects += 1
        n = self._capture_gate_rejects
        self._emit_event(
            "capture_reject",
            reason=reason,
            occ=round(float(occupied_score), 2),
            rejects=n,
        )
        if n >= CAPTURE_GATE_MAX_REJECTS:
            self._capture_gate_rejects = 0
            # Still jammed (hand / flood / unphotoable part) — don't resume the
            # belt or we immediately STOP→SETTLE→reject in a tight loop.
            # Edge-touch should have been overridden in settle; if we land here
            # with edge reason, still try WaitClear rather than thrashing.
            if occupied_score >= self.occupied_threshold:
                return self._wait_clear_after_capture_give_up(
                    gray, occupied_score, reason=reason, rejects=n
                )
            return self._resume_advancing(
                occupied_score,
                reason=(
                    f"Capture rejected ({reason}) ×{n} — giving up, resuming…"
                ),
            )

        # Re-settle so the part can center / a second part can clear.
        self._stable_since = None
        self._reset_motion()
        return SortCycleUpdate(
            SortState.SETTLING,
            f"Capture rejected: {reason} — re-settling… "
            f"({n}/{CAPTURE_GATE_MAX_REJECTS})",
            virtual_bin=self.last_bin,
            occupancy=occupied_score,
        )

    def _wait_clear_after_capture_give_up(
        self,
        gray: np.ndarray,
        occupied_score: float,
        *,
        reason: str,
        rejects: int,
    ) -> SortCycleUpdate:
        """Park in WaitClear until the zone empties after unphotoable rejects."""
        self._clear_match_future()
        self._pending_match = False
        # Do NOT set _identify_failed — that enables the ghost-clear shortcut and
        # immediately resumes while an edge-stuck part is still in the zone.
        self._identify_failed = False
        self._capture_give_up_wait = True
        # No frozen still → WaitClear only requires empty occupancy (not removal).
        self._last_still_gray = None
        self._capture_occupancy = 0.0
        self._emit_event(
            "capture_give_up",
            reason=reason,
            occ=round(float(occupied_score), 2),
            rejects=rejects,
            action="wait_clear",
        )
        self._enter_wait_clear(occupied_score)
        return SortCycleUpdate(
            SortState.WAIT_CLEAR,
            (
                f"Capture gave up ({reason}) ×{rejects} — "
                f"clear the zone to continue"
            ),
            virtual_bin=self.last_bin,
            occupancy=occupied_score,
            preview=self.last_still,
        )

    def _handle_captured(
        self, do_match: bool, occupied_score: float
    ) -> SortCycleUpdate:
        if do_match and self._pending_match:
            self.state = SortState.MATCHING
            return SortCycleUpdate(
                SortState.MATCHING,
                "Matching frozen still…",
                virtual_bin=self.last_bin,
                occupancy=occupied_score,
                preview=self.last_still,
            )

        # Auto-match off (or already handled): wait for clear.
        self._pending_match = False
        self._enter_wait_clear(occupied_score)
        return SortCycleUpdate(
            SortState.WAIT_CLEAR,
            "Captured — remove piece to continue",
            virtual_bin=self.last_bin,
            occupancy=occupied_score,
            preview=self.last_still,
        )

    def _handle_matching(self, occupied_score: float) -> SortCycleUpdate:
        """Poll / start background identify — never block the stream on the API."""
        with self._lock:
            still = self.last_still
            self._pending_match = False

            if still is None:
                self._clear_match_future()
                self._enter_wait_clear(occupied_score)
                return SortCycleUpdate(
                    SortState.WAIT_CLEAR,
                    "No still to match — remove piece to continue",
                    error="no_still",
                    occupancy=occupied_score,
                )

            if self.identify_fn is None:
                self._clear_match_future()
                self._enter_wait_clear(occupied_score)
                return SortCycleUpdate(
                    SortState.WAIT_CLEAR,
                    "Captured (no identify fn) — remove piece to continue",
                    occupancy=occupied_score,
                    preview=still,
                )

            identify_fn = self.identify_fn
            fut = self._match_future
            if fut is None:
                # Submit outside the critical section below — capture args first.
                still_arg = still
                start_new = True
            else:
                start_new = False

        if start_new:
            fut = self._match_pool.submit(identify_fn, still_arg)
            with self._lock:
                # Another frame may have started a match; keep the first.
                if self._match_future is None:
                    self._match_future = fut
                else:
                    fut = self._match_future
            return SortCycleUpdate(
                SortState.MATCHING,
                "Matching…",
                virtual_bin=self.last_bin,
                occupancy=occupied_score,
                preview=still,
            )

        assert fut is not None
        if not fut.done():
            return SortCycleUpdate(
                SortState.MATCHING,
                "Matching…",
                virtual_bin=self.last_bin,
                occupancy=occupied_score,
                preview=still,
            )

        try:
            outcome = fut.result()
        except Exception as exc:  # noqa: BLE001 — surface to WaitClear
            logger.exception("Identify worker failed")
            outcome = IdentifyOutcome(error=str(exc))

        with self._lock:
            self._match_future = None
            # No confident match — piece may have still been rolling at capture.
            # Go back to Settling, wait, and re-shoot before giving up.
            if outcome.candidate is None and self._match_retries_left > 0:
                self._match_retries_left -= 1
                self._retry_not_before = time.monotonic() + MATCH_RETRY_DELAY_S
                self.state = SortState.SETTLING
                self._reset_motion()
                return SortCycleUpdate(
                    SortState.SETTLING,
                    f"{outcome.error or 'No match'} — retrying with a fresh photo "
                    f"in {MATCH_RETRY_DELAY_S:.0f}s…",
                    virtual_bin=self.last_bin,
                    occupancy=occupied_score,
                    preview=still,
                )

            bin_id = virtual_bin(outcome.candidate, outcome.color)
            self.last_bin = bin_id
            self._identify_failed = outcome.candidate is None

            # No shape match on a weak / empty-looking crop — do not wedge
            # WaitClear. Absorb residual into the empty ref and keep feeding.
            if self._identify_failed:
                cap = self._capture_vs_empty()
                empty_crop = (
                    self.last_api_crop is not None
                    and crop_looks_empty(self.last_api_crop)
                )
                self._emit_event(
                    "match_fail",
                    error=outcome.error or "no match",
                    occ=round(float(occupied_score), 2),
                    api_calls=outcome.api_calls,
                    still=str(self.last_still_path) if self.last_still_path else None,
                )
                if empty_crop or cap <= GHOST_NO_MATCH_MAX_OCC:
                    if self._last_still_gray is not None:
                        self._absorb_ghost_into_empty_ref(self._last_still_gray)
                    why = "empty crop" if empty_crop else f"occ≈{cap:.1f}%"
                    return self._resume_advancing(
                        occupied_score,
                        reason=(
                            f"No shape match ({why}) — empty/noise capture, resuming…"
                        ),
                    )
            else:
                cand = outcome.candidate
                color = outcome.color
                self._emit_event(
                    "match_ok",
                    part=getattr(cand, "id", None) or "",
                    score=round(float(getattr(cand, "score", 0.0) or 0.0) * 100, 1),
                    color=getattr(color, "key", None)
                    or getattr(color, "name", None)
                    or "",
                    api_calls=outcome.api_calls,
                    still=str(self.last_still_path) if self.last_still_path else None,
                    crop="captures/pending",
                )

            self._enter_wait_clear(occupied_score)

            if outcome.error:
                status = f"{outcome.error} → bin `{bin_id}` — remove piece to continue"
            else:
                status = f"Matched → bin `{bin_id}` — remove piece to continue"

            return SortCycleUpdate(
                SortState.WAIT_CLEAR,
                status,
                virtual_bin=bin_id,
                occupancy=occupied_score,
                preview=outcome.preview if outcome.preview is not None else still,
                matched=True,
                identify=outcome,
                error=outcome.error,
            )

    def _try_capture_live_blob(
        self,
        frame: np.ndarray,
        gray: np.ndarray,
        occupied_score: float,
        roi: LandingRoi,
        *,
        why: str,
    ) -> SortCycleUpdate | None:
        """If the amber live blob is a sane part crop, capture it now."""
        if self.last_live_blob_rect is None or self._empty_ref is None:
            return None
        still = _normalize_frame(frame).copy()
        fh, fw = still.shape[:2]
        live_crop, live_rect = _crop_from_frame_rect(still, self.last_live_blob_rect)
        if live_crop is None or live_rect is None:
            return None
        roi_rect = _roi_bounds_px((fh, fw), roi)
        if crop_geometry_reject_reason(live_rect, roi_rect) is not None:
            return None
        if crop_looks_empty(live_crop):
            return None
        self._emit_event(
            "capture",
            reason=why,
            occ=round(float(occupied_score), 2),
            live_blob_override=1,
            crop_rect=f"{live_rect[0]},{live_rect[1]},{live_rect[2]},{live_rect[3]}",
        )
        return self._accept_capture(
            still,
            gray,
            live_crop,
            live_rect,
            occupied_score,
            roi,
            edge_override=True,
        )

    def _handle_wait_clear(
        self,
        frame: np.ndarray,
        gray: np.ndarray,
        occupied_score: float,
        empty_thr: float,
        empty_n: int,
        roi: LandingRoi,
    ) -> SortCycleUpdate:
        # Track stability so removal detection ignores the hand reaching in.
        now = time.monotonic()
        if self._prev_gray is not None:
            motion = _motion_score(self._prev_gray, gray, roi=self.roi)
        else:
            motion = self.motion_threshold + 1.0
        self._prev_gray = gray
        if motion > self.motion_threshold:
            self._stable_since = None
        elif self._stable_since is None:
            self._stable_since = now
        stable_for = (now - self._stable_since) if self._stable_since else 0.0

        self._wait_peak_occupancy = max(self._wait_peak_occupancy, occupied_score)
        peak = self._wait_peak_occupancy

        # Give-up park with a clear amber part (e.g. leaf slightly over the zone
        # edge) — shoot that blob instead of waiting forever for empty.
        if (
            self._capture_give_up_wait
            and stable_for >= WAIT_CLEAR_STABLE_S
            and occupied_score >= self.occupied_threshold
        ):
            shot = self._try_capture_live_blob(
                frame,
                gray,
                occupied_score,
                roi,
                why="give_up_live_blob",
            )
            if shot is not None:
                self._capture_give_up_wait = False
                return shot

        # Diff vs frozen capture first — gates every "clear" shortcut so a small
        # sitting part cannot be mistaken for an already-empty tray.
        removal_score = 0.0
        removal_needed = REMOVAL_MIN_PCT
        removal_ok = False
        if (
            self._last_still_gray is not None
            and stable_for >= WAIT_CLEAR_STABLE_S
        ):
            removal_score = _occupancy_percent(
                self._last_still_gray, gray, roi=self.roi
            )
            removal_needed = max(
                REMOVAL_MIN_PCT, REMOVAL_MATCH_FRAC * self._capture_occupancy
            )
            removal_ok = removal_score >= removal_needed

        # Soft-pull empty ref only after the capture no longer matches (part gone).
        # Previously, small parts (occ ~1–2%) looked "already dropping" and got
        # baked into the empty reference → belt restarted while still in tray.
        if (
            removal_ok
            and self._empty_ref is not None
            and empty_thr < occupied_score <= WAIT_CLEAR_STUCK_MAX_PCT
        ):
            a = WAIT_CLEAR_STUCK_EMA
            self._empty_ref = (1.0 - a) * self._empty_ref + a * gray
            occupied_score = _diff_stats(self._empty_ref, gray, roi=self.roi).occupancy

        empty_match = occupied_score <= empty_thr
        if empty_match:
            self._empty_streak += 1
        else:
            self._empty_streak = 0

        # Did the frozen capture itself look like a real part vs the empty ref?
        # Re-check here so ghost captures (shadow/AE flash, empty still) do not
        # require an impossible removal spike (live≈capture≈empty → stuck).
        capture_vs_empty = self._capture_vs_empty()
        had_part = (
            not self._identify_failed
            and capture_vs_empty >= self.occupied_threshold
        )

        empty_ref_clear = empty_match and self._empty_streak >= empty_n
        if had_part and self._last_still_gray is not None:
            empty_ref_clear = empty_ref_clear and removal_ok

        # Occupancy dropped from WaitClear peak — only with removal evidence.
        peak_drop_ok = (
            removal_ok
            and peak >= self.occupied_threshold
            and occupied_score <= PEAK_DROP_FRAC * peak
        )

        # Failed ID + (empty-looking crop OR scene unchanged with modest occ):
        # nothing real to remove — absorb residual and resume.
        # Skip after capture-gate give-up: that path parks on a real stuck blob.
        empty_crop = (
            self.last_api_crop is not None and crop_looks_empty(self.last_api_crop)
        )
        ghost_clear = (
            not self._capture_give_up_wait
            and self._identify_failed
            and stable_for >= WAIT_CLEAR_STABLE_S
            and self._last_still_gray is not None
            and (
                empty_crop
                or (
                    removal_score < removal_needed
                    and occupied_score <= GHOST_NO_MATCH_MAX_OCC
                )
            )
        )
        if ghost_clear:
            self._absorb_ghost_into_empty_ref(gray)
            return self._resume_advancing(
                occupied_score,
                reason=(
                    f"No match + {'empty crop' if empty_crop else 'unchanged scene'} "
                    f"(occ={occupied_score:.1f}%) — cleared ghost, resuming…"
                ),
            )

        # After a real match: part is gone (removal_ok) but AE left a soft high-%
        # blotch. Next real parts have structure; empty amber crops do not.
        if (
            not self._capture_give_up_wait
            and removal_ok
            and had_part
            and stable_for >= WAIT_CLEAR_STABLE_S
            and occupied_score > empty_thr
            and self._live_blob_looks_empty(frame)
        ):
            self._absorb_ghost_into_empty_ref(gray)
            return self._resume_advancing(
                occupied_score,
                reason=(
                    f"Cleared AE ghost after removal "
                    f"(occ={occupied_score:.1f}%) — resuming…"
                ),
            )

        # Give-up wait: only real empty occupancy may resume (no removal/peak tricks).
        # After a real match: "differs from capture" alone is not enough — the next
        # part arriving also differs, which used to resume at ~20% occ with a leaf
        # already in the zone.
        if self._capture_give_up_wait:
            can_resume = empty_ref_clear
        else:
            zone_quiet = occupied_score <= max(
                empty_thr * 3.0, self.occupied_threshold
            )
            can_resume = empty_ref_clear or (
                zone_quiet and (removal_ok or peak_drop_ok)
            )

        if not can_resume:
            wait_msg = (
                f"Waiting for clear — occupancy={occupied_score:.2f}% "
                f"thr={empty_thr:.2f}% (noise≈{self._noise_floor:.1f}%) "
                f"({self._empty_streak}/{empty_n})"
            )
            if self._capture_give_up_wait:
                wait_msg = (
                    f"Capture give-up — clear the zone "
                    f"(occupancy={occupied_score:.2f}% thr={empty_thr:.2f}% "
                    f"{self._empty_streak}/{empty_n})"
                )
            elif had_part and self._last_still_gray is not None:
                wait_msg += (
                    f" · vs capture={removal_score:.2f}%/{removal_needed:.2f}%"
                )
            if stable_for < WAIT_CLEAR_STABLE_S:
                wait_msg += " · settling"
            return SortCycleUpdate(
                SortState.WAIT_CLEAR,
                wait_msg,
                virtual_bin=self.last_bin,
                occupancy=occupied_score,
                preview=self.last_still,
            )

        # Snap in-memory empty ref to this clear frame so the next cycle
        # is not fighting leftover AE / drift from the piece that just left.
        self._empty_ref = gray.copy()
        self._noise_floor = min(self._noise_floor, occupied_score)
        self._clear_streak_advancing = 0
        self._wait_peak_occupancy = 0.0
        self._capture_give_up_wait = False
        self._emit_event(
            "resume",
            reason="WaitClear clear",
            occ=round(float(occupied_score), 2),
        )

        try:
            self.feeder.start()
        except ConveyorError as exc:
            self.state = SortState.IDLE
            return SortCycleUpdate(
                SortState.IDLE, str(exc), error=str(exc), occupancy=occupied_score
            )

        self.state = SortState.ADVANCING
        self._occupied_streak = 0
        self._empty_streak = 0
        self._reset_motion()
        self._pending_match = False
        return SortCycleUpdate(
            SortState.ADVANCING,
            "Tray clear — advancing for next part…",
            virtual_bin=self.last_bin,
            occupancy=occupied_score,
        )

"""HSV baseline color detection for LEGO bricks (demo / prototype)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from .brickognize_client import BoundingBox

OVERLAY_COLOR = (0, 220, 90)


@dataclass(frozen=True)
class ColorDetection:
    name: str
    key: str
    confidence: float
    rgb: tuple[int, int, int]
    sample_pixels: int


@dataclass(frozen=True)
class _ColorRule:
    key: str
    name: str
    rgb: tuple[int, int, int]
    hue_ranges: tuple[tuple[float, float], ...] | None = None
    sat_min: float = 0.0
    sat_max: float = 1.0
    val_min: float = 0.0
    val_max: float = 1.0


_COLOR_RULES: tuple[_ColorRule, ...] = (
    _ColorRule("black", "Black", (5, 19, 29), sat_max=0.35, val_max=0.28),
    _ColorRule("white", "White", (255, 255, 255), sat_max=0.18, val_min=0.62),
    _ColorRule("red", "Bright Red", (201, 26, 9), hue_ranges=((0, 18), (342, 360)), sat_min=0.28),
    _ColorRule("orange", "Bright Orange", (254, 138, 24), hue_ranges=((18, 48),), sat_min=0.35),
    _ColorRule("yellow", "Bright Yellow", (242, 205, 55), hue_ranges=((48, 72),), sat_min=0.28),
    _ColorRule("green", "Bright Green", (35, 120, 65), hue_ranges=((72, 165),), sat_min=0.22),
    _ColorRule("blue", "Bright Blue", (0, 85, 191), hue_ranges=((165, 255),), sat_min=0.22),
)

_UNKNOWN = ColorDetection("Unknown", "unknown", 0.0, (128, 128, 128), 0)


def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    """Convert RGB uint8 array (H, W, 3) to HSV float (H 0–360, S/V 0–1)."""
    rgb_f = rgb.astype(np.float32) / 255.0
    r, g, b = rgb_f[..., 0], rgb_f[..., 1], rgb_f[..., 2]
    cmax = np.max(rgb_f, axis=-1)
    cmin = np.min(rgb_f, axis=-1)
    delta = cmax - cmin

    hue = np.zeros_like(cmax)
    mask = delta > 1e-6
    rm, gm, bm = r[mask], g[mask], b[mask]
    d = delta[mask]
    cm = cmax[mask]

    sec = np.zeros_like(rm)
    idx = cm == rm
    sec[idx] = (gm[idx] - bm[idx]) / d[idx]
    idx = cm == gm
    sec[idx] = 2.0 + (bm[idx] - rm[idx]) / d[idx]
    idx = cm == bm
    sec[idx] = 4.0 + (rm[idx] - gm[idx]) / d[idx]
    hue[mask] = (sec * 60.0) % 360.0

    sat = np.where(cmax > 1e-6, delta / cmax, 0.0)
    val = cmax
    return np.stack([hue, sat, val], axis=-1)


def _hue_in_ranges(hue: float, ranges: tuple[tuple[float, float], ...]) -> bool:
    for low, high in ranges:
        if low <= hue <= high:
            return True
    return False


def _rule_score(h: float, s: float, v: float, rule: _ColorRule) -> float:
    if v < rule.val_min or v > rule.val_max:
        return 0.0
    if s < rule.sat_min or s > rule.sat_max:
        return 0.0
    if rule.hue_ranges is not None:
        if not _hue_in_ranges(h, rule.hue_ranges):
            return 0.0
        for low, high in rule.hue_ranges:
            if low <= h <= high:
                mid = (low + high) / 2
                span = max(high - low, 1.0)
                hue_fit = 1.0 - min(abs(h - mid) / (span / 2), 1.0)
                break
        else:
            hue_fit = 0.5
        sat_fit = min((s - rule.sat_min) / max(0.25 - rule.sat_min, 0.05), 1.0)
        return 0.55 * hue_fit + 0.45 * sat_fit

    if rule.key == "black":
        return min((rule.val_max - v) / max(rule.val_max, 0.05), 1.0) * min(
            (rule.sat_max - s) / max(rule.sat_max, 0.05), 1.0
        )
    if rule.key == "white":
        return min((v - rule.val_min) / max(1.0 - rule.val_min, 0.05), 1.0) * min(
            (rule.sat_max - s) / max(rule.sat_max, 0.05), 1.0
        )
    return 0.0


def _classify_median(h: float, s: float, v: float) -> ColorDetection:
    if v < 0.22:
        rule = next(r for r in _COLOR_RULES if r.key == "black")
        conf = min((rule.val_max - v) / max(rule.val_max, 0.05), 1.0)
        return ColorDetection(rule.name, rule.key, float(conf), rule.rgb, 0)
    if s < 0.18 and v > 0.62:
        rule = next(r for r in _COLOR_RULES if r.key == "white")
        conf = min((v - rule.val_min) / max(1.0 - rule.val_min, 0.05), 1.0)
        return ColorDetection(rule.name, rule.key, float(conf), rule.rgb, 0)

    scores = [(rule, _rule_score(h, s, v, rule)) for rule in _COLOR_RULES]
    scores.sort(key=lambda pair: pair[1], reverse=True)
    best_rule, best_score = scores[0]
    second_score = scores[1][1] if len(scores) > 1 else 0.0

    if best_score < 0.15:
        return _UNKNOWN

    confidence = best_score
    if second_score > 0.6 * best_score:
        confidence *= 0.7

    return ColorDetection(
        name=best_rule.name,
        key=best_rule.key,
        confidence=float(min(max(confidence, 0.0), 1.0)),
        rgb=best_rule.rgb,
        sample_pixels=0,
    )


def _color_from_rgb_crop(rgb_crop: np.ndarray) -> ColorDetection:
    if rgb_crop.size == 0:
        return _UNKNOWN

    hsv = _rgb_to_hsv(rgb_crop)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    chroma = (s > 0.15) & (v > 0.12) & (v < 0.98)
    if int(chroma.sum()) < 20:
        chroma = np.ones(rgb_crop.shape[:2], dtype=bool)

    sample_count = int(chroma.sum())
    result = _classify_median(
        float(np.median(h[chroma])),
        float(np.median(s[chroma])),
        float(np.median(v[chroma])),
    )
    confidence = result.confidence
    if sample_count < 60:
        confidence *= 0.85
    return ColorDetection(
        name=result.name,
        key=result.key,
        confidence=float(min(max(confidence, 0.0), 1.0)),
        rgb=result.rgb,
        sample_pixels=sample_count,
    )


def detect_color_in_rect(
    image: Image.Image | np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
) -> ColorDetection:
    """Sample color inside a pixel rect on the given image."""
    if isinstance(image, Image.Image):
        rgb = np.asarray(image.convert("RGB"))
    else:
        rgb = np.asarray(image)
        if rgb.ndim == 3 and rgb.shape[2] >= 4:
            rgb = rgb[..., :3]
    h, w = rgb.shape[:2]
    x0 = max(0, min(w, int(x0)))
    x1 = max(0, min(w, int(x1)))
    y0 = max(0, min(h, int(y0)))
    y1 = max(0, min(h, int(y1)))
    if x1 <= x0 or y1 <= y0:
        return _UNKNOWN
    return _color_from_rgb_crop(rgb[y0:y1, x0:x1])


def detect_color_in_bbox(image: Image.Image, bbox: BoundingBox) -> ColorDetection:
    """Sample color only inside the Brickognize shape-detection bounding box."""
    rgb = np.asarray(image.convert("RGB"))
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = bbox.to_pixel_rect(w, h)
    return detect_color_in_rect(rgb, x0, y0, x1, y1)


def _draw_rect_outline(
    rgb: np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    color: tuple[int, int, int],
    thickness: int,
) -> None:
    h, w = rgb.shape[:2]
    x0, x1 = max(0, x0), min(w - 1, x1)
    y0, y1 = max(0, y0), min(h - 1, y1)
    t = max(1, thickness)
    rgb[y0 : y0 + t, x0 : x1 + 1] = color
    rgb[y1 - t + 1 : y1 + 1, x0 : x1 + 1] = color
    rgb[y0 : y1 + 1, x0 : x0 + t] = color
    rgb[y0 : y1 + 1, x1 - t + 1 : x1 + 1] = color


def annotate_rect_overlay(
    frame: np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    color: tuple[int, int, int] = OVERLAY_COLOR,
) -> np.ndarray:
    """Draw outline on a pixel rect (cheap overlay for crop / part bounds)."""
    if frame is None or not isinstance(frame, np.ndarray) or frame.ndim != 3:
        return frame

    out = frame.copy()
    h, w = out.shape[:2]
    if x1 <= x0 or y1 <= y0:
        return out

    thickness = max(2, min(h, w) // 150)
    _draw_rect_outline(out, x0, y0, x1, y1, color=color, thickness=thickness)
    return out


def annotate_bbox_overlay(
    frame: np.ndarray,
    bbox: BoundingBox,
    *,
    origin: tuple[int, int] = (0, 0),
    source_size: tuple[int, int] | None = None,
) -> np.ndarray:
    """Draw outline on the Brickognize bounding box (cheap; call only after a match).

    ``origin`` / ``source_size`` map a box from an API crop back onto ``frame``.
    With defaults, behaves as before: scale the API box onto the full frame.
    """
    if frame is None or not isinstance(frame, np.ndarray) or frame.ndim != 3:
        return frame

    ox, oy = origin
    if source_size is not None:
        sw, sh = source_size
    elif ox or oy:
        sw = int(bbox.image_width) if bbox.image_width > 0 else max(1, frame.shape[1] - ox)
        sh = int(bbox.image_height) if bbox.image_height > 0 else max(1, frame.shape[0] - oy)
    else:
        sh, sw = frame.shape[:2]
    x0, y0, x1, y1 = bbox.to_pixel_rect(sw, sh)
    return annotate_rect_overlay(frame, x0 + ox, y0 + oy, x1 + ox, y1 + oy)


def format_color_html(det: ColorDetection | None) -> str:
    """Render a color swatch + label for Gradio HTML output."""
    if det is None:
        return "<p><em>Color available after shape match.</em></p>"
    hex_rgb = f"#{det.rgb[0]:02x}{det.rgb[1]:02x}{det.rgb[2]:02x}"
    pct = det.confidence * 100
    border = "#666" if det.key in {"white", "yellow"} else "#333"
    hint = ""
    if det.key == "unknown":
        return "<p><em>Could not read color in detected region.</em></p>"
    if pct < 55:
        hint = (
            '<div style="font-size:0.85em;opacity:0.8;margin-top:2px">'
            "Low confidence — improve lighting or plain background."
            "</div>"
        )
    return (
        f'<div style="display:flex;align-items:center;gap:10px;margin-top:10px">'
        f'<div style="width:40px;height:40px;border-radius:8px;'
        f'background:{hex_rgb};border:2px solid {border};flex-shrink:0"></div>'
        f"<div><strong>{det.name}</strong> "
        f'<span style="opacity:0.85">· {pct:.0f}% color confidence</span>'
        f"{hint}</div></div>"
    )

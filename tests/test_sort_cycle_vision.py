"""Pure vision helpers in orchestrator.sort_cycle."""

from __future__ import annotations

import numpy as np
from conftest import add_blob, make_frame

from orchestrator import sort_cycle as sc
from orchestrator.sort_cycle import (
    LandingRoi,
    _largest_blob_bbox,
    _largest_blob_pixels,
    _normalize_frame,
    _occupancy_percent,
    _pad_rect,
    _roi_diff_mask,
    _to_gray_small,
    blob_touches_roi_edge,
    capture_reject_reason,
    compute_blob_frame_rect,
    compute_padded_blob_crop,
    crop_geometry_reject_reason,
    crop_looks_empty,
    load_landing_roi,
    save_landing_roi,
    significant_secondary_blob,
)


def test_landing_roi_clamped_inverts_and_keeps_order():
    roi = LandingRoi(top=0.9, bottom=0.2, left=0.95, right=0.1).clamped()
    assert 0.0 <= roi.top < roi.bottom <= 1.0
    assert 0.0 <= roi.left < roi.right <= 1.0
    assert roi.bottom - roi.top >= 0.05
    assert roi.right - roi.left >= 0.05


def test_landing_roi_clamped_out_of_range():
    roi = LandingRoi(top=-1.0, bottom=2.0, left=-0.5, right=1.5).clamped()
    assert roi.top == 0.0
    assert roi.bottom == 1.0
    assert roi.left == 0.0
    assert roi.right == 1.0


def test_load_save_landing_roi_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "landing_zone.json"
    monkeypatch.setattr(sc, "ROI_CONFIG_PATH", path)

    roi = LandingRoi(top=0.3, bottom=0.8, left=0.2, right=0.7)
    saved = save_landing_roi(roi, force=True)
    assert saved == path
    assert path.is_file()

    loaded = load_landing_roi()
    assert loaded.top == roi.clamped().top
    assert loaded.bottom == roi.clamped().bottom
    assert loaded.left == roi.clamped().left
    assert loaded.right == roi.clamped().right


def test_occupancy_empty_vs_empty_is_zero():
    empty = _to_gray_small(make_frame())
    full = LandingRoi(top=0.0, bottom=1.0, left=0.0, right=1.0)
    assert _occupancy_percent(empty, empty, roi=full) == 0.0


def test_occupancy_blob_reports_roughly_ten_percent():
    # Mild contrast keeps exposure-match from flooding the whole ROI, so the
    # measured blob % stays near the painted area (within a factor of 2).
    empty_rgb = make_frame(gray=200)
    blob_rgb = add_blob(empty_rgb, 80, 160, 112, 208, value=180)
    empty = _to_gray_small(empty_rgb)
    blob = _to_gray_small(blob_rgb)
    full = LandingRoi(top=0.0, bottom=1.0, left=0.0, right=1.0)
    occ = _occupancy_percent(empty, blob, roi=full)
    assert 5.0 <= occ <= 20.0


def test_occupancy_tiny_blob_below_min_reports_zero():
    empty_rgb = make_frame(gray=200)
    # 2×2 on the full-res frame shrinks below MIN_BLOB_PIXELS after downscale.
    tiny_rgb = add_blob(empty_rgb, 100, 102, 160, 162, value=180)
    empty = _to_gray_small(empty_rgb)
    tiny = _to_gray_small(tiny_rgb)
    full = LandingRoi(top=0.0, bottom=1.0, left=0.0, right=1.0)
    assert _occupancy_percent(empty, tiny, roi=full) == 0.0


def test_largest_blob_pixels_empty_mask():
    mask = np.zeros((8, 8), dtype=bool)
    assert _largest_blob_pixels(mask) == 0


def test_largest_blob_pixels_plus_shape():
    mask = np.zeros((10, 10), dtype=bool)
    mask[5, 4:7] = True
    mask[4:7, 5] = True
    assert int(mask.sum()) == 5
    assert _largest_blob_pixels(mask) == 5


def test_largest_blob_pixels_two_disjoint():
    mask = np.zeros((20, 20), dtype=bool)
    mask[2:4, 2:5] = True  # 6 pixels
    mask[10:15, 10:14] = True  # 20 pixels
    assert _largest_blob_pixels(mask) == 20


def test_largest_blob_pixels_fallback_matches_scipy(monkeypatch):
    """Pure-Python flood fill must match the scipy path on the same masks."""
    plus = np.zeros((10, 10), dtype=bool)
    plus[5, 4:7] = True
    plus[4:7, 5] = True
    disjoint = np.zeros((20, 20), dtype=bool)
    disjoint[2:4, 2:5] = True
    disjoint[10:15, 10:14] = True
    empty = np.zeros((8, 8), dtype=bool)

    if sc._ndimage is not None:
        assert _largest_blob_pixels(empty) == 0
        assert _largest_blob_pixels(plus) == 5
        assert _largest_blob_pixels(disjoint) == 20

    monkeypatch.setattr(sc, "_ndimage", None)
    assert _largest_blob_pixels(empty) == 0
    assert _largest_blob_pixels(plus) == 5
    assert _largest_blob_pixels(disjoint) == 20


def test_diff_stats_matches_wrapper_helpers():
    empty_rgb = make_frame(gray=200)
    blob_rgb = add_blob(empty_rgb, 80, 160, 112, 208, value=180)
    empty = _to_gray_small(empty_rgb)
    blob = _to_gray_small(blob_rgb)
    full = LandingRoi(top=0.0, bottom=1.0, left=0.0, right=1.0)
    stats = sc._diff_stats(empty, blob, roi=full)
    assert stats.occupancy == _occupancy_percent(empty, blob, roi=full)
    assert stats.changed_pct == sc._changed_pixel_percent(empty, blob, roi=full)


def test_normalize_frame_float_0_1():
    arr = np.full((4, 4, 3), 0.5, dtype=np.float32)
    out = _normalize_frame(arr)
    assert out.dtype == np.uint8
    assert out.shape == (4, 4, 3)
    assert int(out[0, 0, 0]) == 127 or int(out[0, 0, 0]) == 128


def test_normalize_frame_drops_alpha():
    arr = np.zeros((3, 3, 4), dtype=np.uint8)
    arr[..., :3] = 40
    arr[..., 3] = 255
    out = _normalize_frame(arr)
    assert out.shape == (3, 3, 3)
    assert out.dtype == np.uint8


def test_largest_blob_bbox_matches_mask():
    mask = np.zeros((20, 20), dtype=bool)
    mask[5:12, 3:9] = True
    assert _largest_blob_bbox(mask) == (3, 5, 9, 12)


def test_largest_blob_bbox_none_when_tiny():
    mask = np.zeros((20, 20), dtype=bool)
    mask[4, 4] = True
    assert _largest_blob_bbox(mask) is None


def test_pad_rect_clamps_to_frame():
    # pad_frac=0.25 on a 10px side → round(2.5)=2 under banker's rounding.
    assert _pad_rect(0, 0, 10, 10, pad_frac=0.25, frame_h=100, frame_w=100) == (
        0,
        0,
        12,
        12,
    )
    assert _pad_rect(90, 90, 100, 100, pad_frac=0.5, frame_h=100, frame_w=100) == (
        85,
        85,
        100,
        100,
    )


def test_crop_looks_empty_on_uniform_tray():
    blank = make_frame(gray=180)
    assert crop_looks_empty(blank) is True


def test_crop_looks_empty_false_on_structured_part():
    # High-contrast brick-like patch (silhouette + studs) should pass the gate.
    frame = make_frame(gray=200)
    part = add_blob(frame, 80, 160, 100, 200, value=30)
    part[100:105, 120:125] = 220
    part[100:105, 150:155] = 220
    part[130:135, 120:125] = 220
    part[130:135, 150:155] = 220
    crop = part[70:170, 90:210]
    assert crop_looks_empty(crop) is False


def test_crop_looks_empty_true_on_soft_ae_blotch():
    # Smooth local darkening — occupancy can fire, but the crop has no edges.
    empty = make_frame(gray=200).astype(np.float32)
    yy, xx = np.mgrid[0:240, 0:320]
    dip = 45.0 * np.exp(-((yy - 150.0) ** 2 + (xx - 160.0) ** 2) / (2.0 * 55.0**2))
    blotch = np.clip(empty - dip[:, :, None], 0, 255).astype(np.uint8)
    crop = blotch[100:200, 100:220]
    assert crop_looks_empty(crop) is True


def test_compute_blob_frame_rect_tight_around_blob():
    empty_rgb = make_frame(gray=200)
    blob_rgb = add_blob(empty_rgb, 110, 180, 100, 180, value=180)
    empty = _to_gray_small(empty_rgb)
    gray = _to_gray_small(blob_rgb)
    roi = LandingRoi(top=0.4, bottom=0.92, left=0.15, right=0.85)
    rect = compute_blob_frame_rect(empty, gray, roi, blob_rgb.shape[:2])
    assert rect is not None
    x0, y0, x1, y1 = rect
    # Allow a few px of COMPARE_SIZE round-trip drift.
    assert x0 <= 105 and x1 >= 175
    assert y0 <= 115 and y1 >= 175
    # Tight (unpadded) rect should be smaller than a padded API crop.
    crop, padded = compute_padded_blob_crop(blob_rgb, empty, gray, roi)
    assert (x1 - x0) <= (padded[2] - padded[0])
    assert (y1 - y0) <= (padded[3] - padded[1])
    assert crop.shape[0] == padded[3] - padded[1]


def test_compute_blob_frame_rect_none_when_empty():
    empty_rgb = make_frame(gray=200)
    empty = _to_gray_small(empty_rgb)
    roi = LandingRoi()
    assert compute_blob_frame_rect(empty, empty, roi, empty_rgb.shape[:2]) is None


def test_compute_padded_blob_crop_smaller_than_frame():
    # Mild contrast so exposure-match does not flood the whole ROI.
    empty_rgb = make_frame(gray=200)
    blob_rgb = add_blob(empty_rgb, 110, 180, 100, 180, value=180)
    empty = _to_gray_small(empty_rgb)
    gray = _to_gray_small(blob_rgb)
    roi = LandingRoi(top=0.4, bottom=0.92, left=0.15, right=0.85)
    crop, rect = compute_padded_blob_crop(blob_rgb, empty, gray, roi)
    x0, y0, x1, y1 = rect
    assert crop.shape[0] == y1 - y0
    assert crop.shape[1] == x1 - x0
    assert crop.shape[0] < blob_rgb.shape[0]
    assert crop.shape[1] < blob_rgb.shape[1]
    # Crop should cover the painted blob region.
    assert x0 <= 100 and x1 >= 180
    assert y0 <= 110 and y1 >= 180


def test_compute_padded_blob_crop_falls_back_to_roi_when_empty():
    empty_rgb = make_frame(gray=200)
    empty = _to_gray_small(empty_rgb)
    roi = LandingRoi(top=0.4, bottom=0.92, left=0.15, right=0.85)
    crop, rect = compute_padded_blob_crop(empty_rgb, empty, empty, roi)
    fh, fw = empty_rgb.shape[:2]
    expected = (
        int(fw * roi.left),
        int(fh * roi.top),
        int(fw * roi.right),
        int(fh * roi.bottom),
    )
    # bottom/right use max(y0+1, …) — allow 1px tolerance on edges
    assert abs(rect[0] - expected[0]) <= 1
    assert abs(rect[1] - expected[1]) <= 1
    assert crop.shape[0] == rect[3] - rect[1]
    assert crop.shape[1] == rect[2] - rect[0]


def test_compute_padded_blob_crop_no_fallback_returns_none():
    empty_rgb = make_frame(gray=200)
    empty = _to_gray_small(empty_rgb)
    roi = LandingRoi(top=0.4, bottom=0.92, left=0.15, right=0.85)
    crop, rect = compute_padded_blob_crop(
        empty_rgb, empty, empty, roi, allow_roi_fallback=False
    )
    assert crop is None and rect is None


def test_significant_secondary_blob_detects_two_parts():
    mask = np.zeros((40, 50), dtype=bool)
    mask[5:15, 5:15] = True  # 100 px
    mask[20:28, 30:40] = True  # 80 px → 0.8 of largest, well separated
    assert significant_secondary_blob(mask) is True
    mask2 = np.zeros((40, 50), dtype=bool)
    mask2[5:25, 5:25] = True  # large
    mask2[30:32, 40:42] = True  # tiny noise
    assert significant_secondary_blob(mask2) is False


def test_significant_secondary_blob_ignores_adjacent_shadow():
    """Part + touching shadow (overlapping/near bboxes) is one part, not two."""
    mask = np.zeros((40, 60), dtype=bool)
    mask[10:22, 10:30] = True  # part body
    mask[12:20, 28:40] = True  # abutting "shadow" (bboxes touch/overlap)
    # Force two components: clear a 1px gap then the shadow still near
    mask[:, 30] = False
    mask[12:20, 31:40] = True
    # Gap is 1px horizontally → absorbed as neighbor, not a second part.
    assert significant_secondary_blob(mask) is False


def test_blob_touches_roi_edge():
    assert blob_touches_roi_edge((0, 5, 10, 15), (40, 50)) is True
    assert blob_touches_roi_edge((5, 5, 15, 15), (40, 50)) is False
    assert blob_touches_roi_edge((40, 5, 50, 15), (40, 50)) is True


def test_crop_geometry_reject_reason():
    roi = (0, 0, 100, 100)
    assert crop_geometry_reject_reason((0, 0, 95, 95), roi) is not None  # area
    assert crop_geometry_reject_reason((0, 40, 80, 50), roi) is not None  # aspect
    assert crop_geometry_reject_reason((30, 30, 55, 55), roi) is None


def test_capture_reject_reason_multi_blob():
    empty_rgb = make_frame(gray=200)
    two = empty_rgb.copy()
    two = add_blob(two, 120, 160, 80, 120, value=150)
    two = add_blob(two, 120, 160, 200, 240, value=150)
    empty = _to_gray_small(empty_rgb)
    gray = _to_gray_small(two)
    roi = LandingRoi(top=0.4, bottom=0.92, left=0.15, right=0.85)
    _, changed = _roi_diff_mask(empty, gray, roi=roi)
    assert significant_secondary_blob(changed)
    reason = capture_reject_reason(
        empty, gray, roi, two.shape[:2], None, None
    )
    assert reason is not None
    assert "multiple" in reason


def test_capture_reject_reason_allow_edge_contact():
    empty_rgb = make_frame(gray=200)
    # Touch left of default-ish ROI (left=0.15 → x≈48 on w=320).
    edge_rgb = add_blob(empty_rgb, 120, 170, 48, 90, value=40)
    empty = _to_gray_small(empty_rgb)
    gray = _to_gray_small(edge_rgb)
    roi = LandingRoi(top=0.4, bottom=0.92, left=0.15, right=0.85)
    crop, rect = compute_padded_blob_crop(
        edge_rgb, empty, gray, roi, allow_roi_fallback=False
    )
    blocked = capture_reject_reason(
        empty, gray, roi, edge_rgb.shape[:2], crop, rect
    )
    assert blocked is not None
    assert "edge" in blocked
    allowed = capture_reject_reason(
        empty,
        gray,
        roi,
        edge_rgb.shape[:2],
        crop,
        rect,
        allow_edge_contact=True,
    )
    assert allowed is None


def test_expand_outside_roi_crop_includes_part_beyond_zone():
    """Blob half outside the ROI should expand the crop past the green edge."""
    empty_rgb = make_frame(gray=200)
    roi = LandingRoi(top=0.4, bottom=0.92, left=0.15, right=0.85)
    # Part straddles left ROI edge (x=48): half outside, half inside.
    edge_rgb = add_blob(empty_rgb, 120, 180, 20, 80, value=40)
    empty = _to_gray_small(empty_rgb)
    gray = _to_gray_small(edge_rgb)
    clipped, clip_rect = compute_padded_blob_crop(
        edge_rgb, empty, gray, roi, allow_roi_fallback=False
    )
    expanded, exp_rect = compute_padded_blob_crop(
        edge_rgb,
        empty,
        gray,
        roi,
        allow_roi_fallback=False,
        expand_outside_roi=True,
    )
    assert clipped is not None and clip_rect is not None
    assert expanded is not None and exp_rect is not None
    # Expanded crop should start further left (outside the zone).
    assert exp_rect[0] < clip_rect[0]
    assert expanded.shape[1] >= clipped.shape[1]

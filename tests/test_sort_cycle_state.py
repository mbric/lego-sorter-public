"""SortCycle state machine with fakes — no network, camera, or robot."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
from conftest import add_blob, make_frame

from feeder.http_client import ConveyorError
from orchestrator import sort_cycle as sc
from orchestrator.sort_cycle import (
    IdentifyOutcome,
    LandingRoi,
    SortCycle,
    SortState,
)


class FakeFeeder:
    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def advance(self):
        self.calls.append("advance")


class RaisingStartFeeder(FakeFeeder):
    def start(self):
        raise ConveyorError("belt offline")


def _patch_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "REFERENCE_PATH", tmp_path / "empty_tray.jpg")
    monkeypatch.setattr(sc, "CAPTURES_DIR", tmp_path / "captures")
    monkeypatch.setattr(sc, "WAIT_CLEAR_STABLE_S", 0.0)
    monkeypatch.setattr(sc, "MATCH_RETRY_DELAY_S", 0.0)


def _frame_kwargs(**extra):
    base = dict(occupied_frames=1, empty_frames=1, settle_s=0.0)
    base.update(extra)
    return base


def _make_cycle(feeder=None, identify_fn=None, **kwargs):
    return SortCycle(
        feeder=feeder or FakeFeeder(),
        identify_fn=identify_fn,
        occupied_frames=1,
        empty_frames=1,
        settle_s=0.0,
        **kwargs,
    )


def _await_match(cycle, frame, *, timeout_s: float = 2.0, **kwargs):
    """Poll on_frame until MATCHING finishes (identify runs on a worker)."""
    deadline = time.monotonic() + timeout_s
    update = None
    while time.monotonic() < deadline:
        update = cycle.on_frame(frame, **_frame_kwargs(**kwargs))
        if update.state != SortState.MATCHING:
            return update
        time.sleep(0.01)
    assert update is not None
    return update


def test_idle_on_frame_leaves_feeder(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder)
    cycle.set_empty_reference(make_frame(), save=False)

    update = cycle.on_frame(make_frame(), **_frame_kwargs())
    assert update.state == SortState.IDLE
    assert feeder.calls == []


def test_happy_path(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    identify_calls = []

    def identify_fn(still):
        identify_calls.append(still)
        return IdentifyOutcome(
            candidate=SimpleNamespace(
                category="Brick",
                item_type="part",
            ),
            color=SimpleNamespace(key="red"),
        )

    cycle = _make_cycle(feeder=feeder, identify_fn=identify_fn)
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)

    started = cycle.start_loop()
    assert started.state == SortState.ADVANCING
    assert feeder.calls == ["start"]

    # Empty frames keep ADVANCING.
    for _ in range(3):
        u = cycle.on_frame(empty, **_frame_kwargs())
        assert u.state == SortState.ADVANCING

    # Dark blob in the default landing ROI → STOP → SETTLING.
    # Full-res ROI ≈ y[96:221], x[48:272] on 240×320; paint well inside.
    # Mild contrast + compact blob (value=30 floods ROI after exposure-match).
    blob = add_blob(empty, 120, 170, 110, 190, value=150)
    u = cycle.on_frame(blob, **_frame_kwargs())
    assert u.state == SortState.SETTLING
    assert "stop" in feeder.calls

    # First settling tick seeds _prev_gray (reports motion); second is still.
    u = cycle.on_frame(blob, **_frame_kwargs())
    assert u.state == SortState.SETTLING
    u = cycle.on_frame(blob, **_frame_kwargs())
    assert u.state == SortState.CAPTURED
    assert u.captured is True
    assert cycle.last_still_path is not None
    assert cycle.last_still_path.is_file()
    assert cycle.last_still_path.parent == tmp_path / "captures"

    # Auto-match → MATCHING (background identify) → WAIT_CLEAR.
    u = cycle.on_frame(blob, **_frame_kwargs(auto_match=True))
    assert u.state == SortState.MATCHING
    u = _await_match(cycle, blob, auto_match=True)
    assert u.state == SortState.WAIT_CLEAR
    assert u.matched is True
    assert u.virtual_bin == "red"
    assert len(identify_calls) == 1
    # Capture stores a padded blob/ROI crop for the API path.
    assert cycle.last_api_crop is not None
    assert cycle.last_crop_rect is not None
    assert cycle.last_api_crop.shape[0] <= blob.shape[0]
    assert cycle.last_api_crop.shape[1] <= blob.shape[1]

    # Empty again (stable, removal vs capture) → ADVANCING + second start.
    # First empty tick seeds motion baseline; second is still + clear.
    u = cycle.on_frame(empty, **_frame_kwargs(auto_match=True))
    # May still be WAIT_CLEAR on the first empty frame (motion seed).
    if u.state == SortState.WAIT_CLEAR:
        u = cycle.on_frame(empty, **_frame_kwargs(auto_match=True))
    assert u.state == SortState.ADVANCING
    assert feeder.calls.count("start") >= 2


def test_identify_retry(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    attempts = {"n": 0}

    def identify_fn(_still):
        attempts["n"] += 1
        return IdentifyOutcome(candidate=None, error="No shape match")

    cycle = _make_cycle(feeder=feeder, identify_fn=identify_fn)
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    # Large enough that a failed match goes to WaitClear (not ghost-resume).
    blob = add_blob(empty, 115, 185, 95, 205, value=150)
    cycle.on_frame(blob, **_frame_kwargs())  # → SETTLING
    cycle.on_frame(blob, **_frame_kwargs())  # seed motion
    u = cycle.on_frame(blob, **_frame_kwargs())  # → CAPTURED
    assert u.state == SortState.CAPTURED

    u = cycle.on_frame(blob, **_frame_kwargs(auto_match=True))  # → MATCHING
    assert u.state == SortState.MATCHING
    u = _await_match(cycle, blob, auto_match=True)  # fail → SETTLING
    assert u.state == SortState.SETTLING
    assert cycle._match_retries_left == 0
    assert attempts["n"] == 1

    # Re-settle and capture for the second attempt.
    cycle.on_frame(blob, **_frame_kwargs(auto_match=True))  # seed motion
    u = cycle.on_frame(blob, **_frame_kwargs(auto_match=True))  # → CAPTURED
    assert u.state == SortState.CAPTURED
    u = cycle.on_frame(blob, **_frame_kwargs(auto_match=True))  # → MATCHING
    assert u.state == SortState.MATCHING
    u = _await_match(cycle, blob, auto_match=True)  # second fail → WAIT_CLEAR
    assert u.state == SortState.WAIT_CLEAR
    assert attempts["n"] == 2
    assert u.matched is True  # matching finished (with error)


def test_matching_does_not_block_stream(monkeypatch, tmp_path):
    """Slow identify_fn must not stall on_frame — intermediate MATCHING updates."""
    _patch_paths(monkeypatch, tmp_path)
    started = threading.Event()
    release = threading.Event()

    def identify_fn(_still):
        started.set()
        assert release.wait(timeout=2.0)
        return IdentifyOutcome(
            candidate=SimpleNamespace(category="Brick", item_type="part"),
            color=SimpleNamespace(key="blue"),
        )

    cycle = _make_cycle(identify_fn=identify_fn)
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()
    # Mild contrast + compact blob (value=30 floods ROI after exposure-match).
    blob = add_blob(empty, 120, 170, 110, 190, value=150)
    cycle.on_frame(blob, **_frame_kwargs())
    cycle.on_frame(blob, **_frame_kwargs())
    assert cycle.on_frame(blob, **_frame_kwargs()).state == SortState.CAPTURED

    t0 = time.monotonic()
    u = cycle.on_frame(blob, **_frame_kwargs(auto_match=True))
    assert u.state == SortState.MATCHING
    # Worker may not have started yet; poll until it has, without finishing.
    while not started.is_set() and time.monotonic() - t0 < 2.0:
        u = cycle.on_frame(blob, **_frame_kwargs(auto_match=True))
        assert u.state == SortState.MATCHING
        time.sleep(0.01)
    assert started.is_set()
    # Still matching while the worker is blocked — on_frame stays fast.
    t1 = time.monotonic()
    u = cycle.on_frame(blob, **_frame_kwargs(auto_match=True))
    assert time.monotonic() - t1 < 0.2
    assert u.state == SortState.MATCHING

    release.set()
    u = _await_match(cycle, blob, auto_match=True)
    assert u.state == SortState.WAIT_CLEAR
    assert u.virtual_bin == "blue"


def test_settle_aborts_when_blob_gone(monkeypatch, tmp_path):
    """Hand/shadow trips STOP then leaves — resume instead of empty capture."""
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder)
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    # Mild contrast + compact blob (value=30 floods ROI after exposure-match).
    blob = add_blob(empty, 120, 170, 110, 190, value=150)
    u = cycle.on_frame(blob, **_frame_kwargs())
    assert u.state == SortState.SETTLING
    # Blob disappears before settle completes → false trigger, resume advancing.
    u = cycle.on_frame(empty, **_frame_kwargs())
    assert u.state == SortState.ADVANCING
    assert "False trigger" in u.status
    assert feeder.calls.count("start") >= 2
    assert cycle.last_still is None or cycle.state == SortState.ADVANCING


def test_wait_clear_unsticks_ghost_empty_capture(monkeypatch, tmp_path):
    """If the frozen still looks empty vs ref, don't demand removal forever."""
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.8)
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    # Manually wedge into WaitClear with a ghost: high recorded capture
    # occupancy, but still gray matches empty ref.
    cycle.state = SortState.WAIT_CLEAR
    cycle._capture_occupancy = 10.0
    cycle._last_still_gray = sc._to_gray_small(empty)
    cycle._empty_ref = sc._to_gray_small(empty)
    cycle._wait_peak_occupancy = 10.0
    cycle.last_still = empty

    starts_before = feeder.calls.count("start")
    u = cycle.on_frame(empty, **_frame_kwargs(empty_frames=1))
    # First empty frame seeds motion; second is stable + clear.
    if u.state == SortState.WAIT_CLEAR:
        u = cycle.on_frame(empty, **_frame_kwargs(empty_frames=1))
    assert u.state == SortState.ADVANCING
    assert feeder.calls.count("start") == starts_before + 1


def test_empty_texture_crop_skips_capture(monkeypatch, tmp_path):
    """Uniform AE blotch (high blob-%, no texture) must not capture/API."""
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.5)
    cycle.set_roi(
        LandingRoi(top=0.40, bottom=0.92, left=0.15, right=0.85), persist=False
    )
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    # Soft local darkening (AE-like) — blob-% fires, interior stays textureless.
    blotch = empty.astype(np.float32)
    yy, xx = np.mgrid[0:240, 0:320]
    dip = 45.0 * np.exp(-((yy - 150.0) ** 2 + (xx - 160.0) ** 2) / (2.0 * 55.0**2))
    blotch = np.clip(blotch - dip[:, :, None], 0, 255).astype(np.uint8)
    u = cycle.on_frame(blotch, **_frame_kwargs())
    assert u.state == SortState.SETTLING
    cycle.on_frame(blotch, **_frame_kwargs())  # seed motion
    u = cycle.on_frame(blotch, **_frame_kwargs())
    # Content / structure gates should reject before CAPTURED (empty or multi).
    assert u.state != SortState.CAPTURED
    assert u.state in (SortState.ADVANCING, SortState.SETTLING)
    assert (
        "Empty crop" in u.status
        or "false trigger" in u.status.lower()
        or "rejected" in u.status.lower()
        or "multiple" in u.status.lower()
    )


def test_multi_part_capture_rejected_resettles(monkeypatch, tmp_path):
    """Two large blobs must not capture — re-settle instead."""
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.5)
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    two = empty.copy()
    two = add_blob(two, 120, 165, 70, 120, value=150)
    two = add_blob(two, 120, 165, 190, 240, value=150)
    u = cycle.on_frame(two, **_frame_kwargs())
    assert u.state == SortState.SETTLING
    cycle.on_frame(two, **_frame_kwargs())
    u = cycle.on_frame(two, **_frame_kwargs())
    assert u.state == SortState.SETTLING
    assert "multiple" in u.status.lower() or "rejected" in u.status.lower()
    assert cycle._capture_gate_rejects >= 1


def test_capture_give_up_waits_for_clear_when_still_occupied(monkeypatch, tmp_path):
    """Unphotoable flood must not belt-thrash — WaitClear until zone empties."""
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sc, "CAPTURE_GATE_MAX_REJECTS", 2)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.5)
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    # Fill most of the landing zone — padded crop exceeds max area gate.
    flood = add_blob(empty, 100, 220, 50, 270, value=40)
    u = None
    for _ in range(12):
        u = cycle.on_frame(flood, **_frame_kwargs())
        if u.state == SortState.WAIT_CLEAR:
            break
    assert u is not None
    assert u.state == SortState.WAIT_CLEAR
    assert "gave up" in u.status.lower() or "clear the zone" in u.status.lower()
    assert cycle._capture_give_up_wait is True

    # Still flooded → stay parked (no resume thrash).
    starts_before = feeder.calls.count("start")
    u = cycle.on_frame(flood, **_frame_kwargs(empty_frames=1))
    assert u.state == SortState.WAIT_CLEAR
    assert feeder.calls.count("start") == starts_before

    # Zone clears → resume advancing.
    clear = make_frame(gray=200)
    cycle.on_frame(clear, **_frame_kwargs(empty_frames=1))
    u = cycle.on_frame(clear, **_frame_kwargs(empty_frames=1))
    assert u.state == SortState.ADVANCING
    assert feeder.calls.count("start") == starts_before + 1
    assert cycle._capture_give_up_wait is False


def test_edge_touch_max_rejects_captures_with_override(monkeypatch, tmp_path):
    """Parked part on the ROI edge should capture after max edge rejects."""
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sc, "CAPTURE_GATE_MAX_REJECTS", 2)
    monkeypatch.setattr(sc, "MIN_EDGE_OVERRIDE_OCC", 0.5)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.5)
    cycle.set_roi(
        LandingRoi(top=0.40, bottom=0.92, left=0.15, right=0.85), persist=False
    )
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    # Blob flush against the left ROI edge (x≈48 on a 320-wide frame).
    edge = add_blob(empty, 120, 170, 48, 90, value=40)
    u = None
    for _ in range(16):
        u = cycle.on_frame(edge, **_frame_kwargs())
        if u.state == SortState.CAPTURED:
            break
    assert u is not None
    assert u.state == SortState.CAPTURED
    assert "edge override" in u.status.lower()
    assert cycle._capture_give_up_wait is False


def test_tiny_edge_fringe_is_ignored_not_captured(monkeypatch, tmp_path):
    """Skinny left-frame feeder fringe must resume, not edge-override to API."""
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sc, "MIN_EDGE_OVERRIDE_OCC", 2.5)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.5)
    cycle.set_roi(
        LandingRoi(top=0.40, bottom=0.92, left=0.05, right=0.85), persist=False
    )
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    # Skinny strip on the extreme left of the frame (feeder housing).
    fringe = add_blob(empty, 100, 200, 0, 18, value=80)
    u = None
    for _ in range(8):
        u = cycle.on_frame(fringe, **_frame_kwargs())
        if u.state == SortState.ADVANCING and "fringe" in u.status.lower():
            break
        if u.state == SortState.CAPTURED:
            break
    assert u is not None
    assert u.state == SortState.ADVANCING
    assert "fringe" in u.status.lower() or "ignored" in u.status.lower()


def test_capture_give_up_still_blocks_ghost_shortcut(monkeypatch, tmp_path):
    """Flood give-up WaitClear must not ghost-resume while zone is flooded."""
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sc, "GHOST_NO_MATCH_MAX_OCC", 20.0)
    monkeypatch.setattr(sc, "WAIT_CLEAR_STABLE_S", 0.0)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.5)
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    # Full-zone flood — live-blob recovery should also fail geometry.
    flood = add_blob(empty, 100, 220, 50, 270, value=40)
    gray = np.mean(flood.astype(np.float32), axis=2)
    u = cycle._wait_clear_after_capture_give_up(
        gray, occupied_score=80.0, reason="crop too large (143% of zone)", rejects=4
    )
    assert u.state == SortState.WAIT_CLEAR
    assert cycle._capture_give_up_wait is True

    starts_before = feeder.calls.count("start")
    for _ in range(6):
        u = cycle.on_frame(flood, **_frame_kwargs(empty_frames=1))
        assert u.state == SortState.WAIT_CLEAR
        assert "ghost" not in u.status.lower()
    assert feeder.calls.count("start") == starts_before


def test_give_up_wait_captures_stable_live_blob(monkeypatch, tmp_path):
    """After give-up, a stable amber-sized part should be captured, not parked forever."""
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sc, "WAIT_CLEAR_STABLE_S", 0.0)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.5)
    cycle.set_roi(
        LandingRoi(top=0.40, bottom=0.92, left=0.15, right=0.85), persist=False
    )
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    part = add_blob(empty, 120, 170, 110, 190, value=40)
    gray = np.mean(part.astype(np.float32), axis=2)
    cycle._wait_clear_after_capture_give_up(
        gray, occupied_score=5.0, reason="crop too large (143% of zone)", rejects=4
    )
    assert cycle._capture_give_up_wait is True

    u = None
    for _ in range(6):
        u = cycle.on_frame(part, **_frame_kwargs())
        if u.state == SortState.CAPTURED:
            break
    assert u is not None
    assert u.state == SortState.CAPTURED
    assert cycle._capture_give_up_wait is False


def test_no_match_weak_blob_resumes_without_wait_clear(monkeypatch, tmp_path):
    """Brickognize miss on a small structured blob must not wedge WaitClear."""
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sc, "MATCH_MAX_RETRIES", 0)

    def identify_fn(_still):
        return IdentifyOutcome(candidate=None, error="No shape match")

    feeder = FakeFeeder()
    cycle = _make_cycle(
        feeder=feeder,
        identify_fn=identify_fn,
        occupied_threshold=0.5,
    )
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    # Small dark patch: enough texture to pass content gate, weak overall occ.
    blob = add_blob(empty, 120, 150, 140, 180, value=40)
    cycle.on_frame(blob, **_frame_kwargs())  # → SETTLING
    cycle.on_frame(blob, **_frame_kwargs())  # seed
    u = cycle.on_frame(blob, **_frame_kwargs())  # → CAPTURED
    assert u.state == SortState.CAPTURED

    u = cycle.on_frame(blob, **_frame_kwargs(auto_match=True))  # → MATCHING
    assert u.state == SortState.MATCHING
    u = _await_match(cycle, blob, auto_match=True)
    assert u.state == SortState.ADVANCING
    assert (
        "No shape match" in u.status
        or "noise" in u.status.lower()
        or "empty" in u.status.lower()
    )


def test_wait_clear_ghost_escape_when_scene_unchanged(monkeypatch, tmp_path):
    """Failed ID + live≈capture + weak occ → absorb and resume."""
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.8)
    empty = make_frame(gray=200)
    # Mild residual "noise" blob still under GHOST_NO_MATCH_MAX_OCC.
    noisy = add_blob(empty, 180, 210, 220, 280, value=185)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    cycle.state = SortState.WAIT_CLEAR
    cycle._identify_failed = True
    cycle._capture_occupancy = 4.5
    cycle._last_still_gray = sc._to_gray_small(noisy)
    cycle._empty_ref = sc._to_gray_small(empty)
    cycle._wait_peak_occupancy = 4.5
    cycle.last_still = noisy

    starts_before = feeder.calls.count("start")
    u = cycle.on_frame(noisy, **_frame_kwargs(empty_frames=1))
    if u.state == SortState.WAIT_CLEAR:
        u = cycle.on_frame(noisy, **_frame_kwargs(empty_frames=1))
    assert u.state == SortState.ADVANCING
    assert feeder.calls.count("start") == starts_before + 1
    assert "ghost" in u.status.lower() or "resuming" in u.status.lower()


def test_wait_clear_ae_ghost_after_removal_resumes(monkeypatch, tmp_path):
    """Removal ok + soft AE blotch (no structure) must not park forever."""
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.8)
    empty = make_frame(gray=200)
    captured = add_blob(empty, 120, 170, 110, 190, value=150)
    # Soft AE dip — high occupancy vs empty ref, but no part edges.
    blotch = empty.astype(np.float32)
    yy, xx = np.mgrid[0:240, 0:320]
    dip = 55.0 * np.exp(
        -((yy - 130.0) ** 2 + (xx - 240.0) ** 2) / (2.0 * 45.0**2)
    )
    blotch = np.clip(blotch - dip[:, :, None], 0, 255).astype(np.uint8)

    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()
    cycle.state = SortState.WAIT_CLEAR
    cycle._identify_failed = False
    cycle._capture_occupancy = 3.5
    cycle._last_still_gray = sc._to_gray_small(captured)
    cycle._empty_ref = sc._to_gray_small(empty)
    cycle._wait_peak_occupancy = 22.0
    cycle.last_still = captured
    cycle.last_api_crop = captured[120:170, 110:190].copy()

    starts_before = feeder.calls.count("start")
    u = cycle.on_frame(blotch, **_frame_kwargs(empty_frames=1))
    if u.state == SortState.WAIT_CLEAR:
        u = cycle.on_frame(blotch, **_frame_kwargs(empty_frames=1))
    assert u.state == SortState.ADVANCING
    assert feeder.calls.count("start") == starts_before + 1
    assert "ghost" in u.status.lower() or "resuming" in u.status.lower()


def test_wait_clear_keeps_next_structured_part(monkeypatch, tmp_path):
    """Next real brick after removal must not be treated as an AE ghost."""
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.8)
    empty = make_frame(gray=200)
    captured = add_blob(empty, 120, 170, 70, 120, value=150)
    next_part = add_blob(empty, 120, 175, 200, 255, value=140)

    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()
    cycle.state = SortState.WAIT_CLEAR
    cycle._identify_failed = False
    cycle._capture_occupancy = 3.5
    cycle._last_still_gray = sc._to_gray_small(captured)
    cycle._empty_ref = sc._to_gray_small(empty)
    cycle._wait_peak_occupancy = 22.0
    cycle.last_still = captured
    cycle.last_api_crop = captured[120:170, 70:120].copy()

    starts_before = feeder.calls.count("start")
    u = cycle.on_frame(next_part, **_frame_kwargs(empty_frames=1))
    if u.state == SortState.WAIT_CLEAR:
        u = cycle.on_frame(next_part, **_frame_kwargs(empty_frames=1))
    assert u.state == SortState.WAIT_CLEAR
    assert feeder.calls.count("start") == starts_before


def test_roi_left_feeder_fringe_does_not_hold_settling(monkeypatch, tmp_path):
    """Feeder tip on the green-zone left edge must resume, not stick Settling."""
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder, occupied_threshold=0.5)
    cycle.set_roi(
        LandingRoi(top=0.28, bottom=0.98, left=0.18, right=0.92), persist=False
    )
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()

    # Mild skinny strip at ROI left (~1–2% occ, like the live feeder tip).
    fringe = add_blob(empty, 130, 175, 55, 72, value=165)
    u = None
    for _ in range(10):
        u = cycle.on_frame(fringe, **_frame_kwargs())
        if u.state == SortState.CAPTURED:
            break
    assert u is not None
    # Absorb-on-advance / fringe-ignore must keep the belt moving — never
    # CAPTURED / WaitClear on feeder housing.
    assert u.state == SortState.ADVANCING
    assert cycle.last_still is None


def test_feeder_failure_on_start(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    cycle = _make_cycle(feeder=RaisingStartFeeder())
    cycle.set_empty_reference(make_frame(), save=False)
    update = cycle.start_loop()
    assert update.state == SortState.IDLE
    assert update.error is not None
    assert "belt offline" in update.error


def test_force_clear(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    feeder = FakeFeeder()
    cycle = _make_cycle(feeder=feeder)
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()
    assert feeder.calls == ["start"]

    # Drive into WAIT_CLEAR without matching.
    # Mild contrast + compact blob (value=30 floods ROI after exposure-match).
    blob = add_blob(empty, 120, 170, 110, 190, value=150)
    cycle.on_frame(blob, **_frame_kwargs())
    cycle.on_frame(blob, **_frame_kwargs())
    u = cycle.on_frame(blob, **_frame_kwargs())
    assert u.state == SortState.CAPTURED
    u = cycle.on_frame(blob, **_frame_kwargs(auto_match=False))
    assert u.state == SortState.WAIT_CLEAR

    starts_before = feeder.calls.count("start")
    cleared = cycle.force_clear(make_frame(gray=210))
    assert cleared.state == SortState.ADVANCING
    assert feeder.calls.count("start") == starts_before + 1
    assert sc.REFERENCE_PATH.is_file()


def test_event_fn_emits_session_start_capture_and_reject(monkeypatch, tmp_path):
    """SortCycle should fire high-signal events through event_fn."""
    _patch_paths(monkeypatch, tmp_path)
    events: list[tuple[str, dict]] = []

    def event_fn(name: str, fields: dict) -> None:
        events.append((name, fields))

    feeder = FakeFeeder()
    cycle = _make_cycle(
        feeder=feeder,
        event_fn=event_fn,
        occupied_threshold=0.5,
    )
    empty = make_frame(gray=200)
    cycle.set_empty_reference(empty, save=False)
    cycle.start_loop()
    assert any(name == "session_start" for name, _ in events)

    # Multi-part → capture_reject
    two = empty.copy()
    two = add_blob(two, 120, 165, 70, 120, value=150)
    two = add_blob(two, 120, 165, 190, 240, value=150)
    cycle.on_frame(two, **_frame_kwargs())
    cycle.on_frame(two, **_frame_kwargs())
    cycle.on_frame(two, **_frame_kwargs())
    reject_names = [name for name, _ in events if name == "capture_reject"]
    assert reject_names

    # Fresh loop + single blob → capture
    events.clear()
    cycle.stop_loop()
    cycle.start_loop()
    blob = add_blob(empty, 120, 170, 110, 190, value=150)
    captured = False
    for _ in range(4):
        u = cycle.on_frame(blob, **_frame_kwargs())
        if u.state == SortState.CAPTURED or any(n == "capture" for n, _ in events):
            captured = True
            break
    assert captured
    capture_events = [fields for name, fields in events if name == "capture"]
    assert capture_events
    assert "still" in capture_events[0]
    assert "crop_rect" in capture_events[0]

    cycle.stop_loop()
    assert any(name == "loop_stop" for name, _ in events)

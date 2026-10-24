"""SessionCatalog persistence — all under tmp_path, never real data/."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from demo_brickognize import session_catalog as session_catalog_mod
from demo_brickognize.brickognize_client import Candidate
from demo_brickognize.color_detect import ColorDetection
from demo_brickognize.parts_catalog import PartsCatalog
from demo_brickognize.session_catalog import (
    EVENTS_LOG_NAME,
    SCAN_DISPLAY_FIELDS,
    SessionCatalog,
)


def _isolate_parts(monkeypatch, tmp_path):
    parts = PartsCatalog(path=tmp_path / "parts_catalog" / "catalog.json")
    monkeypatch.setattr(session_catalog_mod, "PARTS", parts)
    return parts


def _candidate(**overrides) -> Candidate:
    base = dict(
        id="3001",
        name="Brick 2 x 4",
        item_type="part",
        category="Brick",
        score=0.91,
        img_url="",
        external_links=(),
    )
    base.update(overrides)
    return Candidate(**base)


def _color(key="red", name="Bright Red") -> ColorDetection:
    return ColorDetection(name=name, key=key, confidence=0.8, rgb=(201, 26, 9), sample_pixels=100)


def test_log_scan_persists_and_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    _isolate_parts(monkeypatch, tmp_path)
    catalog = SessionCatalog(base_dir=tmp_path)
    sid = catalog.current_session_id
    assert not (tmp_path / sid).exists()

    catalog.log_scan(_candidate(), _color(), source="desk")
    session_dir = tmp_path / sid
    assert session_dir.is_dir()
    scans_path = session_dir / "scans.csv"
    assert scans_path.is_file()
    text = scans_path.read_text(encoding="utf-8")
    assert "part_id" in text.splitlines()[0]
    assert "3001" in text

    scans = catalog.read_scans(sid)
    assert len(scans) == 1
    assert scans[0].part_id == "3001"
    assert scans[0].color_key == "red"
    assert catalog._current_persisted is True


def test_inventory_totals_aggregates(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    _isolate_parts(monkeypatch, tmp_path)
    catalog = SessionCatalog(base_dir=tmp_path)
    sid = catalog.current_session_id
    cand = _candidate()
    color = _color()
    catalog.log_scan(cand, color, source="desk")
    catalog.log_scan(cand, color, source="desk")

    rows = catalog.inventory_totals(sid)
    assert len(rows) == 1
    assert rows[0][0] == "3001"
    assert rows[0][3] == 2


def test_purge_empty_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    _isolate_parts(monkeypatch, tmp_path)
    catalog = SessionCatalog(base_dir=tmp_path)
    empty = tmp_path / "empty_session"
    empty.mkdir()
    (empty / "session.json").write_text(
        '{"session_id":"empty_session","started_at":"x","label":"Empty"}\n',
        encoding="utf-8",
    )
    (empty / "scans.csv").write_text("part_id\n", encoding="utf-8")

    removed = catalog.purge_empty_sessions()
    assert removed >= 1
    assert not empty.exists()


def test_append_event_creates_events_log(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    _isolate_parts(monkeypatch, tmp_path)
    catalog = SessionCatalog(base_dir=tmp_path / "sessions")
    sid = catalog.current_session_id
    assert not (tmp_path / "sessions" / sid).exists()

    catalog.append_event(
        "capture_reject",
        state="Settling",
        reason="multiple parts in zone",
        occ=9.04,
        rejects=1,
    )

    log_path = tmp_path / "sessions" / sid / EVENTS_LOG_NAME
    assert log_path.is_file()
    line = log_path.read_text(encoding="utf-8").strip()
    assert "event=capture_reject" in line
    assert "state=Settling" in line
    assert "occ=9.04" in line
    assert "rejects=1" in line
    assert "multiple parts in zone" in line

    text = catalog.read_events_text(sid)
    assert "event=capture_reject" in text
    assert "multiple parts in zone" in text
    assert "no events yet" in catalog.read_events_text("missing-session-id")


def test_catalog_overview_and_picker_mark_live_session(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    _isolate_parts(monkeypatch, tmp_path)
    catalog = SessionCatalog(base_dir=tmp_path)
    sid = catalog.current_session_id

    empty_html = catalog.catalog_overview_html(sid, cache_only=True)
    assert "Live" in empty_html

    catalog.log_scan(_candidate(), _color(), source="desk")
    html = catalog.catalog_overview_html(sid, cache_only=True)
    assert "Live" in html
    assert "Scans" in html
    assert "Est. value" in html

    choices = catalog.session_picker_choices()
    assert choices[0][1] == session_catalog_mod.LIVE_SESSION_PICKER_VALUE
    assert choices[0][0].startswith("Live ·")
    assert catalog.default_picker_value() == session_catalog_mod.LIVE_SESSION_PICKER_VALUE
    assert catalog.resolve_picker_session(None) == sid
    assert catalog.resolve_picker_session(session_catalog_mod.LIVE_SESSION_PICKER_VALUE) == sid
    # Past sessions only appear after another session exists.
    assert all(val != sid for _, val in choices[1:])


def test_export_zip_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    _isolate_parts(monkeypatch, tmp_path)
    exports = tmp_path / "exports"
    monkeypatch.setattr(session_catalog_mod, "EXPORTS_DIR", exports)

    catalog = SessionCatalog(base_dir=tmp_path / "sessions")
    sid = catalog.current_session_id
    catalog.log_scan(_candidate(), _color(), source="desk")
    catalog.append_event("loop_stop", state="Idle")

    zip_path = catalog.export_zip(sid)
    assert zip_path is not None
    path = Path(zip_path)
    assert path.is_file()
    assert path.parent == exports

    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        readme = zf.read("README.txt").decode("utf-8")
    assert "scans.csv" in names
    assert "inventory.csv" in names
    assert "README.txt" in names
    assert EVENTS_LOG_NAME in names
    assert EVENTS_LOG_NAME in readme


def test_valuation_uses_manual_parts_catalog_price(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    parts = _isolate_parts(monkeypatch, tmp_path)
    catalog = SessionCatalog(base_dir=tmp_path)
    sid = catalog.current_session_id
    catalog.log_scan(_candidate(), _color(), source="desk")
    catalog.log_scan(_candidate(), _color(), source="desk")
    parts.set_unit_price("3001", "red", 0.5)

    valuation = catalog.inventory_valuation(sid)
    assert valuation.configured is True
    assert valuation.guide_type == "manual"
    assert valuation.priced_combos == 1
    assert valuation.total_value == 1.0  # 2 × 0.50
    assert valuation.rows[0][4] == 0.5
    assert valuation.rows[0][5] == 1.0


def test_valuation_unpriced_without_manual_or_bricklink(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    _isolate_parts(monkeypatch, tmp_path)
    catalog = SessionCatalog(base_dir=tmp_path)
    sid = catalog.current_session_id
    catalog.log_scan(_candidate(), _color(), source="desk")

    valuation = catalog.inventory_valuation(sid)
    assert valuation.configured is True
    assert valuation.priced_combos == 0
    assert valuation.unpriced_combos == 1
    assert valuation.rows[0][4] is None
    assert valuation.rows[0][5] is None


def test_log_scan_saves_capture_and_resolves_path(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    _isolate_parts(monkeypatch, tmp_path)
    catalog = SessionCatalog(base_dir=tmp_path)
    sid = catalog.current_session_id
    crop = np.full((40, 50, 3), 40, dtype=np.uint8)
    catalog.log_scan(_candidate(), _color(), source="feed", capture_image=crop)

    scans = catalog.read_scans(sid)
    assert scans[0].image_file.startswith("captures/")
    path = catalog.scan_capture_path(sid, 0)
    assert path is not None and path.is_file()
    assert Image.open(path).size == (50, 40)
    # Display table omits the path column.
    display = catalog.scans_display_table(sid)
    assert len(display[0]) == len(SCAN_DISPLAY_FIELDS)
    assert catalog.capture_path(sid, "../etc/passwd") is None


def test_read_scans_cache_hit_returns_same_list(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    _isolate_parts(monkeypatch, tmp_path)
    catalog = SessionCatalog(base_dir=tmp_path)
    sid = catalog.current_session_id
    catalog.log_scan(_candidate(), _color(), source="desk")

    first = catalog.read_scans(sid)
    second = catalog.read_scans(sid)
    assert first is second
    assert len(first) == 1


def test_read_scans_cache_invalidated_by_log_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    _isolate_parts(monkeypatch, tmp_path)
    catalog = SessionCatalog(base_dir=tmp_path)
    sid = catalog.current_session_id
    catalog.log_scan(_candidate(), _color(), source="desk")

    before = catalog.read_scans(sid)
    assert len(before) == 1

    catalog.log_scan(_candidate(id="3002", name="Brick 2 x 3"), _color(), source="desk")
    after = catalog.read_scans(sid)
    assert after is not before
    assert len(after) == 2
    assert after[1].part_id == "3002"


def test_read_scans_reloads_after_external_mtime_change(tmp_path, monkeypatch):
    monkeypatch.setattr(session_catalog_mod.bricklink_client, "is_configured", lambda: False)
    _isolate_parts(monkeypatch, tmp_path)
    catalog = SessionCatalog(base_dir=tmp_path)
    sid = catalog.current_session_id
    catalog.log_scan(_candidate(), _color(), source="desk")

    cached = catalog.read_scans(sid)
    assert len(cached) == 1

    path = catalog._scans_path(sid)
    # Append a row outside SessionCatalog, then bump mtime_ns so the cache key
    # changes even on filesystems with coarse timestamp resolution.
    with path.open("a", encoding="utf-8", newline="") as f:
        f.write(
            "2026-01-01T00:00:00,3003,Brick 1 x 2,Brick,part,90.0,"
            "Bright Blue,blue,80.0,desk,,\n"
        )
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1))

    refreshed = catalog.read_scans(sid)
    assert refreshed is not cached
    assert len(refreshed) == 2
    assert refreshed[1].part_id == "3003"

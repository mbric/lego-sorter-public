"""PartsCatalog — manual prices for unique part+color rows."""

from __future__ import annotations

import numpy as np
from PIL import Image

from demo_brickognize.parts_catalog import PartsCatalog


def _write_session_scan(
    sessions,
    *,
    sid: str,
    part_id: str = "3001",
    color_key: str = "red",
    color_name: str = "Bright Red",
    image_name: str = "x.jpg",
    scanned_at: str = "2026-01-01T12:00:00",
    label: str | None = None,
):
    root = sessions / sid
    (root / "captures").mkdir(parents=True, exist_ok=True)
    (root / "scans.csv").write_text(
        "scanned_at,part_id,part_name,category,item_type,shape_score_pct,"
        "color_name,color_key,color_confidence_pct,source,listing_id,image_file\n"
        f"{scanned_at},{part_id},Brick 2 x 4,Brick,part,90.0,"
        f"{color_name},{color_key},80.0,feed,,captures/{image_name}\n",
        encoding="utf-8",
    )
    Image.new("RGB", (16, 16), (200, 0, 0)).save(root / "captures" / image_name)
    (root / "session.json").write_text(
        f'{{"session_id":"{sid}","label":"{label or sid}","started_at":"{scanned_at}"}}\n',
        encoding="utf-8",
    )


def test_upsert_preserves_manual_price(tmp_path):
    catalog = PartsCatalog(path=tmp_path / "catalog.json")
    catalog.upsert(
        part_id="3001",
        part_name="Brick 2 x 4",
        color_name="Bright Red",
        color_key="red",
        capture_image=np.full((20, 30, 3), 10, dtype=np.uint8),
    )
    catalog.set_unit_price("3001", "red", 0.12)
    catalog.upsert(
        part_id="3001",
        part_name="Brick 2 x 4",
        color_name="Bright Red",
        color_key="red",
    )
    entry = catalog.get("3001", "red")
    assert entry is not None
    assert entry.unit_price == 0.12
    assert entry.times_seen == 2
    path = catalog.capture_path("3001", "red")
    assert path is not None and path.is_file()
    assert Image.open(path).size == (30, 20)


def test_apply_price_rows_and_display(tmp_path):
    catalog = PartsCatalog(path=tmp_path / "catalog.json")
    catalog.upsert(part_id="3001", part_name="Brick 2 x 4", color_key="red", color_name="Red")
    catalog.upsert(part_id="3002", part_name="Brick 2 x 3", color_key="blue", color_name="Blue")
    changed = catalog.apply_price_rows(
        [
            ["3001", "Brick 2 x 4", "Red", "red", "0.25", 1, ""],
            ["3002", "Brick 2 x 3", "Blue", "blue", "", 1, ""],
        ]
    )
    assert changed == 1
    assert catalog.unit_price("3001", "red") == 0.25
    assert catalog.unit_price("3002", "blue") is None
    assert catalog.priced_count() == 1
    rows = catalog.display_rows()
    assert len(rows) == 2
    assert rows[0][0] == "3002"  # unpriced first
    needs = catalog.filtered_entries(filter_mode="needs_price")
    assert [e.part_id for e in needs] == ["3002"]
    choices = catalog.picker_choices(filter_mode="all")
    assert len(choices) == 2
    radio = catalog.radio_choices(filter_mode="needs_price")
    assert len(radio) == 1
    assert radio[0][1].startswith("3002|")


def test_sync_from_session_scans(tmp_path):
    sessions = tmp_path / "sessions"
    _write_session_scan(sessions, sid="2026-01-01_120000")

    catalog = PartsCatalog(path=tmp_path / "parts" / "catalog.json")
    added = catalog.sync_from_session_scans(sessions)
    assert added == 1
    assert catalog.get("3001", "red") is not None
    assert catalog.capture_path("3001", "red") is not None
    assert catalog.get("3001", "red").times_seen == 1

    # Re-import must not inflate times_seen.
    added2 = catalog.sync_from_session_scans(sessions)
    assert added2 == 0
    assert catalog.get("3001", "red").times_seen == 1


def test_appearances_and_gallery(tmp_path):
    sessions = tmp_path / "sessions"
    _write_session_scan(
        sessions,
        sid="2026-01-01_120000",
        image_name="a.jpg",
        scanned_at="2026-01-01T12:00:00",
        label="Morning",
    )
    _write_session_scan(
        sessions,
        sid="2026-01-02_130000",
        image_name="b.jpg",
        scanned_at="2026-01-02T13:00:00",
        label="Afternoon",
    )
    # Second scan of same part in afternoon session
    afternoon = sessions / "2026-01-02_130000"
    with (afternoon / "scans.csv").open("a", encoding="utf-8") as f:
        f.write(
            "2026-01-02T13:05:00,3001,Brick 2 x 4,Brick,part,90.0,"
            "Bright Red,red,80.0,feed,,captures/c.jpg\n"
        )
    Image.new("RGB", (12, 12), (180, 0, 0)).save(afternoon / "captures" / "c.jpg")

    catalog = PartsCatalog(path=tmp_path / "parts" / "catalog.json")
    catalog.upsert(part_id="3001", part_name="Brick 2 x 4", color_key="red")

    apps = catalog.appearances_for("3001", "red", sessions)
    assert len(apps) == 3
    assert apps[0].session_label == "Morning"

    session_rows, session_ids = catalog.session_rows_for("3001", "red", sessions)
    assert len(session_rows) == 2
    assert session_rows[0][0] == "Afternoon"
    assert session_rows[0][1] == 2
    assert session_ids[0] == "2026-01-02_130000"

    gallery = catalog.gallery_items_for("3001", "red", sessions)
    assert len(gallery) == 3
    filtered = catalog.gallery_items_for(
        "3001", "red", sessions, session_id="2026-01-01_120000"
    )
    assert len(filtered) == 1
    assert "Morning" in filtered[0][1]


def test_list_table_rows_seen_from_sessions(tmp_path):
    sessions = tmp_path / "sessions"
    _write_session_scan(sessions, sid="s1")
    _write_session_scan(sessions, sid="s2", scanned_at="2026-01-02T12:00:00")

    catalog = PartsCatalog(path=tmp_path / "parts" / "catalog.json")
    catalog.upsert(part_id="3001", part_name="Brick 2 x 4", color_key="red", color_name="Red")
    catalog.upsert(part_id="3002", part_name="Brick 2 x 3", color_key="blue", color_name="Blue")
    catalog.set_unit_price("3001", "red", 0.5)

    rows, keys = catalog.list_table_rows(sessions, filter_mode="all")
    assert keys[0].startswith("3002|")  # unpriced first
    by_id = {r[0]: r for r in rows}
    assert by_id["3001"][4] == 2  # seen in two sessions
    assert by_id["3002"][4] == 0
    needs, need_keys = catalog.list_table_rows(sessions, filter_mode="needs_price")
    assert [r[0] for r in needs] == ["3002"]
    assert need_keys == ["3002|blue"]

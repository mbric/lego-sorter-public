"""Persist scan sessions to CSV and load them for the catalog viewer."""

from __future__ import annotations

import csv
import io
import json
import shutil
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from . import bricklink_client
from .brickognize_client import Candidate
from .color_detect import ColorDetection
from .parts_catalog import PARTS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SESSIONS_DIR = PROJECT_ROOT / "data" / "sessions"
EXPORTS_DIR = PROJECT_ROOT / "data" / "exports"
CAPTURES_DIRNAME = "captures"
EVENTS_LOG_NAME = "events.log"
# Picker sentinel: show the active scanning session (not a saved history id).
LIVE_SESSION_PICKER_VALUE = "__live__"

SCAN_FIELDS = (
    "scanned_at",
    "part_id",
    "part_name",
    "category",
    "item_type",
    "shape_score_pct",
    "color_name",
    "color_key",
    "color_confidence_pct",
    "source",
    "listing_id",
    "image_file",
)

# UI table columns (omit image_file path; preview loads it via row index).
SCAN_DISPLAY_FIELDS = tuple(f for f in SCAN_FIELDS if f != "image_file")

INVENTORY_FIELDS = ("part_id", "part_name", "color_name", "count")

VALUED_INVENTORY_FIELDS = (
    "part_id",
    "part_name",
    "color_name",
    "count",
    "unit_price",
    "line_value",
)


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    started_at: str
    label: str
    scan_count: int

    @property
    def choice_label(self) -> str:
        if self.scan_count:
            return f"{self.label} ({self.scan_count} scans)"
        return f"{self.label} (empty)"


@dataclass(frozen=True)
class SessionValuation:
    """Estimated BrickLink value for a session's aggregated inventory."""

    configured: bool
    currency: str
    guide_type: str
    condition: str
    total_value: float
    priced_combos: int
    unpriced_combos: int
    priced_pieces: int
    unpriced_pieces: int
    rows: list[list]  # [part_id, part_name, color_name, count, unit_price, line_value]
    pending_combos: int = 0  # known color, not yet looked up (cache-only view)
    pending_pieces: int = 0

    @property
    def total_combos(self) -> int:
        return self.priced_combos + self.unpriced_combos + self.pending_combos


@dataclass(frozen=True)
class ScanRecord:
    scanned_at: str
    part_id: str
    part_name: str
    category: str
    item_type: str
    shape_score_pct: float
    color_name: str
    color_key: str
    color_confidence_pct: float
    source: str
    listing_id: str
    image_file: str = ""

    def as_row(self) -> list:
        return [getattr(self, field) for field in SCAN_FIELDS]


class SessionCatalog:
    """One folder per app session: session.json + scans.csv."""

    def __init__(self, base_dir: Path = SESSIONS_DIR) -> None:
        self.base_dir = base_dir
        self._lock = threading.Lock()
        self._scans_cache: dict[str, tuple[int, list[ScanRecord]]] = {}
        self._current_id: str | None = None
        self._current_started_at: datetime = datetime.now()
        self._current_label: str = ""
        self._current_persisted: bool = False
        self._scan_seq = 0
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.purge_empty_sessions()
        self.start_new_session()

    @property
    def current_session_id(self) -> str:
        assert self._current_id is not None
        return self._current_id

    def start_new_session(self) -> str:
        """Begin a new (in-memory) session. Nothing is written to disk until the
        first scan is logged, so empty sessions never clutter ``data/sessions``."""
        now = datetime.now()
        session_id = now.strftime("%Y-%m-%d_%H%M%S")
        with self._lock:
            self._current_id = session_id
            self._current_started_at = now
            self._current_label = now.strftime("%Y-%m-%d %H:%M")
            self._current_persisted = False
            self._scan_seq = 0
        return session_id

    def _ensure_current_persisted(self) -> None:
        """Create the on-disk folder + files for the current session on demand."""
        with self._lock:
            if self._current_persisted:
                return
            session_id = self._current_id
            now = self._current_started_at
            session_dir = self._session_dir(session_id)
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / CAPTURES_DIRNAME).mkdir(parents=True, exist_ok=True)
            meta = {
                "session_id": session_id,
                "started_at": now.isoformat(timespec="seconds"),
                "label": self._current_label,
            }
            (session_dir / "session.json").write_text(
                json.dumps(meta, indent=2) + "\n",
                encoding="utf-8",
            )
            scans_path = session_dir / "scans.csv"
            if not scans_path.exists():
                with scans_path.open("w", encoding="utf-8", newline="") as f:
                    csv.writer(f).writerow(SCAN_FIELDS)
            self._current_persisted = True

    def purge_empty_sessions(self) -> int:
        """Delete any session folders that have no recorded scans. Returns count."""
        removed = 0
        if not self.base_dir.exists():
            return removed
        for entry in list(self.base_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name == self._current_id and self._current_persisted:
                continue
            if not self.read_scans(entry.name):
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        return removed

    def log_scan(
        self,
        candidate: Candidate,
        color: ColorDetection | None,
        *,
        source: str,
        listing_id: str = "",
        capture_image: Image.Image | np.ndarray | None = None,
    ) -> None:
        self._ensure_current_persisted()
        session_id = self.current_session_id
        now = datetime.now()

        with self._lock:
            self._scan_seq += 1
            seq = self._scan_seq

        image_file = ""
        if capture_image is not None:
            image_file = self._save_capture(session_id, seq, candidate, color, capture_image)

        record = ScanRecord(
            scanned_at=now.isoformat(timespec="seconds"),
            part_id=candidate.id,
            part_name=candidate.name,
            category=candidate.category or "",
            item_type=candidate.item_type,
            shape_score_pct=round(candidate.score * 100, 1),
            color_name=color.name if color else "",
            color_key=color.key if color else "",
            color_confidence_pct=round(color.confidence * 100, 1) if color else 0.0,
            source=source,
            listing_id=listing_id,
            image_file=image_file,
        )
        path = self._scans_path(session_id)
        with self._lock:
            with path.open("a", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(record.as_row())
            self._scans_cache.pop(session_id, None)

        # Keep the long-lived parts catalog in sync (manual prices live there).
        try:
            PARTS.upsert(
                part_id=record.part_id,
                part_name=record.part_name,
                category=record.category,
                item_type=record.item_type,
                color_name=record.color_name,
                color_key=record.color_key,
                capture_image=capture_image,
            )
        except ValueError:
            pass

        self.append_event(
            "scan",
            state="catalog",
            part=record.part_id,
            name=record.part_name,
            score=record.shape_score_pct,
            color=record.color_key or record.color_name or "",
            source=source,
            crop=image_file or "",
        )

    def append_event(self, event: str, *, state: str = "", **fields: object) -> None:
        """Append one structured line to this session's ``events.log``.

        Persists the session folder on first call so gate rejects / loop starts
        are recorded even before a successful scan.
        """
        if not event:
            return
        self._ensure_current_persisted()
        session_id = self.current_session_id
        stamp = datetime.now().isoformat(timespec="milliseconds")
        parts = [stamp, f"state={_event_token(state or '-')}", f"event={_event_token(event)}"]
        for key in sorted(fields):
            val = fields[key]
            if val is None:
                continue
            parts.append(f"{key}={_event_token(val)}")
        line = " ".join(parts) + "\n"
        path = self._session_dir(session_id) / EVENTS_LOG_NAME
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)

    def read_events_text(
        self, session_id: str | None, *, max_lines: int = 80
    ) -> str:
        """Return plaintext ``events.log`` for the Sessions tab (newest lines kept)."""
        if not session_id:
            return "Select a session to view its event log."
        path = self._session_dir(session_id) / EVENTS_LOG_NAME
        if not path.exists():
            return (
                "(no events yet — start the feed loop to record capture / match activity)"
            )
        with self._lock:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                return "(could not read events.log)"
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return "(no events yet — start the feed loop to record capture / match activity)"
        if len(lines) > max_lines:
            omitted = len(lines) - max_lines
            lines = lines[-max_lines:]
            return f"… ({omitted} earlier lines omitted)\n" + "\n".join(lines)
        return "\n".join(lines)

    def _save_capture(
        self,
        session_id: str,
        seq: int,
        candidate: Candidate,
        color: ColorDetection | None,
        image: Image.Image | np.ndarray,
    ) -> str:
        if isinstance(image, np.ndarray):
            pil = Image.fromarray(image.astype(np.uint8))
        else:
            pil = image
        pil = pil.convert("RGB")

        color_key = color.key if color and color.key else "nocolor"
        safe_part = "".join(c for c in candidate.id if c.isalnum()) or "part"
        filename = f"{seq:04d}_{safe_part}_{color_key}.jpg"
        rel_path = f"{CAPTURES_DIRNAME}/{filename}"
        dest = self._session_dir(session_id) / CAPTURES_DIRNAME / filename
        try:
            pil.save(dest, format="JPEG", quality=88)
        except OSError:
            return ""
        return rel_path

    def list_sessions(self) -> list[SessionInfo]:
        sessions: list[SessionInfo] = []
        if not self.base_dir.exists():
            return sessions

        for meta_path in self.base_dir.glob("*/session.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            session_id = str(meta.get("session_id", meta_path.parent.name))
            scans = self.read_scans(session_id)
            sessions.append(
                SessionInfo(
                    session_id=session_id,
                    started_at=str(meta.get("started_at", "")),
                    label=str(meta.get("label", session_id)),
                    scan_count=len(scans),
                )
            )

        sessions.sort(key=lambda s: s.session_id, reverse=True)
        return sessions

    def read_scans(self, session_id: str) -> list[ScanRecord]:
        path = self._scans_path(session_id)
        if not path.exists():
            return []

        with self._lock:
            mtime = path.stat().st_mtime_ns
            cached = self._scans_cache.get(session_id)
            if cached and cached[0] == mtime:
                return cached[1]

            rows: list[ScanRecord] = []
            with path.open(encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for raw in reader:
                    if not raw.get("part_id"):
                        continue
                    rows.append(
                        ScanRecord(
                            scanned_at=raw.get("scanned_at", ""),
                            part_id=raw.get("part_id", ""),
                            part_name=raw.get("part_name", ""),
                            category=raw.get("category", ""),
                            item_type=raw.get("item_type", ""),
                            shape_score_pct=float(raw.get("shape_score_pct") or 0),
                            color_name=raw.get("color_name", ""),
                            color_key=raw.get("color_key", ""),
                            color_confidence_pct=float(raw.get("color_confidence_pct") or 0),
                            source=raw.get("source", ""),
                            listing_id=raw.get("listing_id", ""),
                            image_file=raw.get("image_file", ""),
                        )
                    )
            self._scans_cache[session_id] = (mtime, rows)
            return rows

    def inventory_totals(self, session_id: str) -> list[list]:
        """Aggregate scans by part + color."""
        counts: dict[tuple[str, str, str, str], int] = {}
        for scan in self.read_scans(session_id):
            key = (scan.part_id, scan.part_name, scan.color_name or "—", scan.color_key or "")
            counts[key] = counts.get(key, 0) + 1

        rows = [
            [part_id, part_name, color_name, count]
            for (part_id, part_name, color_name, _color_key), count in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0][0], item[0][2]),
            )
        ]
        return rows

    def scans_table(self, session_id: str) -> list[list]:
        return [scan.as_row() for scan in self.read_scans(session_id)]

    def scans_display_table(self, session_id: str) -> list[list]:
        """Rows for the Catalog UI (no image_file column)."""
        return [
            [getattr(scan, field) for field in SCAN_DISPLAY_FIELDS]
            for scan in self.read_scans(session_id)
        ]

    def capture_path(self, session_id: str, image_file: str) -> Path | None:
        """Resolve a scan's ``image_file`` to an absolute path if it exists."""
        if not session_id or not image_file:
            return None
        # Only allow relative captures/… paths under the session folder.
        rel = Path(image_file)
        if rel.is_absolute() or ".." in rel.parts:
            return None
        path = (self._session_dir(session_id) / rel).resolve()
        try:
            path.relative_to(self._session_dir(session_id).resolve())
        except ValueError:
            return None
        return path if path.is_file() else None

    def scan_capture_path(self, session_id: str, row_index: int) -> Path | None:
        """Capture image for the Nth scan in session order (Catalog row click)."""
        if not session_id or row_index < 0:
            return None
        scans = self.read_scans(session_id)
        if row_index >= len(scans):
            return None
        return self.capture_path(session_id, scans[row_index].image_file)

    def _inventory_csv_bytes(self, session_id: str) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(INVENTORY_FIELDS)
        writer.writerows(self.inventory_totals(session_id))
        return buf.getvalue().encode("utf-8")

    def inventory_valuation(self, session_id: str, *, cache_only: bool = False) -> SessionValuation:
        """Aggregate scans by part+color and price them.

        Price order: manual Parts catalog → BrickLink (if configured).
        Local catalog prices always count, so session value works without
        seller API / scrape. With ``cache_only=True``, BrickLink is cache-only.
        """
        combos: dict[tuple[str, str, str, str], int] = {}
        for scan in self.read_scans(session_id):
            key = (scan.part_id, scan.part_name, scan.color_name or "—", scan.color_key or "")
            combos[key] = combos.get(key, 0) + 1

        bl_configured = bricklink_client.is_configured()
        # Manual catalog is always available as a pricing source.
        configured = True
        total_value = 0.0
        priced_combos = unpriced_combos = pending_combos = 0
        priced_pieces = unpriced_pieces = pending_pieces = 0
        currency = bricklink_client.currency_code()
        used_manual = False
        used_bricklink = False
        rows: list[list] = []

        for (part_id, part_name, color_name, color_key), count in combos.items():
            unit = PARTS.unit_price(part_id, color_key)
            source = "manual"
            if unit is None and bl_configured:
                bl_value = bricklink_client.price_for(
                    part_id, color_key, cache_only=cache_only
                )
                if bl_value and bl_value.has_price:
                    unit = float(bl_value.unit_price)
                    currency = bl_value.currency
                    source = "bricklink"
            if unit is not None and unit > 0:
                if source == "manual":
                    used_manual = True
                else:
                    used_bricklink = True
                line_value = round(unit * count, 2)
                total_value += line_value
                priced_combos += 1
                priced_pieces += count
                rows.append(
                    [part_id, part_name, color_name, count, round(unit, 2), line_value]
                )
                continue

            has_color = bricklink_client.color_id_for(color_key) is not None
            is_pending = (
                bl_configured
                and cache_only
                and has_color
                and PARTS.unit_price(part_id, color_key) is None
                and not bricklink_client.is_cached(part_id, color_key)
            )
            if is_pending:
                pending_combos += 1
                pending_pieces += count
            else:
                unpriced_combos += 1
                unpriced_pieces += count
            rows.append([part_id, part_name, color_name, count, None, None])

        rows.sort(key=lambda r: (-(r[5] or 0), -r[3], r[0]))

        if used_manual and not used_bricklink:
            guide = "manual"
            condition = "—"
        elif used_bricklink and not used_manual:
            guide = bricklink_client.guide_type()
            condition = bricklink_client.condition()
        elif used_manual and used_bricklink:
            guide = "manual+bricklink"
            condition = bricklink_client.condition()
        else:
            guide = "manual"
            condition = "—"

        return SessionValuation(
            configured=configured,
            currency=currency,
            guide_type=guide,
            condition=condition,
            total_value=round(total_value, 2),
            priced_combos=priced_combos,
            unpriced_combos=unpriced_combos,
            priced_pieces=priced_pieces,
            unpriced_pieces=unpriced_pieces,
            rows=rows,
            pending_combos=pending_combos,
            pending_pieces=pending_pieces,
        )

    def _valued_inventory_csv_bytes(self, valuation: SessionValuation) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        cond = "Used" if valuation.condition == "U" else "New"
        writer.writerow(
            [
                f"# Valuation source={valuation.guide_type}, "
                f"condition={cond}, currency={valuation.currency}"
            ]
        )
        writer.writerow(VALUED_INVENTORY_FIELDS)
        for row in valuation.rows:
            writer.writerow(["" if cell is None else cell for cell in row])
        writer.writerow([])
        writer.writerow(["TOTAL", "", "", valuation.priced_pieces + valuation.unpriced_pieces, "", valuation.total_value])
        return buf.getvalue().encode("utf-8")

    def _valuation_json_bytes(self, session_id: str, valuation: SessionValuation) -> bytes:
        payload = {
            "session_id": session_id,
            "valued_at": datetime.now().isoformat(timespec="seconds"),
            "source": "bricklink",
            "configured": valuation.configured,
            "currency": valuation.currency,
            "guide_type": valuation.guide_type,
            "condition": "used" if valuation.condition == "U" else "new",
            "total_value": valuation.total_value,
            "priced_combos": valuation.priced_combos,
            "unpriced_combos": valuation.unpriced_combos,
            "priced_pieces": valuation.priced_pieces,
            "unpriced_pieces": valuation.unpriced_pieces,
        }
        return (json.dumps(payload, indent=2) + "\n").encode("utf-8")

    @staticmethod
    def _stat_card(label: str, value: str, *, accent: bool = False) -> str:
        border = "#5b6cff" if accent else "rgba(255,255,255,0.12)"
        value_color = "#9aa7ff" if accent else "inherit"
        return (
            "<div style='flex:1 1 120px;min-width:110px;padding:10px 14px;"
            f"border:1px solid {border};border-radius:10px;"
            "background:rgba(255,255,255,0.03)'>"
            "<div style='font-size:0.72em;letter-spacing:0.04em;text-transform:uppercase;"
            f"opacity:0.65'>{label}</div>"
            f"<div style='font-size:1.35em;font-weight:600;margin-top:2px;color:{value_color}'>"
            f"{value}</div></div>"
        )

    def recent_scans_html(self, session_id: str, *, limit: int = 8) -> str:
        """Compact newest-first list for the live side panel."""
        scans = self.read_scans(session_id)
        if not scans:
            return (
                "<p style='opacity:0.6;margin:0;font-size:0.9em'>"
                "No scans yet — hold a piece still to start.</p>"
            )

        import html as html_mod

        rows: list[str] = []
        for scan in reversed(scans[-limit:]):
            if "T" in scan.scanned_at:
                time_part = scan.scanned_at.split("T", 1)[1][:8]
            else:
                time_part = scan.scanned_at[-8:]
            color = f"{scan.color_name} " if scan.color_name else ""
            rows.append(
                "<li style='padding:7px 0;border-bottom:1px solid rgba(255,255,255,0.06)'>"
                f"<span style='opacity:0.5;font-size:0.82em;margin-right:6px'>"
                f"{html_mod.escape(time_part)}</span>"
                f"<span>{html_mod.escape(color)}{html_mod.escape(scan.part_name)}</span> "
                f"<code style='opacity:0.75;font-size:0.82em'>{html_mod.escape(scan.part_id)}</code>"
                "</li>"
            )
        return (
            "<div style='max-height:220px;overflow-y:auto;padding-right:4px'>"
            "<ul style='margin:0;padding:0;list-style:none'>"
            f"{''.join(rows)}</ul></div>"
        )

    def catalog_overview_html(
        self,
        session_id: str | None,
        *,
        cache_only: bool = False,
        valuation: SessionValuation | None = None,
    ) -> str:
        """Single session summary strip: counts + est. value + pricing status."""
        import html as html_mod

        if not session_id:
            return "<p style='opacity:0.7'>Select a session to view its summary.</p>"

        scans = self.read_scans(session_id)
        is_live = session_id == self._current_id
        if not scans:
            empty_note = (
                "No scans yet — start the feed loop or identify a still."
                if is_live
                else "No scans recorded in this session."
            )
            live_bit = (
                "<span style='display:inline-block;margin-left:8px;padding:1px 8px;"
                "border-radius:999px;font-size:0.75em;letter-spacing:0.04em;"
                "text-transform:uppercase;background:rgba(59,130,246,0.22);"
                "color:#93c5fd'>Live</span>"
                if is_live
                else ""
            )
            return (
                f"<div style='display:flex;align-items:center;gap:6px;margin-bottom:8px'>"
                f"<span style='opacity:0.7;font-size:0.88em'>{html_mod.escape(empty_note)}</span>"
                f"{live_bit}</div>"
            )

        unique_parts = len({s.part_id for s in scans})
        combos = len(self.inventory_totals(session_id))
        colors = sorted({s.color_name for s in scans if s.color_name})
        if valuation is None:
            valuation = self.inventory_valuation(session_id, cache_only=cache_only)

        if valuation.priced_combos == 0 and valuation.pending_combos:
            value_str = "?"
            value_detail = "BrickLink pending — use Estimate value, or set prices in Parts"
        elif valuation.priced_combos == 0 and bricklink_client.last_error():
            value_str = "—"
            value_detail = (
                f"{html_mod.escape(str(bricklink_client.last_error()))} "
                "— set prices in Parts catalog"
            )
        elif valuation.priced_combos == 0:
            value_str = "—"
            value_detail = "No prices yet — set unit prices in Parts catalog"
        else:
            suffix = " so far" if valuation.pending_combos else ""
            value_str = f"{valuation.total_value:,.2f} {valuation.currency}"
            if valuation.guide_type == "manual":
                source_label = "Parts catalog"
            elif valuation.guide_type == "manual+bricklink":
                source_label = "Manual + BrickLink"
            else:
                cond = "used" if valuation.condition == "U" else "new"
                source_label = (
                    f"{bricklink_client.backend_label()} · "
                    f"{valuation.guide_type}, {cond}"
                )
            detail_bits = [
                source_label,
                f"{valuation.priced_combos}/{valuation.total_combos} priced{suffix}",
            ]
            if valuation.pending_combos:
                detail_bits.append(f"{valuation.pending_combos} BrickLink pending")
            if valuation.unpriced_combos:
                detail_bits.append(f"{valuation.unpriced_combos} need price in Parts")
            value_detail = " · ".join(detail_bits)

        cards = "".join(
            (
                self._stat_card("Scans", str(len(scans))),
                self._stat_card("Unique parts", str(unique_parts)),
                self._stat_card("Part+color combos", str(combos)),
                self._stat_card("Est. value", value_str, accent=True),
            )
        )
        live_bit = (
            "<span style='display:inline-block;padding:1px 8px;border-radius:999px;"
            "font-size:0.72em;letter-spacing:0.04em;text-transform:uppercase;"
            "background:rgba(59,130,246,0.22);color:#93c5fd'>Live</span>"
            if is_live
            else ""
        )
        colors_line = (
            f"<div style='margin-top:8px;opacity:0.8;font-size:0.88em;"
            f"display:flex;align-items:center;gap:10px;flex-wrap:wrap'>"
            f"{live_bit}"
            f"<span><strong>Colors:</strong> "
            f"{html_mod.escape(', '.join(colors) if colors else '—')}</span></div>"
        )
        value_line = (
            f"<div style='margin-top:2px;opacity:0.75;font-size:0.82em'>{value_detail}</div>"
        )
        return (
            "<div style='display:flex;gap:10px;flex-wrap:wrap'>"
            f"{cards}</div>{colors_line}{value_line}"
        )

    def live_panel_html(self, session_id: str | None, *, cache_only: bool = True) -> str:
        """Alias for the Sessions summary strip (kept for older call sites)."""
        return self.catalog_overview_html(session_id, cache_only=cache_only)

    def export_zip(self, session_id: str | None) -> str | None:
        """Bundle session data (CSVs, metadata, capture stills) into a zip; return path."""
        if not session_id:
            return None
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return None

        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_path = EXPORTS_DIR / f"lego_session_{session_id}_{stamp}.zip"

        scans = self.read_scans(session_id)
        valuation = self.inventory_valuation(session_id)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            scans_csv = self._scans_path(session_id)
            if scans_csv.exists():
                zf.write(scans_csv, arcname="scans.csv")

            meta_path = session_dir / "session.json"
            if meta_path.exists():
                zf.write(meta_path, arcname="session.json")

            events_path = session_dir / EVENTS_LOG_NAME
            if events_path.exists():
                zf.write(events_path, arcname=EVENTS_LOG_NAME)

            zf.writestr("inventory.csv", self._inventory_csv_bytes(session_id))
            zf.writestr("inventory_valued.csv", self._valued_inventory_csv_bytes(valuation))
            zf.writestr("valuation.json", self._valuation_json_bytes(session_id, valuation))
            zf.writestr("README.txt", self._export_readme(session_id, len(scans), valuation))

            for scan in scans:
                if not scan.image_file:
                    continue
                img_path = session_dir / scan.image_file
                if img_path.exists():
                    zf.write(img_path, arcname=scan.image_file)

        return str(zip_path)

    def _export_readme(
        self, session_id: str, scan_count: int, valuation: SessionValuation | None = None
    ) -> str:
        if valuation is None:
            valuation = self.inventory_valuation(session_id)
        if valuation.configured:
            cond = "used" if valuation.condition == "U" else "new"
            value_line = (
                f"Estimated value: {valuation.total_value:,.2f} {valuation.currency} "
                f"(BrickLink {valuation.guide_type} guide, {cond}; "
                f"{valuation.priced_combos}/{valuation.total_combos} combos priced)\n"
            )
        else:
            value_line = "Estimated value: not computed (BrickLink credentials not configured)\n"
        return (
            f"LEGO Sorter — session export\n"
            f"Session: {session_id}\n"
            f"Exported: {datetime.now().isoformat(timespec='seconds')}\n"
            f"Scans: {scan_count}\n"
            f"{value_line}\n"
            f"Contents:\n"
            f"  scans.csv            — one row per capture (chronological), links to image_file\n"
            f"  inventory.csv        — aggregated counts by part + color\n"
            f"  inventory_valued.csv — inventory with BrickLink unit + line value, plus total\n"
            f"  valuation.json       — valuation summary (total, currency, coverage)\n"
            f"  session.json         — session metadata\n"
            f"  {EVENTS_LOG_NAME}          — capture/match/reject debug timeline\n"
            f"  {CAPTURES_DIRNAME}/             — still image for each capture (image_file column)\n"
        )

    def current_session_label(self) -> str:
        """Short label for the active scanning session (legacy helper)."""
        sessions = {s.session_id: s for s in self.list_sessions()}
        info = sessions.get(self.current_session_id)
        if info:
            return info.choice_label
        return f"{self._current_label or self.current_session_id} (empty)"

    def session_choices(self) -> list[tuple[str, str]]:
        return [(s.choice_label, s.session_id) for s in self.list_sessions()]

    def live_picker_label(self) -> str:
        """Dropdown label for the live / current scanning session."""
        if not self._current_persisted:
            return f"Live · {self._current_label} (empty)"
        info = next(
            (s for s in self.list_sessions() if s.session_id == self._current_id),
            None,
        )
        if info is not None:
            return f"Live · {info.choice_label}"
        return f"Live · {self._current_label}"

    def session_picker_choices(self) -> list[tuple[str, str]]:
        """Live session first, then older saved sessions (opt-in history)."""
        live = (self.live_picker_label(), LIVE_SESSION_PICKER_VALUE)
        past: list[tuple[str, str]] = []
        for label, session_id in self.session_choices():
            if session_id == self._current_id:
                continue
            past.append((label, session_id))
        return [live, *past]

    def resolve_picker_session(self, picker_value: str | None) -> str | None:
        """Map picker value → session id (``__live__`` / empty → current)."""
        if not picker_value or picker_value == LIVE_SESSION_PICKER_VALUE:
            return self.current_session_id
        return picker_value

    def default_picker_value(self) -> str:
        """Always default the Sessions dropdown to the live session."""
        return LIVE_SESSION_PICKER_VALUE

    def _session_dir(self, session_id: str) -> Path:
        return self.base_dir / session_id

    def _scans_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "scans.csv"


def _event_token(value: object) -> str:
    """Format a field value for events.log (quote if it contains whitespace)."""
    text = str(value)
    if not text:
        return '""'
    if any(ch.isspace() for ch in text) or "=" in text or '"' in text:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


# Shared catalog for the running app process.
CATALOG = SessionCatalog()

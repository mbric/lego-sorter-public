"""Persistent catalog of unique matched parts with optional manual prices."""

from __future__ import annotations

import csv
import json
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = PROJECT_ROOT / "data" / "parts_catalog"
CATALOG_PATH = CATALOG_DIR / "catalog.json"
CAPTURES_DIRNAME = "captures"

DISPLAY_FIELDS = (
    "part_id",
    "part_name",
    "color_name",
    "color_key",
    "unit_price",
    "times_seen",
    "last_seen",
)

# Parts catalog Dataframe columns (UI).
TABLE_HEADERS = ("part_id", "name", "color", "price", "seen")
SESSION_HEADERS = ("session", "scans", "last_seen")


@dataclass
class PartEntry:
    part_id: str
    part_name: str
    category: str = ""
    item_type: str = "part"
    color_name: str = ""
    color_key: str = ""
    unit_price: float | None = None
    times_seen: int = 0
    first_seen: str = ""
    last_seen: str = ""
    image_file: str = ""

    @property
    def key(self) -> str:
        return _combo_key(self.part_id, self.color_key)


@dataclass(frozen=True)
class PartAppearance:
    """One scan of a part+color inside a session folder."""

    session_id: str
    session_label: str
    scanned_at: str
    image_path: Path | None
    part_id: str
    color_key: str


def _combo_key(part_id: str, color_key: str | None) -> str:
    return f"{(part_id or '').strip()}|{(color_key or '').strip().lower()}"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _session_label(session_dir: Path, session_id: str) -> str:
    meta_path = session_dir / "session.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            label = str(meta.get("label") or "").strip()
            if label:
                return label
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return session_id


class PartsCatalog:
    """One entry per unique part_id + color_key, with optional unit_price."""

    def __init__(self, path: Path = CATALOG_PATH) -> None:
        self.path = path
        self.base_dir = path.parent
        self._lock = threading.Lock()
        self._entries: dict[str, PartEntry] | None = None

    def _captures_dir(self) -> Path:
        d = self.base_dir / CAPTURES_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    def reload(self) -> None:
        """Drop the in-memory cache so the next read comes from disk."""
        with self._lock:
            self._entries = None

    def _load(self) -> dict[str, PartEntry]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, PartEntry] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = []
            if isinstance(raw, dict):
                raw = raw.get("entries", [])
            for item in raw or []:
                try:
                    entry = PartEntry(
                        part_id=str(item.get("part_id", "")),
                        part_name=str(item.get("part_name", "")),
                        category=str(item.get("category", "")),
                        item_type=str(item.get("item_type", "part")),
                        color_name=str(item.get("color_name", "")),
                        color_key=str(item.get("color_key", "")),
                        unit_price=_parse_price(item.get("unit_price")),
                        times_seen=int(item.get("times_seen") or 0),
                        first_seen=str(item.get("first_seen", "")),
                        last_seen=str(item.get("last_seen", "")),
                        image_file=str(item.get("image_file", "")),
                    )
                except (TypeError, ValueError):
                    continue
                if entry.part_id:
                    entries[entry.key] = entry
        self._entries = entries
        return entries

    def _save(self) -> None:
        entries = self._load()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _now_iso(),
            "entries": [asdict(entries[k]) for k in sorted(entries)],
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def list_entries(self) -> list[PartEntry]:
        with self._lock:
            entries = list(self._load().values())
        entries.sort(
            key=lambda e: (
                e.unit_price is not None,  # unpriced first (easier to fill)
                e.part_id,
                e.color_name or e.color_key,
            )
        )
        return entries

    def filtered_entries(
        self,
        *,
        query: str = "",
        filter_mode: str = "all",
    ) -> list[PartEntry]:
        """Filter catalog rows for the Parts UI.

        ``filter_mode``: ``all`` | ``needs_price`` | ``priced``.
        """
        q = (query or "").strip().lower()
        mode = (filter_mode or "all").strip().lower()
        out: list[PartEntry] = []
        for entry in self.list_entries():
            if mode == "needs_price" and entry.unit_price is not None:
                continue
            if mode == "priced" and entry.unit_price is None:
                continue
            if q:
                hay = " ".join(
                    (
                        entry.part_id,
                        entry.part_name,
                        entry.color_name,
                        entry.color_key,
                        entry.category,
                    )
                ).lower()
                if q not in hay:
                    continue
            out.append(entry)
        return out

    def scan_all_appearances(
        self, sessions_dir: Path
    ) -> dict[str, list[PartAppearance]]:
        """Walk every session scans.csv → part+color key → appearance list."""
        by_key: dict[str, list[PartAppearance]] = defaultdict(list)
        if not sessions_dir.exists():
            return {}
        for scans_path in sorted(sessions_dir.glob("*/scans.csv")):
            session_dir = scans_path.parent
            session_id = session_dir.name
            label = _session_label(session_dir, session_id)
            try:
                with scans_path.open(encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        part_id = (row.get("part_id") or "").strip()
                        if not part_id:
                            continue
                        color_key = (row.get("color_key") or "").strip().lower()
                        image_file = (row.get("image_file") or "").strip()
                        image_path: Path | None = None
                        if image_file:
                            candidate = (session_dir / image_file).resolve()
                            try:
                                candidate.relative_to(session_dir.resolve())
                            except ValueError:
                                candidate = None  # type: ignore[assignment]
                            if candidate is not None and candidate.is_file():
                                image_path = candidate
                        by_key[_combo_key(part_id, color_key)].append(
                            PartAppearance(
                                session_id=session_id,
                                session_label=label,
                                scanned_at=str(row.get("scanned_at") or ""),
                                image_path=image_path,
                                part_id=part_id,
                                color_key=color_key,
                            )
                        )
            except OSError:
                continue
        return dict(by_key)

    def appearances_for(
        self,
        part_id: str,
        color_key: str | None,
        sessions_dir: Path,
    ) -> list[PartAppearance]:
        """All session scans for one part+color (chronological)."""
        key = _combo_key(part_id, color_key)
        items = list(self.scan_all_appearances(sessions_dir).get(key, []))
        items.sort(key=lambda a: a.scanned_at)
        return items

    def session_rows_for(
        self,
        part_id: str,
        color_key: str | None,
        sessions_dir: Path,
    ) -> tuple[list[list], list[str]]:
        """Aggregate appearances → rows + parallel session ids for filtering."""
        by_session: dict[str, list[PartAppearance]] = defaultdict(list)
        for app in self.appearances_for(part_id, color_key, sessions_dir):
            by_session[app.session_id].append(app)
        rows: list[list] = []
        session_ids: list[str] = []
        for session_id, apps in sorted(
            by_session.items(),
            key=lambda kv: max((a.scanned_at for a in kv[1]), default=""),
            reverse=True,
        ):
            label = apps[0].session_label if apps else session_id
            last = max((a.scanned_at for a in apps), default="")
            rows.append([label, len(apps), last])
            session_ids.append(session_id)
        return rows, session_ids

    def gallery_items_for(
        self,
        part_id: str,
        color_key: str | None,
        sessions_dir: Path,
        *,
        session_id: str | None = None,
    ) -> list[tuple[str, str]]:
        """Gradio Gallery tuples ``(path, caption)``; optional session filter."""
        items: list[tuple[str, str]] = []
        for app in self.appearances_for(part_id, color_key, sessions_dir):
            if session_id and app.session_id != session_id:
                continue
            if app.image_path is None:
                continue
            time_part = app.scanned_at
            if "T" in time_part:
                time_part = time_part.split("T", 1)[1][:8]
            caption = f"{app.session_label} · {time_part}"
            items.append((str(app.image_path), caption))
        return items

    def list_table_rows(
        self,
        sessions_dir: Path,
        *,
        query: str = "",
        filter_mode: str = "all",
    ) -> tuple[list[list], list[str]]:
        """Dataframe rows + parallel entry keys (seen = session scan count)."""
        counts = {
            key: len(apps)
            for key, apps in self.scan_all_appearances(sessions_dir).items()
        }
        rows: list[list] = []
        keys: list[str] = []
        for entry in self.filtered_entries(query=query, filter_mode=filter_mode):
            price = "" if entry.unit_price is None else float(entry.unit_price)
            color = entry.color_name or entry.color_key or "—"
            seen = counts.get(entry.key, 0)
            rows.append([entry.part_id, entry.part_name, color, price, seen])
            keys.append(entry.key)
        return rows, keys

    def picker_choices(
        self, *, query: str = "", filter_mode: str = "all"
    ) -> list[tuple[str, str]]:
        """Gradio dropdown choices: (label, entry.key)."""
        choices: list[tuple[str, str]] = []
        for entry in self.filtered_entries(query=query, filter_mode=filter_mode):
            color = entry.color_name or entry.color_key or "—"
            price = (
                f"${entry.unit_price:.2f}"
                if entry.unit_price is not None
                else "no price"
            )
            label = f"{entry.part_id} · {color} · {price} — {entry.part_name}"
            choices.append((label, entry.key))
        return choices

    def radio_choices(
        self, *, query: str = "", filter_mode: str = "all", limit: int = 200
    ) -> list[tuple[str, str]]:
        """Compact clickable-row labels for a Radio list: (label, entry.key)."""
        choices: list[tuple[str, str]] = []
        for entry in self.filtered_entries(query=query, filter_mode=filter_mode)[
            :limit
        ]:
            color = entry.color_name or entry.color_key or "—"
            price = (
                f"${entry.unit_price:.2f}"
                if entry.unit_price is not None
                else "—"
            )
            name = entry.part_name
            if len(name) > 42:
                name = name[:41] + "…"
            label = f"{entry.part_id}   {name}   {color}   {price}   ×{entry.times_seen}"
            choices.append((label, entry.key))
        return choices

    def get_by_key(self, key: str | None) -> PartEntry | None:
        if not key:
            return None
        with self._lock:
            return self._load().get(str(key))

    def display_rows(self) -> list[list]:
        rows: list[list] = []
        for entry in self.list_entries():
            rows.append(
                [
                    entry.part_id,
                    entry.part_name,
                    entry.color_name,
                    entry.color_key,
                    "" if entry.unit_price is None else entry.unit_price,
                    entry.times_seen,
                    entry.last_seen,
                ]
            )
        return rows

    def get(self, part_id: str, color_key: str | None) -> PartEntry | None:
        with self._lock:
            return self._load().get(_combo_key(part_id, color_key))

    def unit_price(self, part_id: str, color_key: str | None) -> float | None:
        entry = self.get(part_id, color_key)
        if entry is None or entry.unit_price is None:
            return None
        return float(entry.unit_price)

    def priced_count(self) -> int:
        return sum(1 for e in self.list_entries() if e.unit_price is not None)

    def upsert(
        self,
        *,
        part_id: str,
        part_name: str,
        category: str = "",
        item_type: str = "part",
        color_name: str = "",
        color_key: str = "",
        capture_image: Image.Image | np.ndarray | None = None,
        bump_seen: bool = True,
    ) -> PartEntry:
        """Create or refresh a part entry. Never overwrites an existing price."""
        part_id = (part_id or "").strip()
        if not part_id:
            raise ValueError("part_id required")
        color_key = (color_key or "").strip().lower()
        color_name = (color_name or "").strip()
        now = _now_iso()
        key = _combo_key(part_id, color_key)

        with self._lock:
            entries = self._load()
            existing = entries.get(key)
            if existing is None:
                entry = PartEntry(
                    part_id=part_id,
                    part_name=part_name or part_id,
                    category=category or "",
                    item_type=item_type or "part",
                    color_name=color_name,
                    color_key=color_key,
                    unit_price=None,
                    times_seen=1,
                    first_seen=now,
                    last_seen=now,
                )
            else:
                seen = existing.times_seen + 1 if bump_seen else existing.times_seen
                entry = PartEntry(
                    part_id=part_id,
                    part_name=part_name or existing.part_name,
                    category=category or existing.category,
                    item_type=item_type or existing.item_type,
                    color_name=color_name or existing.color_name,
                    color_key=color_key or existing.color_key,
                    unit_price=existing.unit_price,
                    times_seen=seen,
                    first_seen=existing.first_seen or now,
                    last_seen=now if bump_seen else (existing.last_seen or now),
                    image_file=existing.image_file,
                )

            if capture_image is not None:
                rel = self._save_capture(part_id, color_key, capture_image)
                if rel:
                    entry.image_file = rel

            entries[key] = entry
            self._save()
            return entry

    def _save_capture(
        self,
        part_id: str,
        color_key: str,
        image: Image.Image | np.ndarray,
    ) -> str:
        if isinstance(image, np.ndarray):
            pil = Image.fromarray(image.astype(np.uint8))
        else:
            pil = image
        pil = pil.convert("RGB")
        safe_part = "".join(c for c in part_id if c.isalnum()) or "part"
        safe_color = (
            "".join(c for c in (color_key or "nocolor") if c.isalnum()) or "nocolor"
        )
        filename = f"{safe_part}_{safe_color}.jpg"
        dest = self._captures_dir() / filename
        try:
            pil.save(dest, format="JPEG", quality=88)
        except OSError:
            return ""
        return f"{CAPTURES_DIRNAME}/{filename}"

    def set_unit_price(
        self, part_id: str, color_key: str | None, unit_price: float | None
    ) -> PartEntry | None:
        key = _combo_key(part_id, color_key)
        with self._lock:
            entries = self._load()
            entry = entries.get(key)
            if entry is None:
                return None
            entry.unit_price = _parse_price(unit_price)
            entries[key] = entry
            self._save()
            return entry

    def apply_price_rows(self, rows: list[list] | None) -> int:
        """Update unit prices from Catalog UI rows. Returns how many changed."""
        if not rows:
            return 0
        changed = 0
        with self._lock:
            entries = self._load()
            for row in rows:
                if not row or len(row) < 5:
                    continue
                part_id = str(row[0] or "").strip()
                color_key = str(row[3] or "").strip().lower()
                if not part_id:
                    continue
                key = _combo_key(part_id, color_key)
                entry = entries.get(key)
                if entry is None:
                    continue
                new_price = _parse_price(row[4])
                if new_price != entry.unit_price:
                    entry.unit_price = new_price
                    entries[key] = entry
                    changed += 1
            if changed:
                self._save()
        return changed

    def capture_path(self, part_id: str, color_key: str | None) -> Path | None:
        entry = self.get(part_id, color_key)
        if entry is None or not entry.image_file:
            return None
        rel = Path(entry.image_file)
        if rel.is_absolute() or ".." in rel.parts:
            return None
        path = (self.base_dir / rel).resolve()
        try:
            path.relative_to(self.base_dir.resolve())
        except ValueError:
            return None
        return path if path.is_file() else None

    def sync_from_session_scans(self, sessions_dir: Path) -> int:
        """Upsert entries from every scans.csv under ``sessions_dir``.

        Existing keys are refreshed without inflating ``times_seen``.
        Returns the number of newly created unique part+color rows.
        """
        added = 0
        if not sessions_dir.exists():
            return 0
        for scans_path in sorted(sessions_dir.glob("*/scans.csv")):
            try:
                with scans_path.open(encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        part_id = (row.get("part_id") or "").strip()
                        if not part_id:
                            continue
                        color_key = row.get("color_key") or ""
                        before = self.get(part_id, color_key)
                        capture = None
                        image_file = (row.get("image_file") or "").strip()
                        if image_file and (before is None or not before.image_file):
                            src = scans_path.parent / image_file
                            if src.is_file():
                                try:
                                    with Image.open(src) as im:
                                        capture = im.convert("RGB")
                                except OSError:
                                    capture = None
                        self.upsert(
                            part_id=part_id,
                            part_name=row.get("part_name") or part_id,
                            category=row.get("category") or "",
                            item_type=row.get("item_type") or "part",
                            color_name=row.get("color_name") or "",
                            color_key=color_key,
                            capture_image=capture,
                            bump_seen=before is None,
                        )
                        if before is None:
                            added += 1
            except OSError:
                continue
        # Align stored times_seen with actual session appearances when possible.
        counts = {
            key: len(apps)
            for key, apps in self.scan_all_appearances(sessions_dir).items()
        }
        with self._lock:
            entries = self._load()
            changed = False
            for key, entry in entries.items():
                seen = counts.get(key)
                if seen is not None and entry.times_seen != seen:
                    entry.times_seen = seen
                    entries[key] = entry
                    changed = True
            if changed:
                self._save()
        return added


def _parse_price(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().replace("$", "").replace(",", "")
        if not text or text in {"—", "-", "none", "null"}:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
    try:
        price = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if price < 0 or price != price:  # NaN
        return None
    return round(price, 4)


PARTS = PartsCatalog()

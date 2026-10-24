"""Gradio webcam demo: identify LEGO parts via Brickognize."""

from __future__ import annotations

import html
import time

import gradio as gr
import numpy as np
from gradio.components.image_editor import WebcamOptions
from PIL import Image

from feeder.http_client import ConveyorClient, ConveyorError, feeder_base_url
from orchestrator.sort_cycle import (
    DEFAULT_EMPTY_FRAMES,
    DEFAULT_EMPTY_THRESHOLD,
    DEFAULT_OCCUPIED_FRAMES,
    DEFAULT_OCCUPIED_THRESHOLD,
    REFERENCE_PATH,
    IdentifyOutcome,
    LandingRoi,
    SortCycle,
    annotate_capture_frame,
    annotate_landing_roi,
    load_landing_roi,
)

from .brickognize_client import (
    get_api_call_count,
    health_check,
    reset_api_call_count,
)
from .identify import DESK, IdentifyView, identify_from_image, status_with_calls, to_pil
from .render import (
    empty_match_panel,
    format_valued_rows,
    inventory_table_html,
)
from .parts_catalog import PARTS, SESSION_HEADERS, TABLE_HEADERS
from .session_catalog import (
    CATALOG,
    LIVE_SESSION_PICKER_VALUE,
    SCAN_DISPLAY_FIELDS,
    SESSIONS_DIR,
)
from .ui_theme import THEME_FACTORIES, apply_ui_theme, load_ui_theme_name

MIN_SCORE = 0.15
DEFAULT_SETTLE_TIME = 0.5
DEFAULT_MOTION_THRESHOLD = 8.0
STREAM_POLL_INTERVAL = 0.35  # faster poll helps catch landings
LIVE_TIME_LIMIT = 600  # seconds per streaming session

# Hide the raw webcam video on Setup; keep Gradio's access / select / start controls usable.
_SETUP_WEBCAM_CSS = """
#setup_webcam {
  overflow: visible !important;
}
#session-events-log textarea {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
  font-size: 0.82em !important;
  line-height: 1.35 !important;
  white-space: pre !important;
}
/*
 * Hide the raw <video> visually, but keep real layout size.
 * Gradio's stream loop bails while videoWidth/videoHeight are 0 — a 1×1
 * clipped video leaves the control stuck on "Waiting…".
 * Live picture is shown in the Landing zone preview instead.
 */
#setup_webcam video {
  position: absolute !important;
  inset: 0 !important;
  width: 100% !important;
  height: 100% !important;
  opacity: 0 !important;
  pointer-events: none !important;
}
#setup_webcam .wrap > img {
  display: none !important;
}
/* Give the access / start / device-select chrome room to render. */
#setup_webcam .wrap {
  min-height: 9rem !important;
  height: auto !important;
  display: flex !important;
  flex-direction: column !important;
  align-items: stretch !important;
  justify-content: center !important;
  position: relative !important;
}
#setup_webcam .button-wrap,
#setup_webcam select.select-wrap,
#setup_webcam [aria-label="select source"] {
  position: relative !important;
  z-index: 2 !important;
}
#setup_webcam button {
  min-height: 2.75rem;
}
#conveyor_ok {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  min-width: 2.5rem;
  padding-bottom: 0.35rem;
}
/* Reset = operator escape (rebaseline empty + resume). Blue to stand out. */
button.btn-reset,
.btn-reset button {
  background: #2563eb !important;
  border-color: #1d4ed8 !important;
  color: #fff !important;
}
button.btn-reset:hover,
.btn-reset button:hover {
  background: #1d4ed8 !important;
  border-color: #1e40af !important;
}
"""

# After the user picks a camera device, start streaming once the <video>
# has real dimensions (avoids racing Record before getUserMedia is ready).
_SETUP_WEBCAM_JS = """
() => {
  const clickRecord = (root) => {
    const buttons = root.querySelectorAll('button');
    for (const btn of buttons) {
      const label = (btn.getAttribute('aria-label') || '').toLowerCase();
      const text = (btn.textContent || '').toLowerCase();
      const title = (btn.getAttribute('title') || '').toLowerCase();
      if (
        label.includes('start') ||
        text.includes('start') ||
        text.includes('record') ||
        title.includes('start recording')
      ) {
        btn.click();
        return true;
      }
    }
    return false;
  };

  const waitForVideoThenRecord = (root) => {
    let tries = 0;
    const tick = () => {
      const video = root.querySelector('video');
      if (video && video.videoWidth > 0 && video.videoHeight > 0) {
        clickRecord(root);
        return;
      }
      if (++tries < 40) window.setTimeout(tick, 100);
    };
    tick();
  };

  const bind = () => {
    const root = document.querySelector('#setup_webcam');
    if (!root || root.dataset.camBound === '1') return;
    root.dataset.camBound = '1';
    root.addEventListener('change', (e) => {
      const t = e.target;
      if (!(t instanceof HTMLSelectElement)) return;
      waitForVideoThenRecord(root);
    });
  };
  bind();
  window.setInterval(bind, 1500);
}
"""

_latest_frame: np.ndarray | None = None
_last_loop_status = ""
_last_virtual_bin = ""
_last_occupancy_out: float | None = None
_last_session_value = ""


def _sort_identify(frame: np.ndarray) -> IdentifyOutcome:
    """Brickognize identify used by the conveyor sort loop (frozen still only)."""
    image = to_pil(frame)
    if image is None:
        return IdentifyOutcome(error="No image")
    api_crop = SORT_CYCLE.last_api_crop
    crop_rect = SORT_CYCLE.last_crop_rect
    view: IdentifyView = identify_from_image(
        image,
        item_type="part",
        min_score=MIN_SCORE,
        record_history=True,
        frame_np=frame,
        source="sort_loop",
        api_image=api_crop,
        crop_rect=crop_rect,
    )
    err = None
    if view.candidate is None:
        err = "No shape match"
    return IdentifyOutcome(
        candidate=view.candidate,
        color=view.color,
        error=err,
        match_panel=view.match_panel,
        alt_html=view.alt_html,
        status=view.status,
        preview=view.annotated if view.annotated is not None else frame,
        api_calls=view.api_calls,
    )


SORT_CYCLE = SortCycle(
    identify_fn=_sort_identify,
    auto_match=False,
    event_fn=lambda event, fields: CATALOG.append_event(event, **fields),
)
SORT_CYCLE.set_roi(load_landing_roi(), persist=False)


_viewed_session_id: str | None = CATALOG.current_session_id


def _set_viewed_session(session_id: str | None) -> None:
    """Remember which session the Sessions panels are showing."""
    global _viewed_session_id
    _viewed_session_id = session_id or CATALOG.current_session_id


def _resolve_session(picker_value: str | None) -> str | None:
    return CATALOG.resolve_picker_session(picker_value)


def _catalog_state(
    session_id: str | None, *, cache_only: bool = True
) -> tuple[str, str, list[list], str]:
    """Display pieces for one session: stats, inventory, scans rows, events log."""
    if not session_id:
        return (
            "<p style='opacity:0.7'>Select a session to view its summary.</p>",
            inventory_table_html([]),
            [],
            CATALOG.read_events_text(None),
        )
    valuation = CATALOG.inventory_valuation(session_id, cache_only=cache_only)
    return (
        CATALOG.catalog_overview_html(
            session_id, cache_only=cache_only, valuation=valuation
        ),
        inventory_table_html(format_valued_rows(valuation.rows)),
        CATALOG.scans_display_table(session_id),
        CATALOG.read_events_text(session_id),
    )


def _catalog_snapshot(
    *, cache_only: bool = True
) -> tuple[gr.Dropdown, str, str, list[list], str]:
    """Picker + panels snapped to the live scanning session."""
    sid = CATALOG.current_session_id
    _set_viewed_session(sid)
    stats, valued, scans, events = _catalog_state(sid, cache_only=cache_only)
    return (
        gr.Dropdown(
            choices=CATALOG.session_picker_choices(),
            value=CATALOG.default_picker_value(),
        ),
        stats,
        valued,
        scans,
        events,
    )


def _catalog_skip() -> tuple:
    return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip())


def _catalog_refresh_viewed(*, cache_only: bool = True) -> tuple:
    """Refresh panels for the session currently shown (picker selection unchanged)."""
    viewed = _viewed_session_id or CATALOG.current_session_id
    stats, valued, scans, events = _catalog_state(viewed, cache_only=cache_only)
    return (gr.skip(), stats, valued, scans, events)


def _catalog_live_refresh(*, cache_only: bool = True) -> tuple:
    """Stream/match refresh — only when panels are on the live session."""
    current = CATALOG.current_session_id
    viewed = _viewed_session_id or current
    if viewed != current:
        return _catalog_skip()
    return _catalog_refresh_viewed(cache_only=cache_only)


def _view_session(picker_value: str | None) -> tuple[str, str, list[list], str, None]:
    """Load live or a past session from the picker (older sessions are opt-in)."""
    session_id = _resolve_session(picker_value)
    _set_viewed_session(session_id)
    stats, valued, scans, events = _catalog_state(session_id, cache_only=False)
    return stats, valued, scans, events, None


def _preview_scan_capture(
    picker_value: str | None, evt: gr.SelectData
) -> Image.Image | None:
    """Load the saved crop for the clicked Catalog scan row."""
    if evt is None or evt.index is None:
        return None
    row = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    target = _resolve_session(picker_value) or CATALOG.current_session_id
    path = CATALOG.scan_capture_path(target, int(row))
    if path is None:
        return None
    try:
        return Image.open(path).convert("RGB")
    except OSError:
        return None


def _start_new_session() -> tuple:
    CATALOG.start_new_session()
    return (*_catalog_snapshot(), None, _session_value_force(cache_only=True))


def _export_session(picker_value: str | None) -> gr.File:
    """Build a downloadable zip for the selected session and reveal the download."""
    target = _resolve_session(picker_value) or CATALOG.current_session_id
    path = CATALOG.export_zip(target)
    if not path:
        return gr.File(value=None, visible=False)
    return gr.File(value=path, visible=True)


def _value_session(picker_value: str | None) -> tuple[str, str]:
    """Re-fetch BrickLink prices for the selected session."""
    from . import bricklink_client

    # Drop poisoned "no price" cache rows (old WAF failures) before refetching.
    bricklink_client.purge_null_cache_entries()
    target = _resolve_session(picker_value) or CATALOG.current_session_id
    _set_viewed_session(target)
    valuation = CATALOG.inventory_valuation(target)  # full fetch, caches results
    return (
        CATALOG.catalog_overview_html(target, valuation=valuation),
        inventory_table_html(format_valued_rows(valuation.rows)),
    )


_parts_table_keys: list[str] = []
_parts_all_rows: list[list] = []
_parts_all_keys: list[str] = []
_parts_page: int = 0
_parts_selected_key: str | None = None
_parts_session_ids: list[str] = []
_parts_gallery_filter_session: str | None = None
PARTS_PAGE_SIZE = 50


def _parts_status_text() -> str:
    total = len(PARTS.list_entries())
    priced = PARTS.priced_count()
    return f"{total} unique part+color · {priced} priced · {total - priced} need a price"


def _parts_page_count() -> int:
    n = len(_parts_all_keys)
    if n <= 0:
        return 1
    return max(1, (n + PARTS_PAGE_SIZE - 1) // PARTS_PAGE_SIZE)


def _parts_page_label() -> str:
    n = len(_parts_all_keys)
    if n <= 0:
        return "No matching parts"
    start = _parts_page * PARTS_PAGE_SIZE + 1
    end = min(n, (_parts_page + 1) * PARTS_PAGE_SIZE)
    return (
        f"Page {_parts_page + 1} of {_parts_page_count()} · "
        f"showing {start}–{end} of {n}"
    )


def _parts_nav_buttons() -> tuple:
    pages = _parts_page_count()
    has = len(_parts_all_keys) > 0
    return (
        gr.update(interactive=has and _parts_page > 0),
        gr.update(interactive=has and _parts_page < pages - 1),
    )


def _parts_set_page_for_key(key: str | None) -> None:
    """Jump to the page that contains ``key`` (or page 0)."""
    global _parts_page
    if key and key in _parts_all_keys:
        _parts_page = _parts_all_keys.index(key) // PARTS_PAGE_SIZE
    else:
        _parts_page = 0
    _parts_page = max(0, min(_parts_page, _parts_page_count() - 1))


def _parts_current_page_slice() -> tuple[list[list], list[str]]:
    """Rows/keys for the active page; updates ``_parts_table_keys`` for select."""
    global _parts_table_keys
    start = _parts_page * PARTS_PAGE_SIZE
    end = start + PARTS_PAGE_SIZE
    rows = _parts_all_rows[start:end]
    keys = _parts_all_keys[start:end]
    _parts_table_keys = keys
    return rows, keys


def _parts_editor_fields(key: str | None) -> tuple[str, float | None]:
    entry = PARTS.get_by_key(key)
    if entry is None:
        return "_Select a part in the list to edit its price._", None
    color = entry.color_name or entry.color_key or "—"
    apps = PARTS.appearances_for(entry.part_id, entry.color_key, SESSIONS_DIR)
    price_txt = (
        f"${entry.unit_price:.4g}"
        if entry.unit_price is not None
        else "not set"
    )
    info = (
        f"**{entry.part_name}**\n\n"
        f"`{entry.part_id}` · {color} · seen ×{len(apps)} in sessions · "
        f"current price {price_txt}"
    )
    return info, entry.unit_price


def _parts_detail_for_key(key: str | None) -> tuple:
    """Info, price, sessions table, gallery for one catalog key."""
    global _parts_selected_key, _parts_session_ids, _parts_gallery_filter_session
    _parts_selected_key = key
    _parts_gallery_filter_session = None
    info, price = _parts_editor_fields(key)
    entry = PARTS.get_by_key(key)
    if entry is None:
        _parts_session_ids = []
        return info, price, [], []
    session_rows, session_ids = PARTS.session_rows_for(
        entry.part_id, entry.color_key, SESSIONS_DIR
    )
    _parts_session_ids = session_ids
    gallery = PARTS.gallery_items_for(entry.part_id, entry.color_key, SESSIONS_DIR)
    return info, price, session_rows, gallery


def _parts_filter_view(
    query: str | None = "",
    filter_mode: str | None = "needs_price",
    selected_key: str | None = None,
    *,
    reset_page: bool = True,
) -> tuple:
    """Refresh parts table + detail panels for the current search/filter."""
    global _parts_all_rows, _parts_all_keys, _parts_selected_key, _parts_page
    query = query or ""
    filter_mode = filter_mode or "needs_price"
    rows, keys = PARTS.list_table_rows(
        SESSIONS_DIR, query=query, filter_mode=filter_mode
    )
    _parts_all_rows = rows
    _parts_all_keys = keys
    prefer = selected_key if selected_key is not None else _parts_selected_key
    key = prefer if prefer in keys else (keys[0] if keys else None)
    if reset_page:
        _parts_set_page_for_key(key)
    else:
        # Keep the caller's page (prev/next / save). If the preferred key
        # isn't on this page, detail will fall through to the first row.
        _parts_page = max(0, min(_parts_page, _parts_page_count() - 1))
    page_rows, page_keys = _parts_current_page_slice()
    if key not in page_keys:
        key = page_keys[0] if page_keys else None
    _parts_selected_key = key
    info, price, session_rows, gallery = _parts_detail_for_key(key)
    prev_btn, next_btn = _parts_nav_buttons()
    return (
        page_rows,
        info,
        price,
        session_rows,
        gallery,
        _parts_status_text(),
        _parts_page_label(),
        prev_btn,
        next_btn,
    )


def _parts_prev_page(query: str | None, filter_mode: str | None) -> tuple:
    global _parts_page
    if _parts_page > 0:
        _parts_page -= 1
    return _parts_filter_view(
        query, filter_mode, _parts_selected_key, reset_page=False
    )


def _parts_next_page(query: str | None, filter_mode: str | None) -> tuple:
    global _parts_page
    if _parts_page < _parts_page_count() - 1:
        _parts_page += 1
    return _parts_filter_view(
        query, filter_mode, _parts_selected_key, reset_page=False
    )


def _select_part_row(evt: gr.SelectData) -> tuple:
    """Load editor + history when a parts-table row is clicked."""
    if evt is None or evt.index is None:
        return _parts_detail_for_key(_parts_selected_key)
    row = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    if row < 0 or row >= len(_parts_table_keys):
        return _parts_detail_for_key(None)
    return _parts_detail_for_key(_parts_table_keys[int(row)])


def _open_session_from_parts(evt: gr.SelectData) -> tuple:
    """Jump to Sessions tab with the clicked appearance session selected."""
    skip = (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip())
    if evt is None or evt.index is None:
        return skip
    row = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    if row < 0 or row >= len(_parts_session_ids):
        return skip
    session_id = _parts_session_ids[int(row)]
    if session_id == CATALOG.current_session_id:
        picker_value = LIVE_SESSION_PICKER_VALUE
    else:
        picker_value = session_id
    stats, valued, scans, events, preview = _view_session(picker_value)
    return (
        gr.Tabs(selected="advanced"),
        gr.Dropdown(
            choices=CATALOG.session_picker_choices(),
            value=picker_value,
        ),
        stats,
        valued,
        scans,
        events,
        preview,
    )


def _clear_gallery_filter() -> list:
    """Show all capture images for the selected part again."""
    global _parts_gallery_filter_session
    _parts_gallery_filter_session = None
    entry = PARTS.get_by_key(_parts_selected_key)
    if entry is None:
        return []
    return PARTS.gallery_items_for(entry.part_id, entry.color_key, SESSIONS_DIR)


def _save_part_price(
    unit_price: float | None,
    query: str | None,
    filter_mode: str | None,
) -> tuple:
    """Save one part's unit price and refresh list + session totals."""
    key = _parts_selected_key
    entry = PARTS.get_by_key(key)
    if entry is None:
        (
            table,
            info,
            price,
            sessions,
            gallery,
            status,
            page_label,
            prev_btn,
            next_btn,
        ) = _parts_filter_view(query, filter_mode, key)
        return (
            table,
            info,
            price,
            sessions,
            gallery,
            f"Nothing selected. {status}",
            page_label,
            prev_btn,
            next_btn,
            *_catalog_refresh_viewed(cache_only=True),
            _session_value_force(cache_only=True),
        )
    PARTS.set_unit_price(entry.part_id, entry.color_key, unit_price)
    (
        table,
        info,
        price,
        sessions,
        gallery,
        status,
        page_label,
        prev_btn,
        next_btn,
    ) = _parts_filter_view(query, filter_mode, entry.key, reset_page=False)
    saved = (
        f"Saved ${float(unit_price):.4g} for {entry.part_id}."
        if unit_price is not None
        else f"Cleared price for {entry.part_id}."
    )
    return (
        table,
        info,
        price,
        sessions,
        gallery,
        f"{saved} {status}",
        page_label,
        prev_btn,
        next_btn,
        *_catalog_refresh_viewed(cache_only=True),
        _session_value_force(cache_only=True),
    )


def _sync_parts_from_sessions(
    query: str | None, filter_mode: str | None
) -> tuple:
    added = PARTS.sync_from_session_scans(SESSIONS_DIR)
    (
        table,
        info,
        price,
        sessions,
        gallery,
        status,
        page_label,
        prev_btn,
        next_btn,
    ) = _parts_filter_view(query, filter_mode, _parts_selected_key, reset_page=False)
    note = (
        f"Imported {added} new part+color from sessions."
        if added
        else "No new parts found in sessions."
    )
    return (
        table,
        info,
        price,
        sessions,
        gallery,
        f"{note} {status}",
        page_label,
        prev_btn,
        next_btn,
    )


def _refresh_parts_catalog(query: str | None, filter_mode: str | None) -> tuple:
    PARTS.reload()
    return _parts_filter_view(
        query, filter_mode, _parts_selected_key, reset_page=False
    )


def identify_piece(
    frame,
    item_type: str,
    min_score: float,
) -> tuple:
    empty_alts = "<p><em>No alternatives yet.</em></p>"
    image = to_pil(frame)
    if image is None:
        return (
            empty_match_panel("Upload a photo or use the live camera."),
            empty_alts,
            status_with_calls("Waiting for image"),
            get_api_call_count(),
            *_catalog_skip(),
            gr.skip(),
        )
    view = identify_from_image(
        image, item_type, min_score, record_history=True, source="upload"
    )
    return (
        view.match_panel,
        view.alt_html,
        view.status,
        view.api_calls,
        *_catalog_live_refresh(),
        _session_value_force(),
    )


def reset_call_counter() -> int:
    reset_api_call_count()
    DESK.reset_motion_state()
    return 0


def _startup_status() -> str:
    if health_check():
        return "Brickognize API is reachable."
    return "Brickognize API is not reachable right now (you can still try Identify)."


def _loop_status_or_skip(message: str) -> str | dict:
    global _last_loop_status
    if message == _last_loop_status:
        return gr.skip()
    _last_loop_status = message
    return message


def _virtual_bin_or_skip(bin_id: str) -> str | dict:
    global _last_virtual_bin
    label = bin_id or "—"
    if label == _last_virtual_bin:
        return gr.skip()
    _last_virtual_bin = label
    return label


def _occupancy_out(value: float | None) -> float:
    """Always refresh the occupancy readout (helps tune stop threshold)."""
    global _last_occupancy_out
    out = float(value) if value is not None else -1.0
    _last_occupancy_out = out
    return round(out, 2)


def _session_value_text(*, cache_only: bool = True) -> str:
    """Running session total from Parts catalog (+ BrickLink) prices."""
    sid = CATALOG.current_session_id
    if not CATALOG.read_scans(sid):
        return "—"
    valuation = CATALOG.inventory_valuation(sid, cache_only=cache_only)
    if valuation.priced_combos == 0:
        if valuation.unpriced_combos:
            return f"— ({valuation.unpriced_combos} need price)"
        return "—"
    text = f"{valuation.total_value:,.2f} {valuation.currency}"
    if valuation.unpriced_combos:
        text += f" · {valuation.unpriced_combos} unpriced"
    return text


def _session_value_or_skip(*, cache_only: bool = True) -> str | dict:
    global _last_session_value
    text = _session_value_text(cache_only=cache_only)
    if text == _last_session_value:
        return gr.skip()
    _last_session_value = text
    return text


def _session_value_force(*, cache_only: bool = True) -> str:
    """Always emit the current total (after match / price save / new session)."""
    global _last_session_value
    text = _session_value_text(cache_only=cache_only)
    _last_session_value = text
    return text


def _conveyor_from_url(base_url: str) -> ConveyorClient:
    url = (base_url or "").strip() or feeder_base_url()
    return ConveyorClient(url)


def conveyor_test(base_url: str) -> str:
    """Ping conveyor; return a compact ✓ / ✗ HTML indicator (tooltip has detail)."""
    url = (base_url or "").strip()
    if not url:
        return (
            '<span title="Enter a conveyor URL" '
            'style="font-size:1.75rem;line-height:1;opacity:0.45">○</span>'
        )
    try:
        client = _conveyor_from_url(url)
        msg = client.ping()
        SORT_CYCLE.set_feeder_url(client.base_url)
        return (
            f'<span title="{html.escape(msg)}" '
            f'style="font-size:1.75rem;line-height:1;color:#2ecc71;font-weight:700">✓</span>'
        )
    except ConveyorError as exc:
        return (
            f'<span title="{html.escape(str(exc))}" '
            f'style="font-size:1.75rem;line-height:1;color:#e74c3c;font-weight:700">✗</span>'
        )


_last_auto_ping_url = ""
_last_auto_ping_at = 0.0


def conveyor_test_debounced(base_url: str) -> str:
    """Auto-check on URL edits without hammering the device every keystroke."""
    global _last_auto_ping_url, _last_auto_ping_at
    url = (base_url or "").strip()
    now = time.monotonic()
    if url == _last_auto_ping_url and (now - _last_auto_ping_at) < 1.0:
        return gr.skip()
    _last_auto_ping_url = url
    _last_auto_ping_at = now
    return conveyor_test(url)


def _belt_panel_link(base_url: str) -> object:
    """Keep the Belt Control Panel button pointed at the conveyor URL."""
    url = (base_url or "").strip().rstrip("/") or feeder_base_url()
    return gr.update(link=url)


def _roi_from_sliders(
    roi_top: float,
    roi_bottom: float,
    roi_left: float,
    roi_right: float,
) -> LandingRoi:
    return LandingRoi(
        top=roi_top, bottom=roi_bottom, left=roi_left, right=roi_right
    ).clamped()


def _annotated_live(
    frame: np.ndarray | None,
    roi: LandingRoi,
) -> object:
    if frame is None or not isinstance(frame, np.ndarray):
        return gr.skip()
    return annotate_landing_roi(
        frame, roi, blob_rect=SORT_CYCLE.last_live_blob_rect
    )


def _load_reference_preview(roi: LandingRoi | None = None) -> object:
    if not REFERENCE_PATH.is_file():
        return None
    image = Image.open(REFERENCE_PATH).convert("RGB")
    arr = np.asarray(image)
    use_roi = roi if roi is not None else SORT_CYCLE.roi
    return annotate_capture_frame(arr, use_roi)


def _setup_ready_message() -> str:
    if SORT_CYCLE.has_empty_reference or REFERENCE_PATH.is_file():
        return "Ready — empty reference is set. Continue to Feed loop."
    return "Not ready — start the camera, tune the landing zone, then capture an empty reference."


def _start_gate(*, ready: bool) -> dict:
    """Enable Start feed loop only once an empty reference exists."""
    return gr.update(interactive=ready)


def capture_empty_reference(
    roi_top: float,
    roi_bottom: float,
    roi_left: float,
    roi_right: float,
) -> tuple:
    """Save empty tray reference and unlock Feed loop."""
    global _latest_frame
    if _latest_frame is None:
        return (
            "No camera frame yet — enable the camera first.",
            gr.skip(),
            gr.skip(),
            _start_gate(ready=False),
            gr.skip(),
        )
    roi = _roi_from_sliders(roi_top, roi_bottom, roi_left, roi_right)
    SORT_CYCLE.set_roi(roi)
    SORT_CYCLE.set_empty_reference(_latest_frame, save=True)
    preview = annotate_capture_frame(_latest_frame, roi)
    # Seed noise floor with this frame vs itself (=0); live stream will raise it.
    ready_msg = (
        f"Empty reference saved. Zone top={roi.top:.0%} → bottom={roi.bottom:.0%}. "
        "Keep the tray empty a moment so noise can calibrate before starting."
    )
    return (
        ready_msg,
        preview,
        preview,
        _start_gate(ready=True),
        gr.Tabs(selected="feed"),
    )


def update_zone_preview(
    roi_top: float,
    roi_bottom: float,
    roi_left: float,
    roi_right: float,
) -> object:
    """Slider move: update overlay and persist zone for the next session."""
    global _latest_frame
    if _latest_frame is None:
        # Still save so a refresh keeps the last knobs even before the camera starts.
        SORT_CYCLE.set_roi(
            _roi_from_sliders(roi_top, roi_bottom, roi_left, roi_right),
            persist=True,
        )
        return gr.skip()
    roi = _roi_from_sliders(roi_top, roi_bottom, roi_left, roi_right)
    SORT_CYCLE.set_roi(roi, persist=True)
    return annotate_landing_roi(_latest_frame, roi)


def start_sort_loop(base_url: str) -> tuple[str, str, float]:
    if not SORT_CYCLE.has_empty_reference and not SORT_CYCLE.load_empty_reference():
        return (
            "Idle: capture an empty reference in Setup first.",
            "—",
            _occupancy_out(None),
        )
    SORT_CYCLE.set_feeder_url((base_url or "").strip() or feeder_base_url())
    update = SORT_CYCLE.start_loop()
    return (
        f"{update.state.value}: {update.status}",
        update.virtual_bin or "—",
        _occupancy_out(update.occupancy),
    )


def stop_sort_loop() -> tuple[str, str, float]:
    update = SORT_CYCLE.stop_loop()
    return (
        f"{update.state.value}: {update.status}",
        update.virtual_bin or SORT_CYCLE.last_bin or "—",
        _occupancy_out(update.occupancy),
    )


def force_clear_tray() -> tuple:
    """Re-baseline (+ save) empty reference from the latest frame; resume if WaitClear."""
    global _latest_frame
    update = SORT_CYCLE.force_clear(_latest_frame)
    preview = gr.skip()
    if _latest_frame is not None and isinstance(_latest_frame, np.ndarray):
        preview = annotate_capture_frame(_latest_frame, SORT_CYCLE.roi)
    gate = _start_gate(ready=SORT_CYCLE.has_empty_reference)
    return (
        f"{update.state.value}: {update.status}",
        update.virtual_bin or SORT_CYCLE.last_bin or "—",
        _occupancy_out(update.occupancy if update.occupancy is not None else 0.0),
        preview,
        gate,
        _setup_ready_message() if SORT_CYCLE.has_empty_reference else (
            update.status or _setup_ready_message()
        ),
    )


def _initial_setup_state() -> tuple:
    """On load: restore reference image, ROI sliders, and gate buttons."""
    loaded = SORT_CYCLE.load_empty_reference()
    roi = load_landing_roi()
    SORT_CYCLE.set_roi(roi, persist=False)
    preview = _load_reference_preview(roi) if loaded else None
    ready = loaded or SORT_CYCLE.has_empty_reference
    msg = _setup_ready_message()
    return (
        preview,
        msg,
        _start_gate(ready=ready),
        gr.update(value=roi.top),
        gr.update(value=roi.bottom),
        gr.update(value=roi.left),
        gr.update(value=roi.right),
        *_catalog_snapshot(cache_only=True),
        _session_value_force(cache_only=True),
    )


def stream_frame(
    frame,
    item_type: str,
    min_score: float,
    live_enabled: bool,
    settle_time: float,
    motion_threshold: float,
    auto_match: bool,
    occupied_threshold: float,
    empty_threshold: float,
    occupied_frames: float,
    empty_frames: float,
    roi_top: float,
    roi_bottom: float,
    roi_left: float,
    roi_right: float,
) -> tuple:
    """Single webcam stream → Setup zone preview + Feed live view."""
    global _latest_frame

    if frame is not None and isinstance(frame, np.ndarray):
        _latest_frame = frame

    roi = _roi_from_sliders(roi_top, roi_bottom, roi_left, roi_right)
    SORT_CYCLE.set_roi(roi, persist=False)

    # Outputs: zone_preview, feed_live, last_capture, match, alts, status, api_calls,
    #          *catalog(5), loop_status, virtual_bin, occupancy, session_value
    catalog_idle = _catalog_skip()
    frame_np = frame if isinstance(frame, np.ndarray) else None

    # Run vision before annotating so the amber blob outline matches this frame.
    if (
        not SORT_CYCLE.active
        and frame_np is not None
        and SORT_CYCLE.has_empty_reference
    ):
        SORT_CYCLE.observe_frame(frame_np, roi)

    if SORT_CYCLE.active:
        update = SORT_CYCLE.on_frame(
            frame_np,
            settle_s=settle_time,
            motion_threshold=motion_threshold,
            occupied_threshold=occupied_threshold,
            empty_threshold=empty_threshold,
            occupied_frames=int(occupied_frames),
            empty_frames=int(empty_frames),
            auto_match=bool(auto_match),
            roi_top=roi_top,
            roi_bottom=roi_bottom,
            roi_left=roi_left,
            roi_right=roi_right,
        )
        live_annot = _annotated_live(frame_np, roi)
        loop_status = _loop_status_or_skip(f"{update.state.value}: {update.status}")
        bin_out = _virtual_bin_or_skip(update.virtual_bin or SORT_CYCLE.last_bin)
        occ_out = _occupancy_out(update.occupancy)

        if update.captured and update.preview is not None:
            # Show the padded blob/ROI crop actually uploaded to Brickognize.
            capture = (
                SORT_CYCLE.last_api_crop
                if SORT_CYCLE.last_api_crop is not None
                else annotate_capture_frame(update.preview, SORT_CYCLE.roi)
            )
            return (
                live_annot,
                live_annot,
                capture,
                gr.skip(),
                gr.skip(),
                DESK.status_or_skip(update.status),
                DESK.api_calls_or_skip(),
                *catalog_idle,
                loop_status,
                bin_out,
                occ_out,
                _session_value_or_skip(),
            )

        if update.matched and update.identify is not None:
            ident = update.identify
            # Plain API crop only — no bbox/zone markers on Last capture.
            capture = (
                SORT_CYCLE.last_api_crop
                if SORT_CYCLE.last_api_crop is not None
                else gr.skip()
            )
            match = ident.match_panel or gr.skip()
            alts = ident.alt_html or gr.skip()
            calls = ident.api_calls if ident.api_calls else get_api_call_count()
            return (
                live_annot,
                live_annot,
                capture,
                match,
                alts,
                status_with_calls(update.status),
                calls,
                *_catalog_live_refresh(),
                loop_status,
                bin_out,
                occ_out,
                _session_value_force(),
            )

        return (
            live_annot,
            live_annot,
            gr.skip(),
            gr.skip(),
            gr.skip(),
            DESK.status_or_skip(update.status),
            DESK.api_calls_or_skip(),
            *catalog_idle,
            loop_status,
            bin_out,
            occ_out,
            _session_value_or_skip(),
        )

    live_annot = _annotated_live(frame_np, roi)

    # Loop idle: optional desk auto-scan (advanced tab), still refresh live views.
    if live_enabled:
        result = DESK.identify_live_frame(
            frame,
            item_type,
            min_score,
            live_enabled,
            settle_time,
            motion_threshold,
        )
        if result is not None:
            # result: preview, match, alts, status, calls, *catalog
            return (
                live_annot,
                live_annot,
                result[0],
                result[1],
                result[2],
                result[3],
                result[4],
                *result[5:],
                _loop_status_or_skip("Idle — start feed loop when ready"),
                _virtual_bin_or_skip(SORT_CYCLE.last_bin or "—"),
                _occupancy_out(None),
                _session_value_force(),
            )

    return (
        live_annot,
        live_annot,
        gr.skip(),
        gr.skip(),
        gr.skip(),
        DESK.status_or_skip("Camera live — use Setup, then Feed loop"),
        DESK.api_calls_or_skip(),
        *catalog_idle,
        _loop_status_or_skip("Idle — start feed loop when ready"),
        _virtual_bin_or_skip(SORT_CYCLE.last_bin or "—"),
        _occupancy_out(None),
        _session_value_or_skip(),
    )


def build_app() -> gr.Blocks:
    saved_roi = load_landing_roi()
    SORT_CYCLE.set_roi(saved_roi, persist=False)

    with gr.Blocks(title="LEGO Sorter") as demo:
        gr.Markdown("# LEGO Sorter")
        gr.Markdown(_startup_status())

        with gr.Tabs() as main_tabs:
            # —— Setup ————————————————————————————————————————————————
            with gr.Tab("Setup", id="setup"):
                with gr.Row():
                    with gr.Column(scale=3, min_width=280):
                        webcam = gr.Image(
                            sources=["webcam"],
                            type="numpy",
                            label="Camera — access, pick device, start (live view below)",
                            height=140,
                            streaming=True,
                            elem_id="setup_webcam",
                            webcam_options=WebcamOptions(mirror=False),
                        )
                    with gr.Column(scale=2, min_width=260):
                        with gr.Row(equal_height=True):
                            feeder_url = gr.Textbox(
                                label="Conveyor URL",
                                value=feeder_base_url(),
                                scale=5,
                                max_lines=1,
                                container=True,
                            )
                            conveyor_ok = gr.HTML(
                                value=(
                                    '<span title="Checking…" '
                                    'style="font-size:1.75rem;line-height:1;opacity:0.45">…</span>'
                                ),
                                show_label=False,
                                elem_id="conveyor_ok",
                                min_width=40,
                                scale=1,
                            )

                with gr.Row(equal_height=True):
                    zone_preview = gr.Image(
                        label="Landing zone (live — bright = stop area)",
                        height=380,
                        interactive=False,
                        scale=3,
                    )
                    with gr.Column(scale=2):
                        gr.Markdown(
                            "Raise **Ignore top** if the belt stops before the part "
                            "reaches the capture area."
                        )
                        roi_top = gr.Slider(
                            minimum=0.0,
                            maximum=0.8,
                            value=saved_roi.top,
                            step=0.01,
                            label="Ignore top of frame",
                        )
                        roi_bottom = gr.Slider(
                            minimum=0.2,
                            maximum=1.0,
                            value=saved_roi.bottom,
                            step=0.01,
                            label="Ignore bottom of frame",
                        )
                        roi_left = gr.Slider(
                            minimum=0.0,
                            maximum=0.8,
                            value=saved_roi.left,
                            step=0.01,
                            label="Ignore left side",
                        )
                        roi_right = gr.Slider(
                            minimum=0.2,
                            maximum=1.0,
                            value=saved_roi.right,
                            step=0.01,
                            label="Ignore right side",
                        )

                with gr.Row(equal_height=True):
                    capture_ref_btn = gr.Button(
                        "Capture empty reference (clear the tray first)",
                        variant="primary",
                        scale=2,
                    )
                    setup_ready = gr.Textbox(
                        label="Setup status",
                        value=_setup_ready_message(),
                        interactive=False,
                        max_lines=2,
                        scale=3,
                    )
                with gr.Accordion(
                    "Saved empty reference (occupancy baseline)", open=False
                ):
                    reference_img = gr.Image(
                        show_label=False,
                        height=240,
                        interactive=False,
                    )
                with gr.Accordion("Vision thresholds", open=False):
                    with gr.Row():
                        occupied_threshold = gr.Slider(
                            minimum=0.1,
                            maximum=8.0,
                            value=DEFAULT_OCCUPIED_THRESHOLD,
                            step=0.05,
                            label="Blob occupancy % to STOP (largest changed region)",
                        )
                        empty_threshold = gr.Slider(
                            minimum=0.05,
                            maximum=8.0,
                            value=DEFAULT_EMPTY_THRESHOLD,
                            step=0.05,
                            label="Blob % for empty (auto-raises above measured noise)",
                        )
                    with gr.Row():
                        occupied_frames = gr.Slider(
                            minimum=1,
                            maximum=10,
                            value=DEFAULT_OCCUPIED_FRAMES,
                            step=1,
                            label="Occupied debounce (frames)",
                        )
                        empty_frames = gr.Slider(
                            minimum=1,
                            maximum=12,
                            value=DEFAULT_EMPTY_FRAMES,
                            step=1,
                            label="Empty debounce (frames)",
                        )
                with gr.Accordion("Motion tuning", open=False):
                    settle_time = gr.Slider(
                        minimum=0.3,
                        maximum=2.0,
                        value=DEFAULT_SETTLE_TIME,
                        step=0.1,
                        label="Settle time after STOP (s)",
                    )
                    motion_threshold = gr.Slider(
                        minimum=3.0,
                        maximum=30.0,
                        value=DEFAULT_MOTION_THRESHOLD,
                        step=1.0,
                        label="Motion sensitivity (lower = stricter)",
                    )

            # —— Feed loop ————————————————————————————————————————————
            with gr.Tab("Feed loop", id="feed"):
                with gr.Row():
                    start_loop_btn = gr.Button(
                        "Start",
                        variant="primary",
                        interactive=False,
                    )
                    stop_loop_btn = gr.Button("Stop", variant="stop")
                    force_clear_btn = gr.Button(
                        "Reset",
                        elem_classes=["btn-reset"],
                    )
                    belt_panel_btn = gr.Button(
                        "Open belt web UI",
                        link=feeder_base_url(),
                        link_target="_blank",
                    )
                gr.Markdown(
                    "If **blob occupancy** stays high on an empty tray, click "
                    "**Reset** — that saves a new empty reference from the "
                    "live frame and resumes the belt. "
                    "Tune stop/empty thresholds under **Setup → Vision thresholds**. "
                    "Auto-match lives under **Settings**."
                )
                with gr.Row():
                    loop_status = gr.Textbox(
                        label="Loop status",
                        value="Idle",
                        interactive=False,
                        scale=3,
                    )
                    occupancy_readout = gr.Number(
                        label="Blob occupancy %",
                        value=-1,
                        interactive=False,
                        precision=2,
                        scale=1,
                    )
                    virtual_bin = gr.Textbox(
                        label="Virtual bin",
                        value="—",
                        interactive=False,
                        scale=1,
                    )
                    session_value = gr.Textbox(
                        label="Session value",
                        value=_session_value_text(cache_only=True),
                        interactive=False,
                        scale=1,
                    )
                    api_calls = gr.Number(
                        label="API calls",
                        value=0,
                        interactive=False,
                        precision=0,
                        scale=1,
                        minimum=0,
                    )

                with gr.Row(equal_height=True):
                    with gr.Column(scale=4):
                        feed_live = gr.Image(
                            label="Live (green zone · amber blob)",
                            height=380,
                            interactive=False,
                        )
                    with gr.Column(scale=4):
                        last_capture = gr.Image(
                            label="Last capture (API crop → Brickognize)",
                            height=380,
                            interactive=False,
                        )
                    with gr.Column(scale=4):
                        match_panel = gr.HTML(
                            value=empty_match_panel("Waiting for first capture…")
                        )
                        status = gr.Textbox(
                            label="Status",
                            interactive=False,
                            max_lines=2,
                        )
                        with gr.Accordion("Alternatives", open=False):
                            alternatives = gr.HTML()

            # —— Sessions ——————————————————————————————————————————————
            with gr.Tab("Sessions", id="advanced"):
                with gr.Row():
                    session_picker = gr.Dropdown(
                        label="Session (live by default — pick another to browse history)",
                        choices=CATALOG.session_picker_choices(),
                        value=CATALOG.default_picker_value(),
                        scale=4,
                    )
                    new_session_btn = gr.Button("＋ New", scale=1)
                    refresh_catalog_btn = gr.Button("Live", scale=1)
                catalog_stats = gr.HTML(
                    value=CATALOG.catalog_overview_html(
                        CATALOG.current_session_id, cache_only=True
                    )
                )
                with gr.Row():
                    value_btn = gr.Button(
                        "Estimate value (BrickLink fallback)",
                        variant="secondary",
                        scale=1,
                    )
                    export_btn = gr.Button("Export session (.zip)", scale=1)
                export_file = gr.File(
                    label="Download",
                    interactive=False,
                    visible=False,
                )
                valued_inventory = gr.HTML(
                    value=inventory_table_html(
                        format_valued_rows(
                            CATALOG.inventory_valuation(
                                CATALOG.current_session_id, cache_only=True
                            ).rows
                        )
                    )
                )
                with gr.Row():
                    all_scans = gr.Dataframe(
                        headers=list(SCAN_DISPLAY_FIELDS),
                        value=CATALOG.scans_display_table(
                            CATALOG.current_session_id
                        ),
                        label="Scans — click a row to preview the capture crop",
                        interactive=False,
                        wrap=True,
                        max_height=360,
                        show_row_numbers=True,
                        scale=3,
                    )
                    scan_preview = gr.Image(
                        label="Scan capture",
                        interactive=False,
                        height=360,
                        scale=2,
                    )
                with gr.Accordion("Event log (capture / match / resume)", open=False):
                    events_log = gr.Textbox(
                        show_label=False,
                        value=CATALOG.read_events_text(CATALOG.current_session_id),
                        lines=8,
                        max_lines=16,
                        interactive=False,
                        elem_id="session-events-log",
                    )

            # —— Parts catalog ————————————————————————————————————————
            with gr.Tab("Parts catalog", id="parts"):
                gr.Markdown(
                    "Pick a **part + color** from the list, set its **unit price**, "
                    "and inspect every session / capture. Session totals use these "
                    "prices first."
                )
                parts_status = gr.Markdown(_parts_status_text())
                with gr.Row():
                    parts_search = gr.Textbox(
                        label="Search",
                        placeholder="part id, name, or color",
                        scale=3,
                    )
                    parts_filter = gr.Dropdown(
                        choices=[
                            ("Needs a price", "needs_price"),
                            ("Already priced", "priced"),
                            ("All parts", "all"),
                        ],
                        value="needs_price",
                        label="Show",
                        scale=2,
                    )
                    sync_parts_btn = gr.Button("Import from sessions", scale=1)
                    refresh_parts_btn = gr.Button("↻ Refresh", scale=1)
                _parts_rows0, _parts_keys0 = PARTS.list_table_rows(
                    SESSIONS_DIR, filter_mode="needs_price"
                )
                _parts_all_rows.clear()
                _parts_all_keys.clear()
                _parts_all_rows.extend(_parts_rows0)
                _parts_all_keys.extend(_parts_keys0)
                _parts_set_page_for_key(None)
                _parts_page_rows0, _parts_page_keys0 = _parts_current_page_slice()
                _parts_key0 = _parts_page_keys0[0] if _parts_page_keys0 else None
                _parts_info0, _parts_price0, _parts_sess0, _parts_gal0 = (
                    _parts_detail_for_key(_parts_key0)
                )
                with gr.Row(equal_height=True):
                    with gr.Column(scale=3):
                        parts_table = gr.Dataframe(
                            headers=list(TABLE_HEADERS),
                            value=_parts_page_rows0,
                            label="Parts — click a row",
                            interactive=False,
                            wrap=True,
                            max_height=420,
                            show_row_numbers=True,
                        )
                        with gr.Row():
                            parts_prev_btn = gr.Button(
                                "← Prev",
                                interactive=False,
                                scale=1,
                            )
                            parts_page_label = gr.Markdown(
                                value=_parts_page_label(),
                                scale=3,
                            )
                            parts_next_btn = gr.Button(
                                "Next →",
                                interactive=len(_parts_all_keys) > PARTS_PAGE_SIZE,
                                scale=1,
                            )
                    with gr.Column(scale=2):
                        parts_info = gr.Markdown(value=_parts_info0)
                        parts_price = gr.Number(
                            label="Unit price (USD)",
                            value=_parts_price0,
                            minimum=0,
                            precision=4,
                        )
                        save_parts_btn = gr.Button("Save price", variant="primary")
                with gr.Row(equal_height=True):
                    with gr.Column(scale=2):
                        parts_sessions = gr.Dataframe(
                            headers=list(SESSION_HEADERS),
                            value=_parts_sess0,
                            label="Sessions — click a row to open it",
                            interactive=False,
                            wrap=True,
                            max_height=260,
                            show_row_numbers=True,
                        )
                        clear_gallery_filter_btn = gr.Button("All images")
                    with gr.Column(scale=3):
                        parts_gallery = gr.Gallery(
                            label="Captures",
                            value=_parts_gal0,
                            columns=4,
                            height=320,
                            object_fit="contain",
                            preview=True,
                        )

            # —— Robot ————————————————————————————————————————————————
            with gr.Tab("Robot", id="robot"):
                try:
                    from robot.teach_app import mount_teach_ui

                    mount_teach_ui(demo, heading=True)
                except ImportError:
                    gr.Markdown(
                        "Robot UI needs CRI: "
                        "`pip install -r requirements-robot.txt` "
                        "then restart this app. "
                        "Close Windows **iRC** before connecting."
                    )

            # —— Settings ——————————————————————————————————————————————
            with gr.Tab("Settings", id="settings"):
                gr.Markdown(
                    "Theme, desk auto-scan, feed auto-match, still-photo identify, "
                    "and Brickognize options."
                )
                with gr.Row():
                    theme_picker = gr.Dropdown(
                        choices=sorted(THEME_FACTORIES),
                        value=load_ui_theme_name(),
                        label="UI theme (applies live, saved for next launch)",
                        scale=2,
                    )
                    live_enabled = gr.Checkbox(
                        label="Auto-scan when stable (desk mode — leave off for feed loop)",
                        value=False,
                        scale=3,
                    )
                auto_match = gr.Checkbox(
                    label="Auto-match after capture (Brickognize)",
                    value=True,
                )
                theme_css_sink = gr.HTML(visible=True, show_label=False)
                with gr.Accordion("Still photo", open=False):
                    with gr.Row():
                        upload = gr.Image(
                            sources=["upload"],
                            type="pil",
                            label="Photo",
                            height=200,
                            scale=2,
                        )
                        with gr.Column(scale=1):
                            identify_btn = gr.Button("Identify", variant="primary")
                with gr.Accordion("Identify options", open=False):
                    with gr.Row():
                        item_type = gr.Dropdown(
                            choices=[
                                ("Parts", "part"),
                                ("Sets", "set"),
                                ("Minifigs", "fig"),
                                ("Anything", "any"),
                            ],
                            value="part",
                            label="Search type",
                            scale=2,
                        )
                        min_score = gr.Slider(
                            minimum=0.0,
                            maximum=0.9,
                            value=MIN_SCORE,
                            step=0.05,
                            label="Minimum confidence",
                            scale=2,
                        )
                        reset_btn = gr.Button("Reset API counter", scale=1)

        # —— Wiring ————————————————————————————————————————————————————
        catalog_outputs = [
            session_picker,
            catalog_stats,
            valued_inventory,
            all_scans,
            events_log,
        ]
        scan_result_outputs = [match_panel, alternatives, status, api_calls]
        still_inputs = [upload, item_type, min_score]
        roi_inputs = [roi_top, roi_bottom, roi_left, roi_right]

        live_inputs = [
            webcam,
            item_type,
            min_score,
            live_enabled,
            settle_time,
            motion_threshold,
            auto_match,
            occupied_threshold,
            empty_threshold,
            occupied_frames,
            empty_frames,
            *roi_inputs,
        ]
        live_outputs = [
            zone_preview,
            feed_live,
            last_capture,
            *scan_result_outputs,
            *catalog_outputs,
            loop_status,
            virtual_bin,
            occupancy_readout,
            session_value,
        ]

        demo.load(
            _initial_setup_state,
            outputs=[
                reference_img,
                setup_ready,
                start_loop_btn,
                roi_top,
                roi_bottom,
                roi_left,
                roi_right,
                *catalog_outputs,
                session_value,
            ],
            show_progress="hidden",
        )

        feeder_url.change(
            fn=conveyor_test_debounced,
            inputs=feeder_url,
            outputs=conveyor_ok,
            show_progress="hidden",
        )
        feeder_url.change(
            fn=_belt_panel_link,
            inputs=feeder_url,
            outputs=belt_panel_btn,
            show_progress="hidden",
        )
        feeder_url.submit(
            fn=conveyor_test,
            inputs=feeder_url,
            outputs=conveyor_ok,
            show_progress="hidden",
        )
        demo.load(
            fn=conveyor_test,
            inputs=feeder_url,
            outputs=conveyor_ok,
            show_progress="hidden",
        )
        demo.load(
            fn=_belt_panel_link,
            inputs=feeder_url,
            outputs=belt_panel_btn,
            show_progress="hidden",
        )

        capture_ref_btn.click(
            fn=capture_empty_reference,
            inputs=roi_inputs,
            outputs=[
                setup_ready,
                reference_img,
                zone_preview,
                start_loop_btn,
                main_tabs,
            ],
        )

        for roi_slider in roi_inputs:
            # Persist on change and release — Gradio release is easy to miss.
            roi_slider.release(
                fn=update_zone_preview, inputs=roi_inputs, outputs=zone_preview
            )
            roi_slider.change(
                fn=update_zone_preview, inputs=roi_inputs, outputs=zone_preview
            )

        start_loop_btn.click(
            fn=start_sort_loop,
            inputs=feeder_url,
            outputs=[loop_status, virtual_bin, occupancy_readout],
        )
        stop_loop_btn.click(
            fn=stop_sort_loop,
            outputs=[loop_status, virtual_bin, occupancy_readout],
        )
        force_clear_btn.click(
            fn=force_clear_tray,
            outputs=[
                loop_status,
                virtual_bin,
                occupancy_readout,
                reference_img,
                start_loop_btn,
                setup_ready,
            ],
        )

        session_picker.change(
            fn=_view_session,
            inputs=session_picker,
            outputs=[
                catalog_stats,
                valued_inventory,
                all_scans,
                events_log,
                scan_preview,
            ],
            show_progress="hidden",
        )
        all_scans.select(
            fn=_preview_scan_capture,
            inputs=session_picker,
            outputs=scan_preview,
            show_progress="hidden",
        )
        new_session_btn.click(
            fn=_start_new_session,
            outputs=[*catalog_outputs, scan_preview, session_value],
        )
        refresh_catalog_btn.click(fn=_catalog_snapshot, outputs=catalog_outputs)
        export_btn.click(
            fn=_export_session, inputs=session_picker, outputs=export_file
        )
        value_btn.click(
            fn=_value_session,
            inputs=session_picker,
            outputs=[catalog_stats, valued_inventory],
        )

        parts_filter_outputs = [
            parts_table,
            parts_info,
            parts_price,
            parts_sessions,
            parts_gallery,
            parts_status,
            parts_page_label,
            parts_prev_btn,
            parts_next_btn,
        ]
        parts_detail_outputs = [
            parts_info,
            parts_price,
            parts_sessions,
            parts_gallery,
        ]
        parts_search.change(
            fn=_parts_filter_view,
            inputs=[parts_search, parts_filter],
            outputs=parts_filter_outputs,
            show_progress="hidden",
        )
        parts_filter.change(
            fn=_parts_filter_view,
            inputs=[parts_search, parts_filter],
            outputs=parts_filter_outputs,
            show_progress="hidden",
        )
        parts_prev_btn.click(
            fn=_parts_prev_page,
            inputs=[parts_search, parts_filter],
            outputs=parts_filter_outputs,
            show_progress="hidden",
        )
        parts_next_btn.click(
            fn=_parts_next_page,
            inputs=[parts_search, parts_filter],
            outputs=parts_filter_outputs,
            show_progress="hidden",
        )
        parts_table.select(
            fn=_select_part_row,
            outputs=parts_detail_outputs,
            show_progress="hidden",
        )
        parts_sessions.select(
            fn=_open_session_from_parts,
            outputs=[
                main_tabs,
                session_picker,
                catalog_stats,
                valued_inventory,
                all_scans,
                events_log,
                scan_preview,
            ],
            show_progress="hidden",
        )
        clear_gallery_filter_btn.click(
            fn=_clear_gallery_filter,
            outputs=parts_gallery,
            show_progress="hidden",
        )
        save_parts_btn.click(
            fn=_save_part_price,
            inputs=[parts_price, parts_search, parts_filter],
            outputs=[*parts_filter_outputs, *catalog_outputs, session_value],
        )
        sync_parts_btn.click(
            fn=_sync_parts_from_sessions,
            inputs=[parts_search, parts_filter],
            outputs=parts_filter_outputs,
        )
        refresh_parts_btn.click(
            fn=_refresh_parts_catalog,
            inputs=[parts_search, parts_filter],
            outputs=parts_filter_outputs,
        )

        identify_btn.click(
            fn=identify_piece,
            inputs=still_inputs,
            outputs=[*scan_result_outputs, *catalog_outputs, session_value],
            show_progress="full",
        )
        upload.upload(
            fn=identify_piece,
            inputs=still_inputs,
            outputs=[*scan_result_outputs, *catalog_outputs, session_value],
            show_progress="full",
        )
        reset_btn.click(fn=reset_call_counter, inputs=None, outputs=api_calls)

        theme_picker.change(
            fn=apply_ui_theme,
            inputs=theme_picker,
            outputs=theme_css_sink,
            show_progress="hidden",
        )

        webcam.stream(
            fn=stream_frame,
            inputs=live_inputs,
            outputs=live_outputs,
            time_limit=LIVE_TIME_LIMIT,
            stream_every=STREAM_POLL_INTERVAL,
            concurrency_limit=1,
            show_progress="hidden",
        )

    return demo


def main() -> None:
    app = build_app()
    theme_name = load_ui_theme_name()
    app.launch(
        theme=THEME_FACTORIES.get(theme_name, gr.themes.Soft)(),
        css=_SETUP_WEBCAM_CSS,
        js=_SETUP_WEBCAM_JS,
    )


if __name__ == "__main__":
    main()

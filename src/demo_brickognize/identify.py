"""Brickognize identify pipeline, scan history, and desk-mode motion gating."""

from __future__ import annotations

import html
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

import gradio as gr
import numpy as np
import requests
from PIL import Image

from orchestrator.sort_cycle import _to_gray_small

from .brickognize_client import (
    BrickognizeError,
    Candidate,
    get_api_call_count,
    get_last_payload_bytes,
    predict,
)
from .color_detect import (
    ColorDetection,
    annotate_bbox_overlay,
    annotate_rect_overlay,
    detect_color_in_bbox,
    detect_color_in_rect,
    format_color_html,
)
from .render import (
    format_candidate,
    format_links,
    match_panel_html,
    reference_html,
    shape_only_summary,
)
from .session_catalog import CATALOG

logger = logging.getLogger(__name__)


@dataclass
class IdentifyView:
    match_panel: str
    alt_html: str
    status: str
    history_html: str
    api_calls: int
    annotated: np.ndarray | None = None
    candidate: Candidate | None = None
    color: ColorDetection | None = None


class ScanHistory:
    """Last-N match lines for the in-app session history panel."""

    def __init__(self) -> None:
        self._entries: list[str] = []
        self._last_logged_id: str | None = None

    def log(self, top: Candidate, color: ColorDetection | None = None) -> None:
        if color is not None and color.key != "unknown":
            log_key = f"{top.id}:{color.key}"
            if log_key == self._last_logged_id:
                return
            self._last_logged_id = log_key
            stamp = datetime.now().strftime("%H:%M:%S")
            self._entries.insert(
                0,
                f"[{stamp}] {color.name} {top.name} ({top.id}) — "
                f"{top.score * 100:.1f}% shape / {color.confidence * 100:.0f}% color",
            )
        else:
            if top.id == self._last_logged_id:
                return
            self._last_logged_id = top.id
            stamp = datetime.now().strftime("%H:%M:%S")
            self._entries.insert(
                0,
                f"[{stamp}] {top.name} ({top.id}) — {top.score * 100:.1f}% shape",
            )
        del self._entries[10:]

    def html(self) -> str:
        if not self._entries:
            return "<p style='opacity:0.7;margin:0'>No lookups yet this run.</p>"
        rows = "".join(f"<li>{html.escape(line)}</li>" for line in self._entries)
        return (
            "<p style='opacity:0.75;font-size:0.88em;margin:0 0 8px 0'>"
            "Last 10 matches this app session (any catalog session).</p>"
            f"<ul style='margin:0;padding-left:18px'>{rows}</ul>"
        )


HISTORY = ScanHistory()


def to_pil(frame) -> Image.Image | None:
    """Normalize Gradio Image payloads (PIL, numpy, path, or FileData dict)."""
    if frame is None:
        return None
    if isinstance(frame, Image.Image):
        return frame
    if isinstance(frame, np.ndarray):
        return Image.fromarray(frame)
    if isinstance(frame, str):
        return Image.open(frame).convert("RGB")
    if isinstance(frame, dict):
        path = frame.get("path")
        if path:
            return Image.open(path).convert("RGB")
        url = frame.get("url")
        if url and url.startswith("data:"):
            import base64
            import io as io_mod

            header, encoded = url.split(",", 1)
            data = base64.b64decode(encoded)
            return Image.open(io_mod.BytesIO(data)).convert("RGB")
    if hasattr(frame, "path") and frame.path:
        return Image.open(frame.path).convert("RGB")
    return None


def status_with_calls(message: str) -> str:
    return f"{message} · API calls: {get_api_call_count()}"


def _format_result(
    items: list[Candidate],
    color: ColorDetection | None,
) -> tuple[str, str, str]:
    top = items[0]
    alternatives = items[1:5]
    summary = (
        f"<h2 style='margin:0 0 8px 0'>{html.escape(top.name)}</h2>"
        f"<p style='margin:0'>Part <code>{html.escape(top.id)}</code> "
        f"· {html.escape(top.category or top.item_type)} "
        f"· <strong>{top.score * 100:.1f}%</strong> shape</p>"
        f"{format_color_html(color)}"
        f"{format_links(top)}"
    )
    alt_html = (
        "<h3 style='margin-top:16px'>Alternatives</h3>"
        + "".join(format_candidate(item, i) for i, item in enumerate(alternatives))
        if alternatives
        else "<p><em>No close alternatives.</em></p>"
    )
    return (
        match_panel_html(summary, reference_html(top)),
        alt_html,
        status_with_calls(
            f"Matched: {color.name + ' ' if color and color.key != 'unknown' else ''}{top.name}"
        ),
    )


def _error_view(note: str, status_label: str) -> IdentifyView:
    empty_alts = "<p><em>No alternatives yet.</em></p>"
    return IdentifyView(
        match_panel=match_panel_html(shape_only_summary(note), ""),
        alt_html=empty_alts,
        status=status_with_calls(status_label),
        history_html=HISTORY.html(),
        api_calls=get_api_call_count(),
    )


def identify_from_image(
    image: Image.Image,
    item_type: str,
    min_score: float,
    *,
    record_history: bool,
    frame_np: np.ndarray | None = None,
    source: str = "live",
    api_image: Image.Image | np.ndarray | None = None,
    crop_rect: tuple[int, int, int, int] | None = None,
) -> IdentifyView:
    """Identify a part; ``api_image`` (crop) is uploaded when provided.

    ``image`` / ``frame_np`` remain the full still for overlays. The catalog
    stores the API crop. Local HSV runs on ``crop_rect`` immediately;
    Brickognize bbox color wins when the API returns a valid box.
    """
    api_pil = to_pil(api_image) if api_image is not None else image
    if api_pil is None:
        api_pil = image

    # Local color from the padded blob / ROI — available before the API returns.
    local_color: ColorDetection | None = None
    if crop_rect is not None and frame_np is not None:
        local_color = detect_color_in_rect(frame_np, *crop_rect)
    elif api_image is not None:
        local_color = detect_color_in_rect(
            api_pil, 0, 0, api_pil.size[0], api_pil.size[1]
        )

    try:
        result = predict(api_pil, item_type=item_type)  # type: ignore[arg-type]
        logger.debug("Brickognize payload %d bytes", get_last_payload_bytes())
    except BrickognizeError as exc:
        return _error_view(f"API error: {exc}", "API error")
    except requests.RequestException:
        return _error_view(
            "Offline — could not reach Brickognize. Check your internet connection.",
            "Offline",
        )
    except Exception as exc:
        return _error_view(f"Error: {exc}", "Error")

    items = [item for item in result.items if item.score >= min_score]
    if not items:
        return _error_view(
            "No confident shape match. Try better lighting, a plain background, "
            "and center the piece in frame.",
            "No shape match",
        )

    color: ColorDetection | None = local_color
    annotated: np.ndarray | None = None
    origin = (crop_rect[0], crop_rect[1]) if crop_rect is not None else (0, 0)
    source_size = api_pil.size if crop_rect is not None else None

    if result.bounding_box is not None:
        api_color = detect_color_in_bbox(api_pil, result.bounding_box)
        if api_color.key != "unknown":
            color = api_color
        if frame_np is not None:
            annotated = annotate_bbox_overlay(
                frame_np,
                result.bounding_box,
                origin=origin,
                source_size=source_size,
            )
    elif frame_np is not None and crop_rect is not None:
        annotated = annotate_rect_overlay(frame_np, *crop_rect)

    match_panel, alt_html, status = _format_result(items, color)
    if record_history:
        if color is not None and color.key != "unknown":
            HISTORY.log(items[0], color)
        else:
            HISTORY.log(items[0])
        # Catalog stores the Brickognize crop (what we matched), not the full still.
        CATALOG.log_scan(
            items[0],
            color,
            source=source,
            listing_id=result.listing_id,
            capture_image=api_pil,
        )
    return IdentifyView(
        match_panel=match_panel,
        alt_html=alt_html,
        status=status,
        history_html=HISTORY.html(),
        api_calls=get_api_call_count(),
        annotated=annotated,
        candidate=items[0],
        color=color,
    )


def _frame_motion_score(prev_gray: np.ndarray, curr_gray: np.ndarray) -> float:
    """Mean absolute difference between downscaled grayscale frames (0–255)."""
    return float(np.mean(np.abs(prev_gray - curr_gray)))


class DeskScanner:
    """Desk-mode motion gating: scan once per settle cycle, re-arm after motion."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight = False
        self._prev_gray: np.ndarray | None = None
        self._stable_since: float | None = None
        self._armed = True
        # Skip-dedupe state shared with stream idle returns (reset with motion state).
        self._last_status_msg = ""
        self._last_api_calls = -1
        self._future: Future[IdentifyView] | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desk-identify")

    def reset_motion_state(self) -> None:
        with self._lock:
            self._prev_gray = None
            self._stable_since = None
            self._armed = True
            self._last_status_msg = ""
            self._last_api_calls = -1
            fut = self._future
            self._future = None
            self._in_flight = False
        if fut is not None and not fut.done():
            fut.cancel()

    def status_or_skip(self, message: str) -> str | dict:
        """Avoid re-rendering the status bar when the message has not changed."""
        full = status_with_calls(message)
        if full == self._last_status_msg:
            return gr.skip()
        self._last_status_msg = full
        return full

    def api_calls_or_skip(self) -> int | dict:
        calls = get_api_call_count()
        if calls == self._last_api_calls:
            return gr.skip()
        self._last_api_calls = calls
        return calls

    def _live_idle(self, status_msg: str) -> tuple:
        from .app import _catalog_skip

        return (
            gr.skip(),
            gr.skip(),
            gr.skip(),
            self.status_or_skip(status_msg),
            self.api_calls_or_skip(),
            *_catalog_skip(),
        )

    def _deliver_view(self, view: IdentifyView) -> tuple:
        from .app import _catalog_live_refresh

        preview = view.annotated if view.annotated is not None else gr.skip()
        self._last_status_msg = ""
        self._last_api_calls = -1
        return (
            preview,
            view.match_panel,
            view.alt_html,
            view.status,
            view.api_calls,
            *_catalog_live_refresh(),
        )

    def identify_live_frame(
        self,
        frame,
        item_type: str,
        min_score: float,
        live_enabled: bool,
        settle_time: float,
        motion_threshold: float,
    ) -> tuple | None:
        """Identify when the live feed has been still long enough (belt-stop analogue)."""
        if not live_enabled:
            return None

        if frame is None or not isinstance(frame, np.ndarray):
            return self._live_idle("Live — waiting for camera")

        gray = _to_gray_small(frame)
        now = time.monotonic()

        deliver: IdentifyView | None = None
        err_msg: str | None = None
        start_worker = False
        frame_copy: np.ndarray | None = None
        item_type_arg = item_type
        min_score_arg = min_score

        with self._lock:
            fut = self._future
            if fut is not None and fut.done():
                try:
                    deliver = fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Desk identify worker failed")
                    err_msg = f"Error: {exc}"
                self._future = None
                self._in_flight = False
            elif self._in_flight:
                pass  # keep showing Scanning…
            else:
                if self._prev_gray is not None:
                    motion = _frame_motion_score(self._prev_gray, gray)
                else:
                    motion = motion_threshold + 1.0
                self._prev_gray = gray

                if motion > motion_threshold:
                    self._stable_since = None
                    self._armed = True
                    return self._live_idle("Moving — hold piece still")

                if self._stable_since is None:
                    self._stable_since = now

                stable_for = now - self._stable_since
                if stable_for < settle_time:
                    return self._live_idle("Settling…")

                if not self._armed:
                    return self._live_idle("Stable — move piece to scan again")

                self._armed = False
                self._stable_since = None
                self._in_flight = True
                start_worker = True
                frame_copy = frame.copy()

        if deliver is not None:
            return self._deliver_view(deliver)
        if err_msg is not None:
            return self._live_idle(err_msg)
        if not start_worker or frame_copy is None:
            return self._live_idle("Scanning…")

        image = to_pil(frame_copy)

        def _worker() -> IdentifyView:
            return identify_from_image(
                image,
                item_type_arg,
                min_score_arg,
                record_history=True,
                frame_np=frame_copy,
                source="live",
            )

        submitted = self._pool.submit(_worker)
        with self._lock:
            self._future = submitted
        return self._live_idle("Scanning…")


DESK = DeskScanner()

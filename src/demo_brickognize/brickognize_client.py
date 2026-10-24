"""Client for the Brickognize image recognition API."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Literal

import requests
from PIL import Image

API_BASE = "https://api.brickognize.com"
DEFAULT_TIMEOUT = 20
DEFAULT_JPEG_MAX_SIZE = 1024
DEFAULT_JPEG_QUALITY = 85

ItemType = Literal["part", "set", "fig", "sticker", "any"]

logger = logging.getLogger(__name__)

_api_call_count = 0  # best-effort counter; races are acceptable (UI display only)
_last_payload_bytes = 0
_session = requests.Session()


def get_api_call_count() -> int:
    """Return the number of predict() requests sent this session."""
    return _api_call_count


def reset_api_call_count() -> None:
    """Reset the session API call counter."""
    global _api_call_count
    _api_call_count = 0


def get_last_payload_bytes() -> int:
    """Return the JPEG byte size of the most recent predict() upload."""
    return _last_payload_bytes


@dataclass(frozen=True)
class ExternalLink:
    name: str
    url: str


@dataclass(frozen=True)
class BoundingBox:
    left: float
    upper: float
    right: float
    lower: float
    image_width: float
    image_height: float
    score: float

    def is_valid(self) -> bool:
        return (
            self.score > 0.05
            and self.right > self.left + 1
            and self.lower > self.upper + 1
            and self.image_width > 0
            and self.image_height > 0
        )

    def to_pixel_rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Map API box coords to pixel rect on a (width, height) image."""
        sx = width / self.image_width
        sy = height / self.image_height
        x0 = max(0, int(self.left * sx))
        y0 = max(0, int(self.upper * sy))
        x1 = min(width, int(round(self.right * sx)))
        y1 = min(height, int(round(self.lower * sy)))
        return x0, y0, x1, y1


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    item_type: str
    category: str | None
    score: float
    img_url: str
    external_links: tuple[ExternalLink, ...]


@dataclass(frozen=True)
class PredictionResult:
    listing_id: str
    items: tuple[Candidate, ...]
    bounding_box: BoundingBox | None


class BrickognizeError(Exception):
    """Raised when the Brickognize API returns an error."""


def _endpoint_for(item_type: ItemType) -> str:
    if item_type == "part":
        return f"{API_BASE}/predict/parts/"
    if item_type == "set":
        return f"{API_BASE}/predict/sets/"
    if item_type == "fig":
        return f"{API_BASE}/predict/figs/"
    return f"{API_BASE}/predict/"


def image_to_jpeg_bytes(
    image: Image.Image,
    max_size: int = DEFAULT_JPEG_MAX_SIZE,
    *,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> bytes:
    """Convert a PIL image to JPEG bytes, optionally downscaling large frames."""
    rgb = image.convert("RGB")
    w, h = rgb.size
    if max(w, h) > max_size:
        scale = max_size / max(w, h)
        rgb = rgb.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _parse_bbox(raw: dict | None) -> BoundingBox | None:
    if not raw:
        return None
    return BoundingBox(
        left=float(raw.get("left", 0)),
        upper=float(raw.get("upper", 0)),
        right=float(raw.get("right", 0)),
        lower=float(raw.get("lower", 0)),
        image_width=float(raw.get("image_width", 0)),
        image_height=float(raw.get("image_height", 0)),
        score=float(raw.get("score", 0)),
    )


def predict(
    image: Image.Image,
    *,
    item_type: ItemType = "part",
    timeout: int = DEFAULT_TIMEOUT,
) -> PredictionResult:
    """Send an image to Brickognize and return ranked candidates."""
    global _api_call_count, _last_payload_bytes

    jpeg = image_to_jpeg_bytes(image)
    _last_payload_bytes = len(jpeg)
    url = _endpoint_for(item_type)
    _api_call_count += 1
    logger.debug(
        "Brickognize upload %d bytes (%dx%d → jpeg)",
        _last_payload_bytes,
        image.size[0],
        image.size[1],
    )
    response = _session.post(
        url,
        files={"query_image": ("frame.jpg", jpeg, "image/jpeg")},
        timeout=timeout,
    )
    if not response.ok:
        raise BrickognizeError(f"API error {response.status_code}: {response.text[:200]}")

    data = response.json()
    items: list[Candidate] = []
    for raw in data.get("items", []):
        links = tuple(
            ExternalLink(name=site["name"], url=site["url"])
            for site in raw.get("external_sites", [])
        )
        items.append(
            Candidate(
                id=str(raw.get("id", "")),
                name=str(raw.get("name", "Unknown")),
                item_type=str(raw.get("type", "")),
                category=raw.get("category"),
                score=float(raw.get("score", 0.0)),
                img_url=str(raw.get("img_url", "")),
                external_links=links,
            )
        )

    bbox = _parse_bbox(data.get("bounding_box"))
    return PredictionResult(
        listing_id=str(data.get("listing_id", "")),
        items=tuple(items),
        bounding_box=bbox if bbox and bbox.is_valid() else None,
    )


def health_check(timeout: int = 5) -> bool:
    """Return True if the Brickognize API health endpoint responds."""
    try:
        response = _session.get(f"{API_BASE}/health/", timeout=timeout)
        return response.ok
    except requests.RequestException:
        return False

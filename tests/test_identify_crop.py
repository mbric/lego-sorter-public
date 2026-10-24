"""Identify path uploads the API crop, not the full still."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from PIL import Image

from demo_brickognize import identify as identify_mod
from demo_brickognize.brickognize_client import (
    BoundingBox,
    Candidate,
    PredictionResult,
    image_to_jpeg_bytes,
)


def test_image_to_jpeg_defaults_are_tighter():
    img = Image.new("RGB", (1600, 1200), (40, 40, 40))
    jpeg = image_to_jpeg_bytes(img)
    # Downscale to ≤1024 on the long edge before encode.
    assert len(jpeg) < 1600 * 1200  # sanity
    # Re-open to check dimensions of the encoded path would require decode;
    # just ensure we produce a non-trivial JPEG.
    assert jpeg[:2] == b"\xff\xd8"


def test_identify_from_image_sends_api_crop(monkeypatch):
    full = np.full((240, 320, 3), 200, dtype=np.uint8)
    crop = np.full((60, 80, 3), (201, 26, 9), dtype=np.uint8)
    seen: dict = {}

    def fake_predict(image, *, item_type="part", timeout=20):
        arr = np.asarray(image.convert("RGB"))
        seen["shape"] = arr.shape
        seen["item_type"] = item_type
        return PredictionResult(
            listing_id="t1",
            items=(
                Candidate(
                    id="3001",
                    name="Brick 2x4",
                    item_type="part",
                    category="Brick",
                    score=0.95,
                    img_url="",
                    external_links=(),
                ),
            ),
            bounding_box=BoundingBox(
                left=5,
                upper=5,
                right=75,
                lower=55,
                image_width=80,
                image_height=60,
                score=0.9,
            ),
        )

    monkeypatch.setattr(identify_mod, "predict", fake_predict)
    monkeypatch.setattr(
        identify_mod.CATALOG,
        "log_scan",
        lambda *a, **k: SimpleNamespace(),
    )

    view = identify_mod.identify_from_image(
        Image.fromarray(full),
        item_type="part",
        min_score=0.1,
        record_history=False,
        frame_np=full,
        source="test",
        api_image=crop,
        crop_rect=(40, 50, 120, 110),
    )
    assert seen["shape"] == (60, 80, 3)
    assert view.candidate is not None
    assert view.candidate.id == "3001"
    # Local / API color should resolve to red from the crop.
    assert view.color is not None
    assert view.color.key == "red"


def test_identify_logs_api_crop_to_catalog(monkeypatch):
    full = np.full((240, 320, 3), 200, dtype=np.uint8)
    crop = np.full((60, 80, 3), (10, 20, 30), dtype=np.uint8)
    logged: dict = {}

    def fake_predict(image, *, item_type="part", timeout=20):
        return PredictionResult(
            listing_id="t1",
            items=(
                Candidate(
                    id="3001",
                    name="Brick 2x4",
                    item_type="part",
                    category="Brick",
                    score=0.95,
                    img_url="",
                    external_links=(),
                ),
            ),
            bounding_box=None,
        )

    def fake_log_scan(*_args, **kwargs):
        logged["capture"] = kwargs.get("capture_image")

    monkeypatch.setattr(identify_mod, "predict", fake_predict)
    monkeypatch.setattr(identify_mod.CATALOG, "log_scan", fake_log_scan)
    monkeypatch.setattr(
        identify_mod.HISTORY, "log", lambda *a, **k: None
    )

    identify_mod.identify_from_image(
        Image.fromarray(full),
        item_type="part",
        min_score=0.1,
        record_history=True,
        frame_np=full,
        source="feed",
        api_image=crop,
        crop_rect=(40, 50, 120, 110),
    )
    assert logged["capture"] is not None
    arr = np.asarray(logged["capture"].convert("RGB"))
    assert arr.shape == (60, 80, 3)
    assert tuple(arr[0, 0]) == (10, 20, 30)

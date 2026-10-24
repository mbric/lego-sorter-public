"""HSV color detection and BoundingBox projection."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from demo_brickognize.brickognize_client import BoundingBox
from demo_brickognize.color_detect import (
    annotate_rect_overlay,
    detect_color_in_bbox,
    detect_color_in_rect,
)


def _solid_image(rgb: tuple[int, int, int], size=(120, 120)) -> Image.Image:
    arr = np.full((size[1], size[0], 3), rgb, dtype=np.uint8)
    return Image.fromarray(arr, mode="RGB")


def _center_bbox(w: int, h: int) -> BoundingBox:
    return BoundingBox(
        left=w * 0.2,
        upper=h * 0.2,
        right=w * 0.8,
        lower=h * 0.8,
        image_width=float(w),
        image_height=float(h),
        score=0.9,
    )


@pytest.mark.parametrize(
    "rgb,expected_key",
    [
        ((201, 26, 9), "red"),
        ((0, 85, 191), "blue"),
        ((242, 205, 55), "yellow"),
        ((35, 120, 65), "green"),
        ((10, 10, 10), "black"),
        ((250, 250, 250), "white"),
    ],
)
def test_detect_solid_colors(rgb, expected_key):
    image = _solid_image(rgb)
    w, h = image.size
    det = detect_color_in_bbox(image, _center_bbox(w, h))
    assert det.key == expected_key


def test_mid_gray_is_unknown():
    image = _solid_image((128, 128, 128))
    w, h = image.size
    det = detect_color_in_bbox(image, _center_bbox(w, h))
    assert det.key == "unknown"


def test_bounding_box_to_pixel_rect_scales():
    # Box on a 1280×960 source projected onto 320×240.
    box = BoundingBox(
        left=320.0,
        upper=240.0,
        right=960.0,
        lower=720.0,
        image_width=1280.0,
        image_height=960.0,
        score=0.9,
    )
    x0, y0, x1, y1 = box.to_pixel_rect(320, 240)
    assert (x0, y0, x1, y1) == (80, 60, 240, 180)


def test_detect_color_in_rect_reads_patch():
    arr = np.full((100, 100, 3), 200, dtype=np.uint8)
    arr[20:60, 30:70] = (201, 26, 9)
    det = detect_color_in_rect(arr, 30, 20, 70, 60)
    assert det.key == "red"


def test_annotate_rect_overlay_draws_without_changing_interior():
    arr = np.zeros((80, 80, 3), dtype=np.uint8)
    out = annotate_rect_overlay(arr, 10, 10, 50, 50)
    assert out is not arr
    # Interior pixel away from the border stays black.
    assert tuple(out[30, 30]) == (0, 0, 0)
    # Border gets the overlay green.
    assert int(out[10, 30, 1]) > 100

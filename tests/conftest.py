import numpy as np


def make_frame(h=240, w=320, gray=200):
    """Uniform RGB uint8 frame."""
    return np.full((h, w, 3), gray, dtype=np.uint8)


def add_blob(frame, y0, y1, x0, x1, value=30):
    """Paint a dark rectangle (a 'part') onto a copy of frame."""
    out = frame.copy()
    out[y0:y1, x0:x1] = value
    return out

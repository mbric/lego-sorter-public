"""Virtual bin routing from identification results."""

from __future__ import annotations

from typing import Protocol


class _HasCategory(Protocol):
    category: str | None
    item_type: str


class _HasColorKey(Protocol):
    key: str


def virtual_bin(
    candidate: _HasCategory | None,
    color: _HasColorKey | None,
) -> str:
    """Map ID + color to a virtual bin label.

    Priority: known HSV color → Brickognize category → unknown.
    """
    if color is not None and color.key and color.key != "unknown":
        return color.key
    if candidate is not None:
        category = (candidate.category or candidate.item_type or "").strip()
        if category:
            # Normalize for bin labels (e.g. "Brick, Modified" → "brick_modified")
            slug = category.lower().replace(",", "").replace(" ", "_")
            return slug
    return "unknown"

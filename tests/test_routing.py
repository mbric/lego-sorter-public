"""Virtual bin routing."""

from __future__ import annotations

from types import SimpleNamespace

from orchestrator.routing import virtual_bin


def test_known_color_wins():
    candidate = SimpleNamespace(category="Brick, Modified", item_type="part")
    color = SimpleNamespace(key="red")
    assert virtual_bin(candidate, color) == "red"


def test_unknown_color_uses_category():
    candidate = SimpleNamespace(category="Brick, Modified", item_type="part")
    color = SimpleNamespace(key="unknown")
    assert virtual_bin(candidate, color) == "brick_modified"


def test_both_missing_is_unknown():
    assert virtual_bin(None, None) == "unknown"
    assert virtual_bin(None, SimpleNamespace(key="unknown")) == "unknown"
    assert virtual_bin(SimpleNamespace(category=None, item_type=""), None) == "unknown"

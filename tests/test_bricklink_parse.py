"""BrickLink scrape parsers — no network."""

from __future__ import annotations

import json

import requests

import demo_brickognize.bricklink_client as bricklink_client
from demo_brickognize.bricklink_client import _summary_ints, _summary_values


def _summary_block(qty_avg: str, avg: str, total_qty: str) -> str:
    return (
        f">Qty Avg Price:</TD><TD ALIGN=\"RIGHT\"><B>{qty_avg}</B></TD>"
        f">Avg Price:</TD><TD ALIGN=\"RIGHT\"><B>{avg}</B></TD>"
        f">Total Qty:</TD><TD ALIGN=\"RIGHT\"><B>{total_qty}</B></TD>"
    )


def test_summary_values_and_ints_four_blocks():
    # Four summary blocks: sold/new, sold/used, current/new, current/used.
    html = "".join(
        [
            _summary_block("US $0.0813", "US $0.10", "1,234"),
            _summary_block("US $0.0500", "US $0.06", "56"),
            _summary_block("US $0.1200", "US $0.15", "9"),
            _summary_block("US $1,234.5678", "US $2.00", "10,001"),
        ]
    )

    qty_avg = _summary_values(html, "Qty Avg Price:")
    avg = _summary_values(html, "Avg Price:")
    totals = _summary_ints(html, "Total Qty:")

    assert qty_avg == [0.0813, 0.05, 0.12, 1234.5678]
    assert avg == [0.10, 0.06, 0.15, 2.0]
    assert totals == [1234, 56, 9, 10001]
    assert len(qty_avg) == 4
    assert len(avg) == 4
    assert len(totals) == 4


def test_price_for_does_not_cache_network_failures(tmp_path, monkeypatch):
    """A RequestException must not poison the on-disk cache for a week."""
    cache_path = tmp_path / "bricklink_cache.json"
    monkeypatch.setattr(bricklink_client, "CACHE_PATH", cache_path)
    monkeypatch.setattr(bricklink_client, "_cache", None)
    monkeypatch.setattr(bricklink_client, "_throttle", lambda: None)
    # Force the scrape backend (no OAuth needed).
    monkeypatch.setattr(bricklink_client, "has_api_credentials", lambda: False)
    monkeypatch.setattr(bricklink_client, "scrape_enabled", lambda: True)

    def _boom(*_args, **_kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr(bricklink_client.requests, "get", _boom)

    result = bricklink_client.price_for("3001", "red")
    assert result is None
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        assert data == {}
    else:
        # No cache file is also fine — nothing was written.
        pass


class _FakeResponse:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


def test_price_for_does_not_cache_waf_challenge(tmp_path, monkeypatch):
    """AWS WAF 202/empty responses must not cache as 'no price'."""
    cache_path = tmp_path / "bricklink_cache.json"
    monkeypatch.setattr(bricklink_client, "CACHE_PATH", cache_path)
    monkeypatch.setattr(bricklink_client, "_cache", None)
    monkeypatch.setattr(bricklink_client, "_throttle", lambda: None)
    monkeypatch.setattr(bricklink_client, "has_api_credentials", lambda: False)
    monkeypatch.setattr(bricklink_client, "scrape_enabled", lambda: True)
    monkeypatch.setattr(bricklink_client, "_last_error", None)

    monkeypatch.setattr(
        bricklink_client.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(
            status_code=202, text="", headers={"x-amzn-waf-action": "challenge"}
        ),
    )

    result = bricklink_client.price_for("3001", "red")
    assert result is None
    assert bricklink_client.last_error() is not None
    assert "WAF" in (bricklink_client.last_error() or "")
    assert not cache_path.exists() or json.loads(cache_path.read_text()) == {}


def test_purge_null_cache_entries(tmp_path, monkeypatch):
    cache_path = tmp_path / "bricklink_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "scrape|3001|5|sold|U|USD": {"ts": 1, "value": None},
                "scrape|3002|5|sold|U|USD": {
                    "ts": 1,
                    "value": {
                        "unit_price": 0.12,
                        "currency": "USD",
                        "guide_type": "sold",
                        "condition": "U",
                        "qty_found": 10,
                        "color_id": 5,
                        "source": "scrape",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bricklink_client, "CACHE_PATH", cache_path)
    monkeypatch.setattr(bricklink_client, "_cache", None)

    removed = bricklink_client.purge_null_cache_entries()
    assert removed == 1
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert list(data) == ["scrape|3002|5|sold|U|USD"]

"""Look up the market value of a LEGO part on BrickLink.

Two backends are supported; the first available one is used:

1. Official BrickLink Price Guide API (OAuth 1.0a) — most reliable, but
   registering an API consumer requires BrickLink *seller* status.
2. Public price-guide page scrape (``catalogPG.asp``) — no account needed,
   returns the same Times Sold / Avg / Qty-Avg / Min / Max numbers. This is
   the default fallback so valuation works without seller status.

   Note: scraping is technically against BrickLink's Terms of Service. It is
   intended for personal, low-volume use only; results are cached for a week
   and requests are rate-limited to be gentle on their servers. Set
   ``BRICKLINK_DISABLE_SCRAPE=1`` to turn it off.

OAuth credentials (optional) are read from the environment / a project-root
``.env`` file:

    BRICKLINK_CONSUMER_KEY / BRICKLINK_CONSUMER_SECRET
    BRICKLINK_TOKEN / BRICKLINK_TOKEN_SECRET

Optional tuning (defaults shown):

    BRICKLINK_CURRENCY=USD
    BRICKLINK_GUIDE_TYPE=sold     # "sold" (last 6 months) or "stock" (current)
    BRICKLINK_CONDITION=U         # "U" used or "N" new
    BRICKLINK_DISABLE_SCRAPE=     # set to 1 to disable the scrape fallback
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

try:  # Optional: load credentials from a project-root .env file.
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:  # pragma: no cover - dotenv is best-effort
    pass

API_BASE = "https://api.bricklink.com/api/store/v3"
PG_URL = "https://www.bricklink.com/catalogPG.asp"
DEFAULT_TIMEOUT = 15
CACHE_TTL_SECONDS = 7 * 24 * 3600  # price guide is slow-moving; cache a week
MIN_REQUEST_INTERVAL = 1.0  # seconds between live network fetches (politeness)
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "bricklink_cache.json"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Maps the local HSV color keys (see color_detect.py) to BrickLink color IDs.
# https://www.bricklink.com/catalogColors.asp
BRICKLINK_COLOR_IDS: dict[str, int] = {
    "black": 11,
    "white": 1,
    "red": 5,      # LEGO "Bright Red"
    "orange": 4,   # LEGO "Bright Orange"
    "yellow": 3,   # LEGO "Bright Yellow"
    "green": 6,    # LEGO "Bright Green"
    "blue": 7,     # LEGO "Bright Blue"
}

_lock = threading.Lock()
_api_call_count = 0  # best-effort counter; races are acceptable (UI display only)
_cache: dict[str, dict] | None = None
_last_request_at = 0.0
_last_error: str | None = None


class BrickLinkError(Exception):
    """Raised when the BrickLink API returns an error."""


@dataclass(frozen=True)
class PartValue:
    """Resolved market value for a single part/color combination."""

    unit_price: float
    currency: str
    guide_type: str
    condition: str
    qty_found: int
    color_id: int | None
    source: str = "bricklink"

    @property
    def has_price(self) -> bool:
        return self.unit_price > 0


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def currency_code() -> str:
    return _env("BRICKLINK_CURRENCY") or "USD"


def guide_type() -> str:
    value = (_env("BRICKLINK_GUIDE_TYPE") or "sold").lower()
    return value if value in {"sold", "stock"} else "sold"


def condition() -> str:
    value = (_env("BRICKLINK_CONDITION") or "U").upper()
    return value if value in {"U", "N"} else "U"


def has_api_credentials() -> bool:
    """True when all four OAuth credentials are present."""
    return all(
        _env(name)
        for name in (
            "BRICKLINK_CONSUMER_KEY",
            "BRICKLINK_CONSUMER_SECRET",
            "BRICKLINK_TOKEN",
            "BRICKLINK_TOKEN_SECRET",
        )
    )


def scrape_enabled() -> bool:
    return _env("BRICKLINK_DISABLE_SCRAPE") not in {"1", "true", "yes", "on"}


def active_backend() -> str:
    """Which price source will be used: 'api', 'scrape', or 'none'."""
    if has_api_credentials():
        return "api"
    if scrape_enabled():
        return "scrape"
    return "none"


def is_configured() -> bool:
    """True when valuation can run via either backend."""
    return active_backend() != "none"


def backend_label() -> str:
    backend = active_backend()
    if backend == "api":
        return "BrickLink API"
    if backend == "scrape":
        return "BrickLink price guide (public)"
    return "disabled"


def get_api_call_count() -> int:
    """Number of BrickLink network requests sent this process (cache excluded)."""
    return _api_call_count


def last_error() -> str | None:
    """Most recent fetch failure reason (WAF block, network, etc.), if any."""
    return _last_error


def color_id_for(color_key: str | None) -> int | None:
    if not color_key:
        return None
    return BRICKLINK_COLOR_IDS.get(color_key.lower())


# --- caching -----------------------------------------------------------------


def _load_cache() -> dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _cache = {}
    return _cache


def _save_cache() -> None:
    if _cache is None:
        return
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_cache), encoding="utf-8")
    except OSError:
        pass


def _cache_key(part_id, color_id, gtype, cond, cur, backend) -> str:
    cid = color_id if color_id is not None else "-"
    return f"{backend}|{part_id}|{cid}|{gtype}|{cond}|{cur}"


def purge_null_cache_entries() -> int:
    """Drop cached 'no price' rows so failed scrapes can be retried.

    Older builds cached WAF/empty responses as value=None for a week, which
    made Estimate value look permanently broken.
    """
    with _lock:
        cache = _load_cache()
        doomed = [key for key, entry in cache.items() if entry.get("value") is None]
        if not doomed:
            return 0
        for key in doomed:
            del cache[key]
        _save_cache()
        return len(doomed)


def _throttle() -> None:
    global _last_request_at
    now = time.time()
    wait = MIN_REQUEST_INTERVAL - (now - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.time()


# --- backend: official API ---------------------------------------------------


def _auth():
    from requests_oauthlib import OAuth1

    return OAuth1(
        _env("BRICKLINK_CONSUMER_KEY"),
        _env("BRICKLINK_CONSUMER_SECRET"),
        _env("BRICKLINK_TOKEN"),
        _env("BRICKLINK_TOKEN_SECRET"),
        signature_type="auth_header",
    )


def _price_via_api(part_id, color_id, gtype, cond, cur, timeout) -> PartValue | None:
    global _api_call_count
    params = {"guide_type": gtype, "new_or_used": cond, "currency_code": cur}
    if color_id is not None:
        params["color_id"] = color_id
    url = f"{API_BASE}/items/PART/{part_id}/price"
    try:
        with _lock:
            _api_call_count += 1
        _throttle()
        response = requests.get(url, params=params, auth=_auth(), timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    data = payload.get("data") or {}
    raw_price = data.get("qty_avg_price") or data.get("avg_price") or "0"
    try:
        unit_price = float(raw_price)
    except (TypeError, ValueError):
        unit_price = 0.0
    return PartValue(
        unit_price=round(unit_price, 4),
        currency=str(data.get("currency_code") or cur),
        guide_type=gtype,
        condition=cond,
        qty_found=int(data.get("total_quantity") or 0),
        color_id=color_id,
        source="api",
    )


# --- backend: public price-guide page scrape ---------------------------------

_MONEY_RE = re.compile(r"([0-9][0-9,]*\.[0-9]+)")


def _summary_cells(html: str, label: str) -> list[str]:
    """Return the bold value text for every occurrence of a summary row label.

    The page renders four summary blocks in order:
        [sold/new, sold/used, current/new, current/used]
    """
    pattern = re.compile(r">\s*" + re.escape(label) + r"\s*</TD>\s*<TD[^>]*>\s*<B>([^<]*)</B>", re.I)
    return [m.group(1) for m in pattern.finditer(html)]


def _summary_values(html: str, label: str) -> list[float]:
    out: list[float] = []
    for cell in _summary_cells(html, label):
        money = _MONEY_RE.search(cell)
        out.append(float(money.group(1).replace(",", "")) if money else 0.0)
    return out


def _summary_ints(html: str, label: str) -> list[int]:
    out: list[int] = []
    for cell in _summary_cells(html, label):
        digits = re.search(r"([0-9][0-9,]*)", cell)
        out.append(int(digits.group(1).replace(",", "")) if digits else 0)
    return out


def _price_via_scrape(part_id, color_id, gtype, cond, cur, timeout) -> PartValue | None:
    global _api_call_count, _last_error
    if color_id is None:  # the public PG page is per-color; skip uncolored lookups
        return None
    params = {"P": part_id, "colorID": color_id}
    try:
        with _lock:
            _api_call_count += 1
        _throttle()
        response = requests.get(
            PG_URL, params=params, headers={"User-Agent": _USER_AGENT}, timeout=timeout
        )
    except requests.RequestException as exc:
        _last_error = f"BrickLink scrape network error: {exc}"
        return None

    waf = (response.headers.get("x-amzn-waf-action") or "").strip()
    html = response.text or ""
    # AWS WAF often returns HTTP 202 + empty/challenge body — not a price guide.
    if response.status_code != 200 or waf or "Qty Avg Price:" not in html:
        if waf or response.status_code == 202:
            _last_error = (
                "BrickLink blocked the public price-guide scrape (WAF challenge). "
                "Add seller API credentials in .env to use the official Price Guide API."
            )
        elif not html.strip():
            _last_error = "BrickLink price-guide scrape returned an empty page."
        else:
            _last_error = (
                "BrickLink price-guide page did not include price summary data."
            )
        return None

    # Block index: sold->0/1, stock->2/3; new->even, used->odd.
    index = (0 if gtype == "sold" else 2) + (0 if cond == "N" else 1)

    qty_avg = _summary_values(html, "Qty Avg Price:")
    avg = _summary_values(html, "Avg Price:")
    totals = _summary_ints(html, "Total Qty:")
    if not qty_avg and not avg:
        _last_error = "BrickLink price-guide page could not be parsed."
        return None

    unit_price = 0.0
    if index < len(qty_avg) and qty_avg[index] > 0:
        unit_price = qty_avg[index]
    elif index < len(avg):
        unit_price = avg[index]

    qty_found = totals[index] if index < len(totals) else 0
    _last_error = None

    return PartValue(
        unit_price=round(unit_price, 4),
        currency="USD",  # not logged in -> page defaults to US$
        guide_type=gtype,
        condition=cond,
        qty_found=qty_found,
        color_id=color_id,
        source="scrape",
    )


# --- public entry point ------------------------------------------------------


def is_cached(part_id: str, color_key: str | None) -> bool:
    """True when a fresh cached result exists for this part/color lookup."""
    backend = active_backend()
    if not part_id or backend == "none":
        return False
    color_id = color_id_for(color_key)
    key = _cache_key(part_id, color_id, guide_type(), condition(), currency_code(), backend)
    with _lock:
        cache = _load_cache()
        entry = cache.get(key)
        return bool(entry and (time.time() - entry.get("ts", 0)) < CACHE_TTL_SECONDS)


def price_for(
    part_id: str,
    color_key: str | None,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    cache_only: bool = False,
) -> PartValue | None:
    """Return the market value for ``part_id`` in the detected color.

    Results (including "no data") are cached on disk to limit network calls.
    With ``cache_only=True`` no network request is made: cached results are
    returned and uncached lookups yield ``None`` (used for snappy UI refreshes).
    Returns ``None`` when valuation is disabled or the lookup fails.
    """
    backend = active_backend()
    if not part_id or backend == "none":
        return None

    color_id = color_id_for(color_key)
    gtype = guide_type()
    cond = condition()
    cur = currency_code()
    key = _cache_key(part_id, color_id, gtype, cond, cur, backend)

    with _lock:
        cache = _load_cache()
        entry = cache.get(key)
        if entry and (time.time() - entry.get("ts", 0)) < CACHE_TTL_SECONDS:
            cached = entry.get("value")
            return PartValue(**cached) if cached else None

    if cache_only:
        return None

    if backend == "api":
        value = _price_via_api(part_id, color_id, gtype, cond, cur, timeout)
    else:
        value = _price_via_scrape(part_id, color_id, gtype, cond, cur, timeout)

    # Only cache successful parses. None means blocked/network/unparseable —
    # caching that would poison the part/color for CACHE_TTL_SECONDS.
    # A parsed page with unit_price=0 (no sales) still caches as value=None.
    if value is not None:
        with _lock:
            cache = _load_cache()
            cache[key] = {
                "ts": time.time(),
                "value": value.__dict__ if value.has_price else None,
            }
            _save_cache()

    return value if (value and value.has_price) else None

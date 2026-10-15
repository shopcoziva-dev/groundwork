"""Collect competitor ad prices for a product (what the pricing formula needs), with no Chrome step.

Sources, best first (the file records which one was used):
  1. serpapi   - real Google Shopping results (env SERPAPI_KEY; about $0.01 a search). Same data Coziva collects by hand.
  2. web       - Claude web search for current US prices of the same kind of product (lower confidence; tagged so)
  3. manual    - a file already in stores/<slug>/market/<key>.json (Coziva's Chrome-collected format) is used as is.
File format = Coziva's stage2_ads format: {"niches": {"<search>": [[title, price], ...]}, "collected": ..., "source": ...}
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from . import ai
from .core import Log, Store, now


def serpapi_ready() -> bool:
    return bool(os.environ.get("SERPAPI_KEY", "").strip())


def serpapi(query: str, n: int = 10) -> list[list]:
    key = os.environ.get("SERPAPI_KEY", "").strip()
    if not key:
        return []
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode({"engine": "google_shopping", "q": query, "gl": "us", "hl": "en", "api_key": key})
    try:
        d = json.loads(urllib.request.urlopen(url, timeout=60).read())
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in d.get("shopping_results") or []:
        p = r.get("extracted_price")
        if p:
            out.append([str(r.get("title", ""))[:80] + f" | {r.get('source', '')}", float(p)])
        if len(out) >= n:
            break
    return out


WEB_SYSTEM = """You collect CURRENT US retail prices for a product type, the way a shopper sees them in Google Shopping ads.
Search the web, then answer with JSON: {"rows": [{"title": "...", "seller": "...", "price": 59.99}, ...]}
- 6 to 10 different listings of the SAME kind of product as the search (same item type and use; any brand), US dollars, current price.
- Skip bundles, multi-packs, used/refurbished, and accessories for it. One row per listing; no duplicates.
- Only prices you actually found; never estimate."""


def web(query: str) -> list[list]:
    ans = ai.ask_json("market_web", WEB_SYSTEM, f"Search: {query}", web=4, max_tokens=1500,
                      check=lambda a: [] if isinstance(a, dict) and len(a.get("rows") or []) >= 3 else ["need at least 3 rows"])
    rows = []
    for r in ans.get("rows") or []:
        try:
            rows.append([f"{r.get('title', '')[:70]} | {r.get('seller', '')}", float(r["price"])])
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def collect(store: Store, key: str, queries: list[str], log: Log, refresh: bool = False) -> dict:
    """Ad prices for one product (key = pid[:8]-gender). Reuses the saved file unless refresh."""
    path = store.path("market", f"{key}.json")
    have = store.load(f"market/{key}.json")
    if have and not refresh and len(have.get("niches") or {}) >= 2:
        log(f"  market: using saved prices ({have.get('source')}, {have.get('collected')})")
        return have
    niches, source = {}, ""
    for q in [q for q in queries if q][:3]:
        rows = serpapi(q)
        src = "serpapi"
        if len(rows) < 3:
            rows, src = web(q), "web"
        if len(rows) >= 3:
            niches[q] = rows
            source = source or src
            if src != source:
                source = "mixed"
        log(f"  market '{q}': {len(rows)} prices ({src})")
    data = {"niches": niches, "collected": now(), "source": source or "none", "queries": queries}
    path.parent.mkdir(parents=True, exist_ok=True)
    store.save(f"market/{key}.json", data)
    return data

"""Solo Scout check 1: Keyword Planner demand per niche option (US, Google Search).

Pass: 10,000+ searches a month (100,000+ is ideal) AND average CPC under $3.
Low-spend Google Ads accounts get ranges ("10K-100K") instead of exact numbers: the check is judged on the LOW end of the
range, so a range never passes on a number it might not reach.

Sources, in order:
  1. stores/<slug>/keyword_planner.json  (typed in by hand from the Keyword Planner screen; always wins)
  2. the cache (stores/_cache/demand/, 30 days) so a re-run gives the same answer
  3. the Google Ads API (lib/gads) when its secrets are set
Nothing available -> status "unavailable": shown on the page, never blocks a run.
"""
from __future__ import annotations

import re
import time

from .core import STORES, Store, read_json, write_json

MIN_SEARCHES = 10_000
IDEAL_SEARCHES = 100_000
MAX_CPC = 3.0
TTL_DAYS = 30
CACHE = STORES / "_cache" / "demand"


def _num(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.match(r"^\s*\$?\s*([\d.,]+)\s*([kKmM]?)\s*$", str(s))
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return v * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1)


def parse_range(v) -> tuple[float, float] | None:
    """10000 | "10,000" | "10K" | "10K-100K" | "10K – 100K" | {"low": .., "high": ..} | [low, high] -> (low, high)."""
    if v is None or v == "":
        return None
    if isinstance(v, dict):
        lo, hi = _num(v.get("low")), _num(v.get("high"))
        return (lo, hi if hi is not None else lo) if lo is not None else None
    if isinstance(v, (list, tuple)) and len(v) == 2:
        lo, hi = _num(v[0]), _num(v[1])
        return (lo, hi) if lo is not None and hi is not None else None
    parts = [p for p in re.split(r"\s*(?:-|–|—|to)\s*", str(v).strip()) if p]
    nums = [_num(p) for p in parts]
    if not nums or any(n is None for n in nums):
        return None
    return (nums[0], nums[-1])


def fmt(lo: float, hi: float) -> str:
    def f(x):
        return f"{x / 1e6:g}M" if x >= 1e6 else f"{x / 1e3:g}K" if x >= 1e3 else f"{x:g}"
    return f(lo) if lo == hi else f"{f(lo)}–{f(hi)}"


def judge(searches, cpc) -> dict:
    """The pass rule, pure: searches (number or range) + CPC dollars -> {status, ideal, text}."""
    rg = parse_range(searches)
    c = _num(cpc)
    if rg is None:
        return {"status": "unavailable", "ideal": False, "text": "Keyword Planner: unavailable"}
    lo, hi = rg
    why = []
    if lo < MIN_SEARCHES:
        why.append(f"{fmt(lo, hi)} searches/mo" + (" (low end under 10K)" if hi >= MIN_SEARCHES else " (need 10K+)"))
    if c is not None and c >= MAX_CPC:
        why.append(f"CPC ${c:.2f} (need under ${MAX_CPC:.0f})")
    ok = not why
    txt = f"{fmt(lo, hi)} searches/mo" + (f", CPC ${c:.2f}" if c is not None else ", CPC n/a")
    return {"status": "pass" if ok else "fail", "ideal": ok and lo >= IDEAL_SEARCHES, "low": lo, "high": hi, "cpc": c,
            "text": txt + ("" if ok else " - " + "; ".join(why)) + (" (ideal)" if ok and lo >= IDEAL_SEARCHES else "")}


def _key(niche: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", niche.lower()).strip("-")[:60]


def _manual(store: Store | None, niche: str, keywords: list[str]):
    if store is None:
        return None
    kp = store.load("keyword_planner.json") or {}
    rows = kp.get("niches") or kp
    for k in [niche] + list(keywords):
        for name, v in rows.items() if isinstance(rows, dict) else []:
            if name.strip().lower() == k.strip().lower() and isinstance(v, dict):
                return {"searches": v.get("searches"), "cpc": v.get("cpc"), "source": "typed in from Keyword Planner"}
    return None


def _api(keywords: list[str]) -> dict:
    from . import gads
    js = gads.call(":generateKeywordHistoricalMetrics", {
        "keywords": keywords[:20], "geoTargetConstants": ["geoTargetConstants/2840"],     # US
        "language": "languageConstants/1000", "keywordPlanNetwork": "GOOGLE_SEARCH"})     # English, Search only
    lo = hi = 0.0
    cpcs = []
    rows = []
    for r in js.get("results") or []:
        m = r.get("keywordMetrics") or {}
        s = float(m.get("avgMonthlySearches") or 0)
        # some accounts get a bucket instead of a number; the API may send it as a range
        rg = parse_range(m.get("monthlySearchVolumeRange") or m.get("avgMonthlySearches") or 0) or (s, s)
        lo, hi = lo + rg[0], hi + rg[1]
        c = m.get("averageCpcMicros")
        if c is None and m.get("lowTopOfPageBidMicros") is not None:
            c = (float(m.get("lowTopOfPageBidMicros") or 0) + float(m.get("highTopOfPageBidMicros") or 0)) / 2
        if c:
            cpcs.append((float(c) / 1e6, max(rg[0], 1)))
        rows.append({"keyword": r.get("text"), "searches": fmt(*rg), "cpc": round(float(c) / 1e6, 2) if c else None})
    cpc = round(sum(c * w for c, w in cpcs) / sum(w for _, w in cpcs), 2) if cpcs else None
    return {"searches": {"low": lo, "high": hi}, "cpc": cpc, "keywords": rows, "source": "Google Ads Keyword Planner (US)"}


def check(niche: str, keywords: list[str] | None = None, store: Store | None = None, refresh: bool = False) -> dict:
    """Demand for one niche option. Never raises. Searches = the niche's keywords added up (they are different searches);
    CPC = search-weighted average."""
    kws = [k for k in dict.fromkeys([niche] + list(keywords or [])) if k]
    got = _manual(store, niche, kws)
    path = CACHE / f"{_key(niche)}.json"
    cached = read_json(path, None)
    fresh = cached and time.time() - cached.get("ts", 0) < TTL_DAYS * 86400
    if got is None and cached and (fresh and not refresh):
        got = cached["data"]
    if got is None:
        from . import gads
        if gads.ready():
            try:
                got = _api(kws)
                write_json(path, {"ts": time.time(), "data": got})
            except Exception as e:  # noqa: BLE001 - fail soft
                got = cached["data"] if cached else None
                if got is None:
                    return {**judge(None, None), "source": "", "error": str(e)[:200]}
    if got is None and cached:
        got = {**cached["data"], "source": cached["data"].get("source", "") + " (older than 30 days)"}
    if got is None:
        return {**judge(None, None), "source": "", "error": "no Google Ads API keys and nothing typed in"}
    return {**judge(got.get("searches"), got.get("cpc")), "source": got.get("source", ""), "keywords": got.get("keywords") or []}

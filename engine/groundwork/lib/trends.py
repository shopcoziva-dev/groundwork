"""Solo Scout check 2: the 5-year Google Trends line per niche option (US).

Google Trends has no stable official API, so: pytrends fetch -> cache (stores/_cache/trends/, 7 days; an older copy is
used when a fetch fails) -> fail soft ("unavailable", never blocks). The tag is a pure function of the cached monthly
line, so the same data always gives the same tag:
  declining  - the last 12 months average under 75% of the first 2 years' average
  seasonal   - the average year swings: busiest month >= 1.8x the quietest AND the top months hold the volume
  evergreen  - otherwise
Peak months = calendar months at or above 85% of the busiest month in the average year.
"""
from __future__ import annotations

import re
import time

from .core import STORES, read_json, write_json

TTL_DAYS = 7
CACHE = STORES / "_cache" / "trends"
DECLINE = 0.75
SEASON_SWING = 1.8
PEAK_SHARE = 0.85
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _key(kw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", kw.lower()).strip("-")[:60]


def monthly(points: list[tuple[str, float]]) -> list[list]:
    """Weekly [(YYYY-MM-DD, value)] -> [[YYYY-MM, mean]] (a part month at either end is kept; Trends weeks are even)."""
    by: dict[str, list[float]] = {}
    for d, v in points:
        by.setdefault(str(d)[:7], []).append(float(v))
    return [[m, round(sum(v) / len(v), 2)] for m, v in sorted(by.items())]


def tag(series: list[list]) -> dict:
    """[[YYYY-MM, value], ...] (60ish months) -> {tag, peaks, change, profile}. Pure."""
    vals = [float(v) for _, v in series]
    if len(vals) < 24 or sum(vals) <= 0:
        return {"tag": "unavailable", "peaks": [], "text": "Trends: not enough data"}
    first = sum(vals[:24]) / 24
    last = sum(vals[-12:]) / 12
    change = (last / first - 1) if first > 0 else 0.0
    prof = []
    for i in range(12):
        xs = [float(v) for m, v in series if int(m[5:7]) == i + 1]
        prof.append(sum(xs) / len(xs) if xs else 0.0)
    mx, mn = max(prof), min(prof)
    peaks = [MONTHS[i] for i, p in enumerate(prof) if mx > 0 and p >= PEAK_SHARE * mx]
    swing = mx / mn if mn > 0 else 99.0
    if first > 0 and last < DECLINE * first:
        t = "declining"
    elif swing >= SEASON_SWING and len(peaks) <= 5:
        t = "seasonal"
    else:
        t = "evergreen"
    txt = {"declining": f"declining ({change:+.0%} vs 5 years ago)", "seasonal": f"seasonal, peaks {_span(peaks)}",
           "evergreen": f"evergreen ({change:+.0%} over 5 years)"}[t]
    return {"tag": t, "peaks": peaks, "change": round(change, 3), "swing": round(swing, 2),
            "profile": [round(p, 1) for p in prof], "text": txt}


def _span(peaks: list[str]) -> str:
    """Months as runs, wrapping the year end: [Jan, Feb, Nov, Dec] -> 'Nov-Feb'."""
    if not peaks:
        return "-"
    on = {MONTHS.index(p) for p in peaks}
    if len(on) == 12:
        return "all year"
    start = next(i for i in range(12) if i in on and (i - 1) % 12 not in on)
    runs, i = [], start
    for _ in range(12):
        j = (i + 0) % 12
        if j in on and (j - 1) % 12 not in on:
            k = j
            while (k + 1) % 12 in on:
                k = (k + 1) % 12
            runs.append(MONTHS[j] if k == j else f"{MONTHS[j]}-{MONTHS[k]}")
        i = (i + 1) % 12
    return ", ".join(runs)


def _fetch(kw: str) -> list[list]:
    from pytrends.request import TrendReq   # optional dependency; ImportError -> fail soft
    py = TrendReq(hl="en-US", tz=360, timeout=(10, 25))   # no retries=: pytrends' Retry call breaks on urllib3 2.x
    for attempt in range(2):
        try:
            py.build_payload([kw], timeframe="today 5-y", geo="US")
            df = py.interest_over_time()
            break
        except Exception:  # noqa: BLE001 - usually a 429; one slow retry, then fail soft
            if attempt:
                raise
            time.sleep(8)
    if df is None or df.empty:
        return []
    return monthly([(str(i.date()), float(r[kw])) for i, r in df.iterrows()])


def check(kw: str, refresh: bool = False) -> dict:
    """Trend for one niche option. Never raises."""
    path = CACHE / f"{_key(kw)}.json"
    cached = read_json(path, None)
    fresh = cached and time.time() - cached.get("ts", 0) < TTL_DAYS * 86400
    series, src, err = None, "", ""
    if cached and fresh and not refresh:
        series, src = cached["series"], "Google Trends (cached " + cached.get("at", "") + ")"
    else:
        try:
            series = _fetch(kw)
            if series:
                write_json(path, {"ts": time.time(), "at": time.strftime("%Y-%m-%d"), "kw": kw, "series": series})
                src = "Google Trends"
        except Exception as e:  # noqa: BLE001 - rate limits and format changes happen; never block
            err = f"{type(e).__name__}: {str(e)[:120]}"
        if not series and cached:
            series, src = cached["series"], "Google Trends (older copy from " + cached.get("at", "") + ")"
    if not series:
        return {"status": "unavailable", "tag": "unavailable", "peaks": [], "series": [], "text": "Trends: unavailable", "error": err}
    t = tag(series)
    st = "unavailable" if t["tag"] == "unavailable" else "fail" if t["tag"] == "declining" else "pass"
    return {"status": st, **t, "series": series, "source": src, "text": "Trends: " + t["text"]}

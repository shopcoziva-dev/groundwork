"""Launch check 4: the ROAS ladder (Control layer). Proposes a budget step; it NEVER changes the budget.
Solo proposes, Jason approves on the page, Jason sets the new budget in Google Ads.

Defaults (all can be changed per store under store.json "ladder"):
  ROAS >= 2.5x over the trailing window  -> raise budget 20%   (only with enough conversions and settled data)
  1.5x - 2.5x                            -> hold
  under 1.5x                             -> cut budget 25%; under 1.0x -> pause
Step-up gates: trailing window 14 days, at least 10 conversions in it, campaign at least 14 days old (learning), and
7+ days since the last budget change. Cuts and pauses wait for day 7 (the first stop-loss checkpoint) and 5 days since
the last change. Runs alongside the day 7/14/30 stop rules in ads.json; where they disagree the stricter one wins.
ROAS = conversion value / spend over the window.
"""
from __future__ import annotations

import datetime as dt

DEFAULTS = {"up_roas": 2.5, "up_pct": 0.20, "hold_roas": 1.5, "cut_pct": 0.25, "pause_roas": 1.0,
            "window_days": 14, "min_conversions": 10, "min_day_up": 14, "min_days_since_change_up": 7,
            "min_day_cut": 7, "min_days_since_change_cut": 5}


def config(over: dict | None = None) -> dict:
    return {**DEFAULTS, **{k: v for k, v in (over or {}).items() if k in DEFAULTS}}


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(str(s)[:10])


def propose(daily: list[dict], budget: float, changes: list[dict] | None = None, cfg: dict | None = None,
            today: str | None = None) -> dict:
    """daily: [{"date": "YYYY-MM-DD", "spend", "conversions", "value"}]; budget: today's daily budget; changes: past
    approved steps [{"date", "from", "to"}]. Pure: same input -> same proposal."""
    c = config(cfg)
    rows = sorted((r for r in daily or [] if r.get("date")), key=lambda r: r["date"])
    if not rows:
        return {"action": "wait", "from": budget, "to": budget, "why": "no ad data yet"}
    end = _d(today) if today else _d(rows[-1]["date"])
    start = _d(rows[0]["date"])
    age = (end - start).days + 1
    win = [r for r in rows if (end - _d(r["date"])).days < c["window_days"]]
    spend = sum(float(r.get("spend") or 0) for r in win)
    conv = sum(float(r.get("conversions") or 0) for r in win)
    value = sum(float(r.get("value") or 0) for r in win)
    roas = value / spend if spend > 0 else 0.0
    last = max((_d(x["date"]) for x in changes or [] if x.get("date")), default=None)
    since = (end - last).days if last else age
    base = {"from": budget, "roas": round(roas, 2), "spend": round(spend, 2), "conversions": round(conv, 1),
            "value": round(value, 2), "window_days": min(c["window_days"], age), "day": age, "days_since_change": since}
    if roas >= c["up_roas"]:
        gates = []
        if age < c["min_day_up"]:
            gates.append(f"day {age} (no step-up before day {c['min_day_up']}: learning)")
        if conv < c["min_conversions"]:
            gates.append(f"{conv:g} conversions in the window (need {c['min_conversions']})")
        if since < c["min_days_since_change_up"]:
            gates.append(f"{since} days since the last change (need {c['min_days_since_change_up']})")
        if gates:
            return {**base, "action": "hold", "to": budget, "why": f"ROAS {roas:.2f}x qualifies, but waiting: " + "; ".join(gates)}
        to = round(budget * (1 + c["up_pct"]), 2)
        return {**base, "action": "raise", "to": to, "needs_approval": True,
                "why": f"ROAS {roas:.2f}x over {base['window_days']} days with {conv:g} conversions -> raise {c['up_pct']:.0%}"}
    if roas >= c["hold_roas"]:
        return {**base, "action": "hold", "to": budget, "why": f"ROAS {roas:.2f}x is between {c['hold_roas']}x and {c['up_roas']}x"}
    if age < c["min_day_cut"] or since < c["min_days_since_change_cut"]:
        return {**base, "action": "hold", "to": budget,
                "why": f"ROAS {roas:.2f}x is low, but day {age} / {since} days since a change is too early to cut"}
    if roas < c["pause_roas"]:
        return {**base, "action": "pause", "to": 0, "needs_approval": True,
                "why": f"ROAS {roas:.2f}x under {c['pause_roas']}x -> pause and check feed, prices and tracking"}
    to = round(budget * (1 - c["cut_pct"]), 2)
    return {**base, "action": "cut", "to": to, "needs_approval": True, "why": f"ROAS {roas:.2f}x under {c['hold_roas']}x -> cut {c['cut_pct']:.0%}"}


def pull(customer: str, days: int = 30) -> list[dict]:
    """Daily spend/conversions/value for the store's own Google Ads account (read-only GAQL)."""
    from . import gads
    q = ("SELECT segments.date, metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM customer "
         f"WHERE segments.date DURING LAST_{30 if days >= 30 else 14}_DAYS")
    js = gads.call("/googleAds:searchStream", {"query": q}, customer=customer)
    out = []
    for batch in js if isinstance(js, list) else [js]:
        for r in batch.get("results") or []:
            m = r.get("metrics") or {}
            out.append({"date": r["segments"]["date"], "spend": round(float(m.get("costMicros") or 0) / 1e6, 2),
                        "conversions": float(m.get("conversions") or 0), "value": float(m.get("conversionsValue") or 0)})
    return sorted(out, key=lambda r: r["date"])

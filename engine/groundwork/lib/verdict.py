"""Solo check 3: one pass/fail badge per niche option and per product. Pure functions, no network.

Niche badge = demand (Keyword Planner) + trend (Google Trends) + margin (the gate's live-formula profit check).
  fail when any part fails; "unavailable" parts are shown but never fail a niche (fail soft).
  A failed niche is flagged; Jason can override on the page (recorded with the reason in gate.json).
Product badge = the live floor (pricing.decide says PRICE: $20 and 25% of what the customer pays, after the code)
  AND still >= 15% net when ad cost per sale runs 25% over plan (the stress case). Aim: >= 20% net.
  A product that fails is never listed.
"""
from __future__ import annotations

STRESS_FLOOR = 0.15
AIM = 0.20


def niche(demand: dict | None, trend: dict | None, margin: dict | None) -> dict:
    parts = {"demand": demand or {"status": "unavailable", "text": "Keyword Planner: unavailable"},
             "trend": trend or {"status": "unavailable", "text": "Trends: unavailable"},
             "margin": margin or {"status": "unavailable", "text": "Margin: checked when picked"}}
    fails = [k for k, v in parts.items() if v.get("status") == "fail"]
    missing = [k for k, v in parts.items() if v.get("status") == "unavailable"]
    badge = "fail" if fails else "pass"
    return {"badge": badge, "fails": fails, "unchecked": missing,
            "why": "; ".join(parts[k]["text"] for k in fails),
            "seasonal": (trend or {}).get("tag") == "seasonal", "peaks": (trend or {}).get("peaks") or [],
            "ideal": bool((demand or {}).get("ideal")),
            "parts": {k: {"status": v.get("status"), "text": v.get("text")} for k, v in parts.items()}}


def margin_from_gate(g: dict) -> dict:
    p = ((g or {}).get("checks") or {}).get("profit")
    if not p:
        return {"status": "unavailable", "text": "Margin: checked when picked"}
    return {"status": "pass" if p.get("ok") else "fail", "text": "Margin: " + p.get("text", "")}


def product(d: dict) -> dict:
    """d = pricing.decide() output."""
    if (d or {}).get("decision") != "PRICE":
        return {"badge": "fail", "aim": False, "why": (d or {}).get("reason") or "below the profit floor"}
    st = d.get("stress_pct_after_code")
    net = d.get("net_pct_after_code") or 0
    if st is not None and st < STRESS_FLOOR:
        return {"badge": "fail", "aim": False, "why": f"only {st:.0%} net if ad cost runs 25% over plan (need {STRESS_FLOOR:.0%})"}
    return {"badge": "pass", "aim": net >= AIM, "why": ""}


def overridden(g: dict) -> bool:
    return bool(((g or {}).get("verdict") or {}).get("override"))


def blocks(g: dict) -> str:
    """'' when the niche may go on, else why not (a failed verdict nobody overrode)."""
    v = (g or {}).get("verdict") or {}
    if v.get("badge") == "fail" and not v.get("override"):
        return "niche verdict failed (" + (v.get("why") or "see Step 1") + "); override it on Step 1 to go on"
    return ""

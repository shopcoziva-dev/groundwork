"""Pricing for any Groundwork store: the live Coziva formula (stage2.py, RULES.md 2.x, 2026-10-10), with every number
in one rules dict so a store or niche profile can change it. The regression test checks this gives the same price as
stage2.decide() on every collected Coziva ad file.

  M  = market average: per search, the first 5 distinct ads; ads > outlier_x x the average of the others are dropped;
       M = mean of the per-search averages
  price = x9.99 round-UP of M x multiplier(M)   (curve: $40 -> 2.0x, $75 -> 1.8x, $150 -> 1.6x, $300 -> 1.4x)
  cap   = never above the top of the MAINSTREAM market (highest ad after dropping > 2x-median outliers)
  floor = net >= min_net_per_sale AND net >= min_margin_of_paid of what the customer pays, after the standing discount
          code and card fees, at the planned ad cost per sale
  PRICE when floor <= capped price; PARK when the floor is above what the market pays; REVIEW when there are too few ads.
  compare-at = price + compare_at_offset.

Scout's quick test for a whole corner (no supplier cost yet) is score_corner(): the beta app's exact rule.
"""
from __future__ import annotations

import math

DEFAULT_RULES = {
    "ad_cost": {"apparel": 34.0, "home": 40.0, "other": 40.0},   # planned ad spend per sale, by category
    "discount_code_pct": 0.10,
    "fee_pct": 0.029,
    "fee_fixed": 0.30,
    "min_net_per_sale": 20.0,
    "min_margin_of_paid": 0.25,
    "compare_at_offset": 30.0,
    "market_min_niches": 2,
    "market_min_ads": 3,
    "outlier_x": 2.0,
    "multiplier_curve": [[40, 2.0], [75, 1.8], [150, 1.6], [300, 1.4]],
    "stress_ad": 0.25,
}
CATEGORY_ALIAS = {"home_goods": "home", "apparel": "apparel", "home": "home"}

# Scout (beta app RULES, identical numbers)
SCOUT = {"margin_floor": 0.15, "stress_ad_cost": {"apparel": 34, "home": 40, "other": 40}, "payment_fee": 0.03,
         "min_advertisers": 5, "cost_guess": 0.30}


def rules_for(overrides: dict | None = None) -> dict:
    r = {**DEFAULT_RULES, **(overrides or {})}
    r["ad_cost"] = {**DEFAULT_RULES["ad_cost"], **((overrides or {}).get("ad_cost") or {})}
    return r


def category(c: str) -> str:
    c = CATEGORY_ALIAS.get(str(c or "").lower(), str(c or "").lower())
    return c if c in ("apparel", "home") else "other"


def up99(v: float) -> float:
    return round(10 * math.ceil((v - 9.99) / 10 - 1e-9) + 9.99, 2)


def down99(v: float) -> float:
    return round(10 * math.floor((v - 9.99) / 10 + 1e-9) + 9.99, 2)


def multiplier(m: float, curve) -> float:
    if m <= curve[0][0]:
        return curve[0][1]
    if m >= curve[-1][0]:
        return curve[-1][1]
    for (a, x), (b, y) in zip(curve, curve[1:]):
        if a <= m <= b:
            return x + (y - x) * (m - a) / (b - a)
    return curve[-1][1]


def _first5(rows):
    seen, out = set(), []
    for t, p in rows:
        if t in seen:
            continue
        seen.add(t)
        out.append((t, float(p)))
        if len(out) == 5:
            break
    return out


def market(ads: dict, r: dict):
    """-> (M, mainstream_high, per_search_avgs, dropped_outliers) or (None, None, {}, []) when too thin."""
    per = {q: pk for q, rows in ads.get("niches", {}).items() if len(pk := _first5(rows)) >= r["market_min_ads"]}
    if len(per) < r["market_min_niches"]:
        return None, None, {q: round(sum(p for _, p in pk) / len(pk), 2) for q, pk in per.items()}, []
    allp = [(q, p) for q, pk in per.items() for _, p in pk]
    keep, dropped = [], []
    for i, (q, p) in enumerate(allp):
        others = [x[1] for j, x in enumerate(allp) if j != i]
        (dropped if p > r["outlier_x"] * sum(others) / len(others) else keep).append((q, p))
    navg = [sum(p for qq, p in keep if qq == q) / sum(1 for qq, _ in keep if qq == q) for q in per if any(qq == q for qq, _ in keep)]
    m = round(sum(navg) / len(navg), 2)
    # mainstream top: every distinct ad across all searches, drop > outlier_x x median
    seen, ps = set(), []
    for rows in ads["niches"].values():
        for t, p in rows:
            if t not in seen:
                seen.add(t)
                ps.append(float(p))
    ps.sort()
    med = ps[len(ps) // 2]
    high = max(x for x in ps if x <= r["outlier_x"] * med)
    avgs = {q: round(sum(p for _, p in pk) / len(pk), 2) for q, pk in per.items()}
    return m, high, avgs, [p for _, p in dropped]


def net_after_code(price: float, landed: float, ad: float, r: dict) -> float:
    return price * (1 - r["discount_code_pct"]) * (1 - r["fee_pct"]) - landed - ad - r["fee_fixed"]


def floor_price(landed: float, ad: float, r: dict) -> float:
    paid = 1 - r["discount_code_pct"]
    by_net = (landed + ad + r["fee_fixed"] + r["min_net_per_sale"]) / (paid * (1 - r["fee_pct"]))
    by_pct = (landed + ad + r["fee_fixed"]) / (paid * (1 - r["fee_pct"] - r["min_margin_of_paid"]))
    return up99(max(by_net, by_pct))


def decide(landed: float, cat: str, ads: dict, rules: dict | None = None) -> dict:
    r = rules_for(rules)
    ad = float(r["ad_cost"][category(cat)])
    m, high, avgs, dropped = market(ads, r)
    d = {"landed": round(float(landed), 2), "ad_spend": ad, "per_search_avg": avgs}
    if m is None:
        d.update(decision="REVIEW", reason=f"only {len(avgs)} usable search(es); need {r['market_min_niches']} with >= {r['market_min_ads']} ads each")
        return d
    f = floor_price(landed, ad, r)
    mult = multiplier(m, r["multiplier_curve"])
    wanted = up99(m * mult)
    cap = down99(high + 0.005) if high > 10 else wanted
    capped = min(wanted, cap)
    d.update(M=m, multiplier=round(mult, 3), wanted=wanted, market_high=high, cap=cap, F=f, outliers_dropped=dropped)
    if f > capped:
        d.update(decision="PARK", reason=f"needs ${f} to clear the profit floor but the market tops out at ${high} (cap ${capped})")
        return d
    price = capped
    net = net_after_code(price, landed, ad, r)
    paid = price * (1 - r["discount_code_pct"])
    d.update(decision="PRICE", price=price, compare_at=(round(price + r["compare_at_offset"], 2) if r["compare_at_offset"] else None),
             net_after_code=round(net, 2), net_pct_after_code=round(net / paid, 3),
             stress_pct_after_code=round(net_after_code(price, landed, ad * (1 + r["stress_ad"]), r) / paid, 3))
    return d


def to99(x: float) -> float:   # beta app rounding: nearest .99
    return max(0.99, round(x + 0.01) - 0.01)


def score_corner(c: dict) -> dict:
    """Scout verdict for one corner from: advertisers, typical_price, typical_cost (optional), category, seasonality
    (year-round|seasonal), demand_trend (rising|flat|falling), policy_risk (low|medium|high). Same rule as the beta app."""
    typical = float(c.get("typical_price") or 0)
    price = to99(typical * 2)
    cat = category(c.get("category"))
    ad = SCOUT["stress_ad_cost"][cat]
    cost = float(c["typical_cost"]) if float(c.get("typical_cost") or 0) > 0 else round(typical * SCOUT["cost_guess"])
    fees = price * SCOUT["payment_fee"]
    net = price - cost - ad - fees
    margin = net / price if price > 0 else -1
    adv = int(c.get("advertisers") or 0)
    gates = [
        {"ok": adv >= SCOUT["min_advertisers"], "text": f"already selling: ~{adv} stores advertising (need {SCOUT['min_advertisers']}+)"},
        {"ok": margin >= SCOUT["margin_floor"], "text": f"at 2x you keep ${net:.0f} a sale after a ${ad} ad cost ({margin:.0%}, need {SCOUT['margin_floor']:.0%})"},
        {"ok": c.get("policy_risk") != "high", "text": f"Google ad rules: {c.get('policy_risk') or 'low'}"},
    ]
    s = min(adv / 20, 1) * 35 + max(0.0, min((margin - SCOUT["margin_floor"]) / 0.25, 1)) * 35
    s += {"rising": 15, "flat": 10}.get(c.get("demand_trend"), 0)
    s += 15 if c.get("seasonality") == "year-round" else 6
    s = round(s)
    verdict = "no" if not all(g["ok"] for g in gates) else ("go" if s >= 60 else "maybe")
    return {"price": price, "cost": cost, "ad": ad, "net": round(net, 2), "margin": round(margin, 3), "gates": gates, "score": s, "verdict": verdict}

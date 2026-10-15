"""Stage 7, Ads: the Performance Max campaign as a ready-to-enter settings file, launch gates and stop rules.

Settings follow the Coziva campaign as built (2026-10-11): one PMax campaign on all products, US, English, goal
Purchases, Maximize conversions with no target while learning, $30/day. Claude writes the asset text inside Google's
character limits (checked in code) and the search themes from the Scout corners. Break-even and stop rules come from the
live listings' real prices and costs. Creating the campaign and adding payment stay with Jason.
Writes stores/<slug>/ads.json.
"""
from __future__ import annotations

from ..lib import ai, engine, rulebook
from ..lib.core import Log, Stop, Store, now

ASSETS = """Write Google Performance Max asset text for a US online store. Store: {name} ({domain}). Sells: {niche}.
Voice: {voice}. Best sellers: {products}.
Answer JSON: {{"headlines": [15 strings, each <= 30 characters], "long_headlines": [5 strings, each <= 90 characters],
"descriptions": [5 strings, each <= 90 characters, the first <= 60], "search_themes": [up to 25 phrases US shoppers type],
"business_name": "<= 25 characters"}}
No prices, no discounts, no 'free shipping' unless asked, no superlatives Google rejects (best, #1), no ALL CAPS words,
no exclamation mark in headlines. Real benefits only."""


def _check(a) -> list[str]:
    e = []
    lim = {"headlines": (15, 30), "long_headlines": (5, 90), "descriptions": (5, 90)}
    for k, (n, mx) in lim.items():
        xs = (a or {}).get(k) or []
        if len(xs) < n:
            e.append(f"{k}: need {n}")
        e += [f"{k} too long ({len(x)} > {mx}): {x}" for x in xs if len(x) > mx]
        if k == "headlines":
            e += [f"headline has '!': {x}" for x in xs if "!" in x]
    if (a or {}).get("descriptions") and len(a["descriptions"][0]) > 60:
        e.append("first description must be <= 60 characters")
    return e


def run(store: Store, log: Log, daily: float = 30.0) -> dict:
    profile, scout, brand = store.load("profile.json"), store.load("scout.json"), store.load("brand.json") or {}
    if not profile and engine.rules():
        from . import profile as _prof
        profile = _prof.run(store, log)
    if not profile or (not scout and not engine.rules()):
        raise Stop("run scout and profile first")
    listings = [store.load(f"listings/{p.name}") for p in store.path("listings").glob("*.json")]
    live = [r for r in listings if r and r.get("live")]
    shelf = (store.load("shelf.json") or {}).get("items", {})
    rows = [shelf.get(r["key"]) for r in live if shelf.get(r["key"])] or [x for x in shelf.values() if x.get("status") == "READY"]
    if not rows:
        raise Stop("no priced products yet (run catalog)")
    code = float(store.get("discount_code_pct") or 0)
    take = [x["price"] * (1 - code) * (1 - 0.029) - 0.30 - x["landed"] for x in rows if x.get("price")]
    k = round(sum(take) / len(take), 2)   # average take-home per sale BEFORE ad cost = the most an order can cost and break even
    if engine.rules():
        a = rulebook.ad_assets(brand.get("name") or store.get("name") or "Store", profile["niche"], list(profile["types"]), brand.get("domain", ""))
        errs = _check(a)
        if errs:
            raise Stop("ad text rules: " + "; ".join(errs))
    a = a if engine.rules() else ai.ask_json("ads_assets", ASSETS.format(name=brand.get("name") or store.get("name"), domain=brand.get("domain", ""), niche=profile["niche"],
                                                voice=profile.get("voice", ""), products=", ".join(sorted({x.get("type", "") for x in rows})[:10])),
                    "Write them.", check=_check, max_tokens=2500)
    month = round(daily * 30.4)
    plan = {
        "written": now(),
        "campaign": {"name": f"PMAX_all products_US_${int(daily)}/day", "type": "Performance Max", "goal": "Purchases",
                     "bidding": "Maximize conversions (no target ROAS/CPA while learning)", "budget_per_day": daily,
                     "locations": "United States, presence only (people IN the US)", "language": "English",
                     "feed": "Merchant Center, all products", "final_url_expansion": "off for the first 30 days (send traffic to product pages)",
                     "auto_apply_recommendations": "off", "asset_group": {"name": "All products", **a,
                                                                          "images": "the 01-studio and 02-hero photos of the top 10 listings + the logo"}},
        "launch_gates": ["one real test order placed, fulfilled by CJ, tracking received", "Purchase conversion shows a real order value (not $1)",
                         "every live listing CJ-linked (gw.py status shows 0 unlinked)", "Merchant Center: products approved, shipping + return policy match the site",
                         "checkout shipping rates match the shipping policy"],
        "break_even": {"take_home_per_sale_before_ads": k, "break_even_cost_per_order": k, "orders_needed_per_month_to_break_even": round(month / k, 1) if k > 0 else None,
                       "monthly_ad_spend": month},
        "stop_rules": [f"day 7: no order at all -> check tracking, feed and prices before spending more",
                       f"150 clicks and no sale -> pause and check product pages / prices",
                       f"cost per order above ${k} after day 14 -> cut the worst products from the feed",
                       "never change budget or bidding during the first 14 days (learning)",
                       "day 30: keep, cut or scale on real ROAS; Jason decides"],
        "checkpoints": [7, 14, 30],
        "learning_phase": "about 14 days or ~30 conversions; expect uneven days, judge on 7-day totals",
    }
    store.save("ads.json", plan)
    log(f"# Ads plan: {plan['campaign']['name']} | break-even ${k} per order | ~{plan['break_even']['orders_needed_per_month_to_break_even']} orders a month to break even on ${month}")
    log("headlines: " + " | ".join(a["headlines"][:6]) + " ...")
    log(ai.usage_line())
    return plan

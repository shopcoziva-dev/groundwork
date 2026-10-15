"""Stage 1, Scout: is this niche worth a store? Claude researches it on the web; the verdict itself is code.

Claude splits the niche into 3-5 broad store directions ("corners", each a whole store's worth of products) and, from real searches, reports per corner:
how many stores advertise it, typical ad price, season, demand trend, Google ad-policy risk, and the CJ search words.
When SERPAPI_KEY is set, the advertiser count and typical price are replaced by real Google Shopping numbers.
The score and the go / maybe / no verdict use the beta app's rule (lib/pricing.score_corner): sell what already sells.
Scout is a conversation, not a one-shot: every run is a ROUND. Jason reacts with an angle ("only long-haired dogs",
"what about the gift angle?", "drop the cheap corner") and `gw.py scout --store S --angle "..."` researches again with
every earlier round in view. Rounds are kept in scout.json; nothing is built until Jason approves corners.
SUPPLY: a direction people buy is useless if CJ does not carry it (chandeliers, say). When CJ_API_KEY is set, every
direction is searched on CJ with its own supplier words before the verdict: too few matching products, or a typical
CJ cost that leaves no profit under the market top, makes it a NO right here, before any time goes into it.
Writes stores/<slug>/scout.json.
"""
from __future__ import annotations

import statistics

import os
import re

from ..lib import ai, market
from ..lib.cj import CJ
from ..lib.core import Log, Stop, Store, now
from ..lib.pricing import DEFAULT_RULES, category, floor_price, score_corner

SYSTEM = """You are the Scout for a US dropshipping store builder. The rule is SELL WHAT ALREADY SELLS: crowded is GOOD
(lots of stores advertising = proof people buy); we never look for "unsaturated" or unique products.
STORE DIRECTIONS ("corners"): each is a whole store the way a shopper thinks of one, defined by WHAT it sells, never by style.
FIRST decide if the niche is already one store. A niche that already names one product family ("floor lamps", "heated
jackets", "dog beds") IS the store: give exactly ONE corner with that name, plus at most ONE wider version if it would clearly
be a better store ("Floor & Table Lamps"). Only a BROAD niche ("lighting", "home decor") gets 3-4 corners, each a different
product family (for lighting: indoor lamps & fixtures, outdoor & landscape lighting, smart RGB lighting).
NEVER split by style, look or vibe (boho, rattan, minimalist, modern, luxury, cozy), material, colour, power source or a
feature: one floor-lamp store sells every style, and style is chosen later for the brand. Never two corners that sell
mostly the same products.
Research the niche on the web (Google Shopping listings, store sites, Google Trends articles, ad-policy pages). Then answer JSON:
{"niche": "...",
 "corners": [ {
   "name": "the store direction, 2-4 words",
   "why": "one plain sentence: who buys it and why",
   "search": "the exact phrase a US shopper types in Google Shopping",
   "category": "apparel" | "home" | "other",
   "typical_price": typical ad price in USD (median of what you saw),
   "top_price": the highest price a normal (non-premium-brand) store charges for it,
   "advertisers": number of DIFFERENT stores you saw advertising it (page one of shopping results),
   "seasonality": "year-round" | "seasonal",
   "season_note": "when it sells, if seasonal",
   "demand_trend": "rising" | "flat" | "falling",
   "policy_risk": "low" | "medium" | "high"  (Google Shopping rules: medical claims, weapons, adult, supplements, CBD, etc. are high),
   "policy_note": "why, if not low",
   "relevance": "high" | "medium" | "low"  (would a LOT of everyday US shoppers buy this for themselves or as a gift?
                 high = many everyday people; medium = a real but smaller crowd; low = few people, or mostly businesses),
   "buyers": "who buys, a few words",
   "b2b": true if it is mostly bought by businesses, contractors, pros or in bulk (this builder makes consumer stores only),
   "products": ["6-10 product types this store would carry"],
   "cj_keywords": ["3-6 short supplier-catalog search words, e.g. 'heated vest', 'usb hand warmer'"],
   "gendered": true if the products are sold as men's / women's versions,
   "evidence": ["2-4 URLs you actually looked at"]
 } ... 1 corner for a niche that is already one store, 3-4 for a broad one ],
 "notes": "anything that changes the call (e.g. trademark-heavy, very seasonal, fragile shipping)"}
Numbers must come from what you found, not guesses. If you could not find a number, use your best conservative reading and say so in why."""


def _check(a) -> list[str]:
    e = []
    cs = a.get("corners") if isinstance(a, dict) else None
    if not cs or not 1 <= len(cs) <= 5:
        return ["need 1-5 corners"]
    for i, c in enumerate(cs):
        for k in ("name", "search", "category", "typical_price", "advertisers", "policy_risk", "cj_keywords"):
            if c.get(k) in (None, "", []):
                e.append(f"corner {i} missing {k}")
        if c.get("category") not in ("apparel", "home", "other"):
            e.append(f"corner {i} category must be apparel|home|other")
    return e


def real_numbers(c: dict) -> dict:
    """Google Shopping numbers (serpapi) replace Claude's reading when available."""
    rows = market.serpapi(c["search"], n=40)
    if len(rows) < 5:
        return {}
    sellers = {t.split("|")[-1].strip().lower() for t, _ in rows}
    return {"advertisers": len(sellers), "typical_price": round(statistics.median(p for _, p in rows), 2), "numbers_from": "Google Shopping (serpapi)"}


def relevance(c: dict) -> dict:
    """The main question: would a lot of everyday shoppers buy this? Low relevance or business buyers = not a store we build."""
    if c.get("b2b"):
        return {"ok": False, "text": "mostly business buyers (B2B): not a shopper store"}
    r = str(c.get("relevance") or "medium").lower()
    return {"ok": r != "low", "text": f"relevance {r}: {c.get('buyers') or 'buyers not stated'}"}


def price_room(c: dict) -> dict:
    """Groundwork Solo gate (the beta app does not have it): the real pricing never goes above the top of the mainstream
    market, so the profit floor for a typical product must fit under that top. Typical landed cost is guessed at 30% of
    the typical price + $5 shipping when Scout has no supplier cost."""
    typical = float(c.get("typical_price") or 0)
    top = float(c.get("top_price") or 0) or typical * 1.5
    landed = float(c.get("typical_cost") or 0) or round(typical * 0.30 + 5, 2)
    f = floor_price(landed, DEFAULT_RULES["ad_cost"][category(c.get("category"))], DEFAULT_RULES)
    return {"ok": f <= top, "text": f"price room: the profit floor is ${f:.0f} (landed ~${landed:.0f} + ${DEFAULT_RULES['ad_cost'][category(c.get('category'))]:.0f} ads) "
            f"vs a market top of ~${top:.0f}"}


SUPPLY_MIN = 40   # matching CJ products per direction: 2x the 20-product catalog minimum, since about half get cut by shipping, stock, photo or price
SHIP_GUESS = 5.0  # US shipping added to the CJ cost until the catalog quotes the real freight


def _stem(w: str) -> str:
    return w[:-1] if w.endswith("s") and not w.endswith("ss") else w


def supply(c: dict, cj) -> dict:
    """Search CJ with the direction's own supplier words. Counts distinct products whose name really matches a word,
    and their typical cost, then checks that cost leaves the profit floor under the market top."""
    kws = [k for k in (c.get("cj_keywords") or [])[:5] if k] or list(c.get("products") or [])[:4]
    top = float(c.get("top_price") or 0) or float(c.get("typical_price") or 0) * 1.5
    hi = max(15.0, round(top * 0.6, 2))
    found: dict[str, dict] = {}
    per_kw: dict[str, int] = {}
    for kw in kws:
        words = [_stem(w) for w in re.findall(r"[a-z]+", kw.lower()) if len(w) > 3] or [kw.lower()]
        rows = cj.search(kw, size=40, min_price=2, max_price=hi)
        hits = [r for r in rows if any(re.search(rf"\b{re.escape(w)}", r["name"].lower()) for w in words)]
        per_kw[kw] = len(hits)
        for r in hits:
            found.setdefault(r["pid"], r)
    costs = sorted(r["price"] for r in found.values() if r.get("price"))
    med = costs[len(costs) // 2] if costs else 0.0
    n = len(found)
    landed = round(med + SHIP_GUESS, 2)
    ad = DEFAULT_RULES["ad_cost"][category(c.get("category"))]
    f = floor_price(landed, ad, DEFAULT_RULES) if med else 0
    enough = n >= SUPPLY_MIN
    room = bool(med) and (f <= top if top > 0 else True)   # no market top on record = cannot judge price room here (catalog prices it for real)
    if not enough:
        text = f"CJ supply: only {n} matching products (need {SUPPLY_MIN}+)"
    elif not room:
        text = f"CJ supply: typical cost ${med:.0f} needs ${f:.0f} to profit, market tops at ${top:.0f}"
    else:
        text = f"CJ supply: {n} matching products, typical cost ${med:.0f}"
    return {"ok": enough and room, "text": text, "n": n, "median_cost": med, "landed_guess": landed, "by_keyword": per_kw,
            "sample": [r["name"][:70] for r in list(found.values())[:5]]}


def cj_client(store: Store, log: Log):
    """CJ when it is set up (or mocked in tests); None means the supply check is skipped and marked unchecked."""
    if not (os.environ.get("CJ_API_KEY") or getattr(CJ, "_mocked", False)):
        return None
    try:
        return CJ(store, log)
    except Stop as e:
        log(f"supply check skipped: {e}")
        return None


def check_supply(corners: list[dict], cj, log: Log) -> None:
    """Adds the supply gate to each scored corner in place; a failed supply makes it a NO."""
    for c in corners:
        if cj is None:
            c["supply"] = {"ok": None, "text": "CJ supply: not checked (no CJ key)"}
            continue
        try:
            sp = supply(c, cj)
        except Stop as e:
            sp = {"ok": None, "text": f"CJ supply: check failed ({e})"}
        c["supply"] = sp
        if sp["ok"] is None:
            continue
        if sp.get("median_cost"):
            c["typical_cost"] = sp["landed_guess"]
        c["score"]["gates"].append({"ok": sp["ok"], "text": sp["text"]})
        if not sp["ok"]:
            c["score"]["verdict"] = "no"
        log(f"  {c['name'][:34]:34} {sp['text']}")


def _round_brief(r: dict) -> str:
    rows = "; ".join(f"{c['name']} ({c['verdict']}, score {c['score']}, ~{c['advertisers']} advertisers, ~${c['typical_price']})" for c in r["corners"])
    return f"- round {r['n']}" + (f" (Jason's angle: {r['angle']})" if r.get("angle") else "") + f": {rows}"


def run(store: Store, log: Log, niche: str | None = None, angle: str = "", like: str = "") -> dict:
    niche = niche or store.get("niche")
    prev = store.load("scout.json") or {}
    rounds = prev.get("rounds") or []
    log(f"# Scout round {len(rounds) + 1}: {niche}" + (f" | angle: {angle}" if angle else ""))
    ask = f"Niche: {niche}\nMarket: United States. Today: {now()}."
    if rounds:
        ask += "\nEarlier rounds (do not just repeat them; go where Jason's angle points):\n" + "\n".join(_round_brief(r) for r in rounds)
    if like:
        have = ", ".join(c["name"] for c in prev.get("corners") or [])
        ask += (f"\nMORE LIKE THIS: Jason likes the direction \"{like}\". Give 3-4 NEW sister directions to it: the same kind of everyday buyer "
                f"and price feel, but a different product family, or a wider or narrower version of it. Do NOT repeat any of: {have}.")
        angle = angle or f"more like {like}"
    if angle and not like:
        ask += f"\nJason's angle for this round: {angle}\nResearch corners that follow this angle; keep a strong earlier corner only if it still fits."
    ans = ai.ask_json("scout", SYSTEM, ask, web=12, max_tokens=6000, check=_check)
    corners = []
    for c in ans["corners"]:
        c = {**c, **real_numbers(c)}
        c.setdefault("numbers_from", "Claude web research")
        c["score"] = score_corner(c)
        room = price_room(c)
        rel = relevance(c)
        c["score"]["gates"] += [room, rel]
        if not (room["ok"] and rel["ok"]):
            c["score"]["verdict"] = "no"
        corners.append(c)
    log("checking CJ supply for each direction...")
    check_supply(corners, cj_client(store, log), log)
    corners.sort(key=lambda c: ({"go": 0, "maybe": 1, "no": 2}[c["score"]["verdict"]], -c["score"]["score"]))
    go = [c for c in corners if c["score"]["verdict"] == "go"]
    maybe = [c for c in corners if c["score"]["verdict"] == "maybe"]
    verdict = "go" if go else ("maybe" if maybe else "no")
    rounds.append({"n": len(rounds) + 1, "at": now(), "angle": angle,
                   "corners": [{"name": c["name"], "verdict": c["score"]["verdict"], "score": c["score"]["score"],
                                "advertisers": int(c["advertisers"]), "typical_price": float(c["typical_price"])} for c in corners]})
    pool = {c["name"]: c for c in prev.get("corners") or []}   # every corner from every round stays approvable
    pool.update({c["name"]: {**c, "round": len(rounds), **({"like": like} if like else {})} for c in corners if not (like and c["name"] in pool)})
    allc = sorted(pool.values(), key=lambda c: ({"go": 0, "maybe": 1, "no": 2}[c["score"]["verdict"]], -c["score"]["score"]))
    out = {"niche": niche, "verdict": verdict, "scouted": now(), "corners": allc, "this_round": [c["name"] for c in corners],
           "notes": ans.get("notes", ""), "approved": None, "rounds": rounds}   # nothing is built until Jason approves (gw.py approve)
    store.save("scout.json", out)
    log(f"\n{'corner':34} {'verdict':8} {'score':>5} {'ads':>4} {'price':>7} {'keep/sale':>9}  why")
    for c in corners:
        s = c["score"]
        log(f"{c['name'][:34]:34} {s['verdict'].upper():8} {s['score']:>5} {int(c['advertisers']):>4} ${float(c['typical_price']):>6.0f} ${s['net']:>8.0f}  "
            + "; ".join(g["text"] for g in s["gates"] if not g["ok"]))
    log(f"\nVERDICT this round: {verdict.upper()}" + (f" - strongest: {', '.join(c['name'] for c in go)}" if go else ""))
    if len(allc) > len(corners):
        log("earlier rounds still on the table: " + ", ".join(f"{c['name']} ({c['score']['verdict']})" for c in allc if c["name"] not in {x["name"] for x in corners}))
    log("Next: `scout --angle \"...\"` for another round, `supply --direction \"...\"` to check one you typed, or `approve --corners \"A,B\"`.")
    log(ai.usage_line())
    return out


def run_supply(store: Store, log: Log, direction: str = "", given: dict | None = None) -> dict:
    """Re-check CJ supply on the scouted directions, or add and check one Jason named (e.g. picked on the Solo page).
    `given` = the direction exactly as the Solo page scored it (prices, category, supplier words): used as-is, so the
    pipeline judges the same direction Jason picked instead of a guess with no prices."""
    sc = store.load("scout.json")
    if not sc and given:
        sc = {"niche": store.get("niche"), "verdict": "maybe", "scouted": now(), "corners": [], "rounds": [], "approved": None, "notes": "from the Solo page pick"}
    if not sc:
        raise Stop("run scout first")
    if given and direction:
        sc["corners"] = [c for c in sc["corners"] if c["name"].lower() != direction.lower()]
        c = {"name": direction, "advertisers": int(given.get("advertisers") or 0), "typical_price": float(given.get("typical_price") or 0),
             "top_price": float(given.get("top_price") or 0), "category": given.get("category") or "other",
             "cj_keywords": list(given.get("cj_keywords") or []), "products": list(given.get("products") or []), "search": (given.get("cj_keywords") or [direction])[0],
             "relevance": given.get("relevance") or "high", "numbers_from": "Solo page pick"}
        c["score"] = {"gates": [], "verdict": given.get("verdict") or "go", "score": 0, "net": 0}
        sc["corners"].append(c)
    cj = cj_client(store, log)
    if cj is None:
        raise Stop("CJ_API_KEY is not set: supply cannot be checked")
    todo = [c for c in sc["corners"] if not direction or c["name"].lower() == direction.lower()]
    if direction and not todo:
        ans = ai.ask_json("supply_kw", "Give CJdropshipping supplier search words for a US store direction. Answer JSON: "
                          '{"cj_keywords": ["3-6 short supplier-catalog search words"], "category": "apparel|home|other", "products": ["6-8 product types"]}',
                          f"Niche: {sc['niche']}. Direction: {direction}.", max_tokens=600,
                          check=lambda a: [] if isinstance(a, dict) and a.get("cj_keywords") else ["need cj_keywords"])
        c = {"name": direction, "typical_price": 0, "advertisers": 0, **ans, "numbers_from": "supply check only (not scouted)"}
        c["score"] = {"gates": [], "verdict": "maybe", "score": 0, "net": 0}
        sc["corners"].append(c)
        todo = [c]
    for c in todo:
        c["score"]["gates"] = [g for g in c["score"]["gates"] if not str(g.get("text", "")).startswith("CJ supply")]
        if c["score"].get("verdict") == "no" and all(g["ok"] for g in c["score"]["gates"]):
            c["score"]["verdict"] = "go" if c["score"].get("score", 0) >= 60 else "maybe"
    log(f"# CJ supply: {', '.join(c['name'] for c in todo)}")
    check_supply(todo, cj, log)
    store.save("scout.json", sc)
    return {c["name"]: c["supply"] for c in todo}

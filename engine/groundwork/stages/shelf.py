"""Solo step 8 part 1: draft the 50-product shelf from CJ, priced by the live formula, as diverse as possible.

Search order: the reference store's best sellers (as short CJ words) first, then the gate's supplier words.
Every candidate is type-checked (gate.classify), priced with real freight against that type's real ad prices
(lib/pricing.decide), and only products that clear the floor can go on the shelf. The shelf is filled round-robin
across product types (and the shopper split when the niche has one) so no type takes over; the next best passing
products are kept as the reserve that replaces anything Jason drops.
Writes shelf50.json: {items, reserve, diversity}.
"""
from __future__ import annotations

import re

from ..lib import ai, pricing, verdict
from ..lib.core import Log, Stop, Store, now
from . import gate
from .scout import SHIP_GUESS, cj_client

SHELF = 50
CANDIDATES = 180
_SKIP = {"with", "for", "and", "the", "set", "pack", "new", "led", "modern", "lamp", "light"}


def ref_keywords(store: Store, n: int = 5) -> list[str]:
    ref = store.load("reference.json") or {}
    out = []
    brand = set(re.findall(r"[a-z]+", str(ref.get("name") or "").lower()))
    for b in ref.get("best_sellers") or []:
        head = re.split(r"\b(?:with|for|in|by)\b|[-–|,]", str(b.get("title") or "").lower())[0]
        words = [w for w in re.findall(r"[a-z]+", head) if w not in brand and len(w) > 2]
        kw = " ".join(words[-3:])
        if kw and kw not in out:
            out.append(kw)
        if len(out) >= n:
            break
    return out


def diversity(items: list[dict], n_types: int = 5) -> dict:
    """0-100: share of the store's product types present (45), no type above 1/n of the shelf by much (35), price bands used (20)."""
    if not items:
        return {"score": 0, "types": {}, "bands": {}, "max_share": 0}
    types: dict[str, int] = {}
    for x in items:
        types[x["type"]] = types.get(x["type"], 0) + 1
    bands = {"under $60": 0, "$60-$120": 0, "$120+": 0}
    for x in items:
        bands["under $60" if x["price"] < 60 else "$60-$120" if x["price"] < 120 else "$120+"] += 1
    mx = max(types.values()) / len(items)
    used = sum(1 for v in bands.values() if v)
    fair = max(0.25, 1 / max(1, n_types)) + 0.05
    score = round(min(1, len(types) / max(1, n_types)) * 45 + (1 - max(0.0, mx - fair) / (1 - fair)) * 35 + used / 3 * 20)
    return {"score": score, "types": types, "bands": bands, "max_share": round(mx, 2)}


def run(store: Store, log: Log) -> dict:
    g = store.load("gate.json")
    if not g or not g.get("ok"):
        raise Stop("pass the direction gate first")
    if why := verdict.blocks(g):
        raise Stop(why)
    cj = cj_client(store, log)
    if cj is None:
        raise Stop("CJ_API_KEY is not set")
    ads = {t: {"niches": v["searches"]} for t, v in (g.get("market") or {}).items()}
    types = g["types"]
    stems = {gate._stem(w) for t in types + g["cj_keywords"] for w in re.findall(r"[a-z]+", t.lower()) if len(w) > 2}
    refk = [k for k in ref_keywords(store) if any(gate._stem(w) in stems for w in k.split())]   # only best sellers that are this niche
    kws = refk + [t for t in types if t not in refk]
    log(f"# Shelf: {g['niche']} | searching CJ: {', '.join(kws)}")
    tops = [v.get("H") or 0 for v in (g.get("market") or {}).values()]
    hi = max(15.0, round(max(tops or [80]) * 0.6, 2))
    found: dict[str, dict] = {}
    for kw in kws[:9]:
        need = [w for w in re.findall(r"[a-z]+", kw.lower()) if len(w) > 2]
        is_type = kw in types
        for r in cj.search(gate.singular(kw) if is_type else kw, size=100, min_price=2, max_price=hi):
            if is_type and not gate.has_all(r["name"], need):
                continue   # a type search keeps only names holding every word of the type
            if r["pid"] not in found:
                found[r["pid"]] = {**r, "from": ("their best seller: " + kw) if kw in refk else "CJ top seller"}
        log(f"  CJ '{kw}': {len(found)} so far")
    cand = sorted(found.values(), key=lambda r: (not r["from"].startswith("their"), -r["stores"]))[:CANDIDATES]
    log(f"  {len(found)} found, checking the top {len(cand)}")
    typed = []
    for i in range(0, len(cand), 60):   # classify in chunks the model answers reliably
        typed += gate.classify(g["niche"], types, cand[i:i + 60])
    pool = [(r, t) for r, t in zip(cand, typed) if t]
    log(f"  {len(pool)} of {len(cand)} candidates really are {', '.join(types)}")
    priced = []
    for r, t in pool:
        try:
            det = cj.product(r["pid"])
            vids = [v["vid"] for v in det["variants"] if v.get("vid")]
            fr = cj.freight(vids[:1]) if vids else None
        except Stop as e:
            log(f"  skip {r['pid']}: {e}")
            continue
        if fr and fr.get("days", 99) > 12:
            continue   # stock/shipping guard: US delivery within 12 days
        landed = round((r.get("price") or 0) + (fr["price"] if fr else SHIP_GUESS), 2)
        d = pricing.decide(landed, g["category"], ads.get(t) or next(iter(ads.values())), gate.price_rules(store))
        pv = verdict.product(d)
        if pv["badge"] != "pass":
            continue   # fails the floor or the 15% stress case: never listed
        priced.append({"id": r["pid"], "pid": r["pid"], "name": re.sub(r"\s+", " ", r["name"])[:70], "type": t, "image": r.get("image"),
                       "cost": r.get("price"), "ship": fr["price"] if fr else SHIP_GUESS, "ship_days": fr["days"] if fr else None,
                       "landed": landed, "price": d["price"], "compare_at": d.get("compare_at"), "keep": d["net_after_code"],
                       "keep_pct": d["net_pct_after_code"], "stress_pct": d.get("stress_pct_after_code"), "aim": pv["aim"], "cj_stores": r["stores"], "from": r["from"]})
    log(f"  {len(priced)} clear the floor and the stress case with US delivery in 12 days or less")
    by_type: dict[str, list] = {}
    for x in priced:
        by_type.setdefault(x["type"], []).append(x)
    shelf, i = [], 0
    while len(shelf) < SHELF and any(by_type.values()):
        t = list(by_type)[i % len(by_type)]
        if by_type[t]:
            shelf.append(by_type[t].pop(0))
        i += 1
    reserve = [x for v in by_type.values() for x in v]
    div = diversity(shelf, len(types))
    out = {"at": now(), "niche": g["niche"], "items": shelf, "reserve": reserve, "diversity": div,
           "checks": {"count": len(shelf) >= SHELF, "diversity": div["score"] >= 70}}
    store.save("shelf50.json", out)
    log(f"\nshelf {len(shelf)}/{SHELF}, reserve {len(reserve)}, diversity {div['score']} {div['types']} {div['bands']}")
    log(ai.usage_line())
    return out


def write_kit(store: Store, log: Log, kit_json: str) -> dict:
    """Solo step 3: the brand kit picked on the page -> brand.json in the shape clone/setup read.
    kit_json: {"name", "domain", "tagline", "palette": {"text","background","card","accent","muted"}, "fonts": {"heading","body"}, "icon"}"""
    import json as _json
    k = _json.loads(kit_json)
    if not k.get("name"):
        raise Stop("the kit needs a name")
    brand = store.load("brand.json") or {}
    brand.update(name=k["name"], domain=k.get("domain") or "", picked=now(),
                 kit={"tagline": k.get("tagline", ""), "palette": k.get("palette") or {}, "fonts": k.get("fonts") or {}, "icon": bool(k.get("icon"))})
    store.save("brand.json", brand)
    store.settings["name"] = k["name"]
    store.save_settings()
    log(f"brand kit saved: {k['name']} {k.get('domain') or ''} fonts {k.get('fonts')} palette {k.get('palette')}")
    return brand


def to_catalog(store: Store, log: Log, n: int = 10) -> list[str]:
    """Solo step 8 part 2: the next n kept shelf products go through the proven catalog vetting (photos checked by
    vision, colour variant, stock, per-product market prices) into shelf.json as READY, so the listing pipeline can
    build them. Returns the keys made READY."""
    from . import catalog as cat
    from .scout import cj_client as _cj
    sh = store.load("shelf50.json")
    if not sh:
        raise Stop("draft the shelf first (gw.py shelf)")
    profile = store.load("profile.json")
    if not profile:
        raise Stop("no niche profile yet (gw.py profile)")
    drop = set(sh.get("dropped") or [])
    shelf = store.load("shelf.json") or {"items": {}, "created": now()}
    done = {x.get("pid") for x in shelf["items"].values()}
    todo = [x for x in sh["items"] + sh.get("reserve", []) if x["pid"] not in drop and x["pid"] not in done]
    cj = _cj(store, log)
    gender = (store.get("genders") or ["unisex"])[0]
    made: list[str] = []
    for x in todo:
        if len(made) >= n:
            break
        k = cat._key(x["pid"], gender)
        rec = {"key": k, "pid": x["pid"], "gender": gender, "name": x["name"], "found_kw": x.get("from"), "status": "FOUND", "at": now()}
        shelf["items"][k] = rec
        try:
            cat._vet_and_price(store, cj, profile, rec, log)
        except Exception as e:  # noqa: BLE001
            rec.update(status="RETRY", why=f"error, will retry: {str(e)[:200]}")
        log(f"  {rec['status']:7} {x['name'][:60]:60} " + (f"${rec.get('price')}" if rec["status"] == "READY" else rec.get("why", "")))
        if rec["status"] == "READY":
            made.append(k)
        store.save("shelf.json", shelf)
    log(f"{len(made)} READY for listing")
    return made

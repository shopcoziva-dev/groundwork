"""Stage 3, Catalog: fill the shelf with real supplier products that pass every guard and price above the floor.

Per approved corner and gender: search CJ with the corner's keywords -> drop blocklisted/vetoed/out-of-band items ->
rank by how many stores already sell it (listedNum) -> Claude vets each candidate from its photos against the niche
profile (right type, no lettering or brand, suits the buyer, launch colour, 2 shopper searches) -> cheapest US shipping
within the limit, stock check -> competitor ad prices (lib/market) -> the pricing formula. PRICE = on the shelf (READY),
PARK/REVIEW/failed guard = kept with the reason so it is never re-checked.
Writes stores/<slug>/shelf.json. Resumable: run it again to keep filling toward catalog_target.
"""
from __future__ import annotations

import re

from ..lib import ai, engine, market, pricing, rulebook, verdict
from ..lib.cj import CJ
from ..lib.core import Log, Stop, Store, now
from .listing import gender_keep
from .profile import type_for

VET = """You vet one supplier product for a US online store in this niche: {niche}. Buyer: {gender}.
The store's product types (pick one key): {types}
Look at the listing text, variant names and photos. Answer JSON:
{{"product_type": one of the type keys above, or "none" if it is not one of them,
 "fits_store": true if a shopper of this store would expect it here,
 "lettering": true if the PRODUCT ITSELF shows words, slogans, big logos or brand names (tiny icons are fine),
 "lettering_what": "...",
 "branded": true if it is a known brand's product (brand on product, packaging or photos),
 "suits_buyer": true if a typical US {gender} shopper would buy it for themselves,
 "colour": ONE colour that exists in the variant names, picked as this buyer would ({colours} first; never loud colours when a neutral exists),
 "colour_plain": that colour as a plain English word for a title,
 "variant_match": a short substring present in EVERY variant name of the chosen colour (used to select those variants; "" if one variant),
 "shopping_queries": [2 different Google Shopping searches a US shopper would type to find THIS product],
 "problems": [ONLY hard blockers: looks cheap/flimsy/damaged, unsafe, photos misrepresent what ships, not really the product type]}}
When unsure about lettering or branding, say true."""


def _key(pid: str, gender: str) -> str:
    return f"{pid}-{gender}"


def _blocked(name: str, profile: dict) -> list[str]:
    s = profile.get("sourcing") or {}
    low = name.lower()
    return [w for w in (s.get("blocklist") or []) + (s.get("vetoes") or []) if w and re.search(rf"\b{re.escape(w.lower())}\b", low)]


def _gender_ok(name: str, gender: str) -> bool:
    low = name.lower()
    has_w = bool(re.search(r"\b(women'?s?|ladies|female|girls?)\b", low))
    has_m = bool(re.search(r"(?<!wo)\b(men'?s?|male|boys?)\b", low))
    if gender == "men":
        return not has_w or has_m
    if gender == "women":
        return not has_m or has_w
    return True


_SKIP = {"with", "and", "for", "the", "set", "pack", "new", "sale", "gift", "of", "in", "by", "a", "an", "to", "pcs", "piece", "pieces", "inch", "in."}


def reference_keywords(store, n: int = 5) -> list[str]:
    """Short CJ search words from the picked reference store's best sellers ('Brass Arc Floor Lamp with Marble Base' -> 'arc floor lamp')."""
    ref = str(store.get("reference_url") or "")
    ms = store.load("model_stores.json") or {}
    if not ref or not ms:
        return []
    from .stores import domain_of
    row = next((r for r in ms.get("stores", []) if r.get("domain") == domain_of(ref)), None)
    out: list[str] = []
    brand = set(re.findall(r"[a-z]+", str((row or {}).get("name") or "").lower()))
    for b in (row or {}).get("best_sellers") or []:
        head = re.split(r"\b(?:with|for|in|by|-|–|\|)\b", str(b.get("title") or "").lower())[0]
        words = [w for w in re.findall(r"[a-z]+", head) if w not in _SKIP and w not in brand and len(w) > 2]
        kw = " ".join(words[-3:])
        if kw and kw not in out:
            out.append(kw)
        if len(out) >= n:
            break
    return out


def run(store: Store, log: Log, limit: int | None = None, per_keyword: int = 12) -> dict:
    profile, scout = store.load("profile.json"), store.load("scout.json")
    if not profile or not scout:
        raise Stop("run scout and profile first")
    shelf = store.load("shelf.json") or {"items": {}, "created": now()}
    target = int(limit or store.get("catalog_target") or 50)
    genders = profile.get("genders") or ["unisex"]
    src = profile.get("sourcing") or {}
    band = src.get("price_band") or [3, 60]
    per_type = int(src.get("max_per_type") or 3)
    cj = CJ(store, log)
    corners = [c for c in scout["corners"] if c["name"] in (scout.get("approved") or [])]
    seller_kw = reference_keywords(store)   # what the reference store actually sells most of goes first
    if seller_kw:
        log(f"reference best sellers -> CJ searches first: {', '.join(seller_kw)}")
    ready = lambda: [x for x in shelf["items"].values() if x["status"] == "READY"]   # noqa: E731
    log(f"# Catalog: target {target} READY | genders {genders} | types {list(profile['types'])} | {len(ready())} already READY")

    def save():
        shelf["updated"] = now()
        store.save("shelf.json", shelf)

    for corner in corners:
        for gender in genders:
            g_target = target // len(genders) if len(genders) > 1 else target
            kws = list(corner.get("cj_keywords") or [corner["search"]])
            if corner is corners[0]:
                kws = seller_kw + [k for k in kws if k not in seller_kw]
            for kw in kws:
                if len([x for x in ready() if x["gender"] == gender]) >= g_target:
                    break
                found = cj.search(kw, size=40, min_price=band[0], max_price=band[1])
                log(f"\n'{kw}' ({gender}): {len(found)} on CJ")
                tried = 0
                for p in found:
                    if tried >= per_keyword or len([x for x in ready() if x["gender"] == gender]) >= g_target:
                        break
                    k = _key(p["pid"], gender)
                    if k in shelf["items"] and shelf["items"][k]["status"] != "RETRY":
                        continue
                    rec = {"key": k, "pid": p["pid"], "gender": gender, "name": p["name"], "stores": p["stores"], "corner": corner["name"],
                           "found_kw": kw, "status": "FOUND", "at": now(), "tries": (shelf["items"].get(k) or {}).get("tries", 0)}
                    shelf["items"][k] = rec
                    bad = _blocked(p["name"], profile)
                    if bad or not _gender_ok(p["name"], gender):
                        rec.update(status="PARKED", why=f"blocked words {bad}" if bad else f"named for the other gender")
                        continue
                    tried += 1
                    try:
                        _vet_and_price(store, cj, profile, rec, log)
                    except Exception as e:  # noqa: BLE001 - a passing outage (CJ limit, API 529) must not park a good product forever
                        rec["tries"] = rec.get("tries", 0) + 1
                        if rec["tries"] >= 3:
                            rec.update(status="PARKED", why=f"error 3 times: {str(e)[:200]}")
                        else:
                            rec.update(status="RETRY", why=f"error, will retry: {str(e)[:200]}")
                    same = [x for x in ready() if x["gender"] == gender and x.get("type") == rec.get("type") and x["key"] != k]
                    if rec["status"] == "READY" and len(same) >= per_type:
                        rec.update(status="PARKED", why=f"type full: {per_type} {gender} {rec.get('type')} already")
                    log(f"  {rec['status']:7} {p['name'][:60]:60} " + (f"${rec.get('price')} (landed ${rec.get('landed')})" if rec["status"] == "READY" else rec.get("why", "")))
                    save()
    save()
    r = ready()
    log(f"\nshelf: {len(r)} READY / {len(shelf['items'])} checked | " + ", ".join(f"{g}: {sum(1 for x in r if x['gender'] == g)}" for g in genders))
    log(ai.usage_line())
    return shelf


def _gate_market(store: Store, tkey: str) -> dict:
    """Beta: the ad prices step 1 already read for this product type (no extra Claude calls per product)."""
    mk = (store.load("gate.json") or {}).get("market") or {}
    m = mk.get(tkey) or next((v for k, v in mk.items() if k.lower().strip() == str(tkey).lower().strip()), None) or next(iter(mk.values()), {})
    return {"niches": {q: [[r[0], r[1]] for r in rows] for q, rows in (m.get("searches") or {}).items()}, "source": "step 1", "collected": now()}


def _vet_and_price(store: Store, cj: CJ, profile: dict, rec: dict, log: Log) -> None:
    info = cj.product(rec["pid"])
    if not info["variants"]:
        rec.update(status="PARKED", why="no US variants")
        return
    pref = (profile.get("colour_preference") or {}).get(rec["gender"]) or (profile.get("colour_preference") or {}).get("unisex") or []
    sys_ = VET.format(niche=profile["niche"], gender=("unisex" if rec["gender"] == "unisex" else rec["gender"] + "'s"),
                      types=", ".join(f"{k} ({', '.join(t.get('match', [])[:4])})" for k, t in profile["types"].items()), colours=", ".join(pref[:5]) or "neutrals")
    if engine.rules():   # Beta: word rules (type, brand list, colour order) instead of a photo check
        v = rulebook.vet(info, profile, rec["gender"], type_for)
    else:
        content = [{"type": "text", "text": f"Name: {info['name']}\nVariants: {[v['name'] for v in info['variants']][:40]}\nText: {info['desc'][:1500]}"}] + ai.photos(info["images"], 6)
        v = ai.ask_json("vet", sys_, content, max_tokens=1200,
                        check=lambda a: [] if isinstance(a, dict) and "product_type" in a and isinstance(a.get("shopping_queries"), list) else ["missing fields"])
    rec["vet"] = v
    why = []
    tkey, _t = type_for(profile, v.get("product_type"), info["name"]) if str(v.get("product_type") or "none").lower() != "none" else (None, None)
    if not tkey:
        why.append(f"not a store product type ({v.get('product_type')})")
    if not v.get("fits_store"):
        why.append("does not fit the store")
    if v.get("lettering"):
        why.append(f"lettering on product: {v.get('lettering_what')}")
    if v.get("branded"):
        why.append("branded product")
    if not v.get("suits_buyer"):
        why.append(f"does not suit {rec['gender']}")
    why += [f"problem: {x}" for x in v.get("problems") or []]
    why += [f"blocked word: {b}" for b in _blocked(info["name"] + " " + str(v.get("product_type")), profile)]
    if why:
        rec.update(status="PARKED", why="; ".join(why))
        return
    m = str(v.get("variant_match") or v.get("colour") or "").lower()
    pool = gender_keep(info["variants"], rec["gender"])
    chosen = [x for x in pool if m and m in x["name"].lower()] or ([pool[0]] if len(pool) == 1 else [])
    if not chosen:
        rec.update(status="PARKED", why=f"no variant matches colour '{v.get('colour')}'")
        return
    costs = [x["cost"] for x in chosen if x["cost"] is not None]
    if not costs:
        rec.update(status="PARKED", why="no supplier cost")
        return
    src = profile.get("sourcing") or {}
    fr = cj.freight([x["vid"] for x in chosen], int(src.get("max_ship_days") or 12))
    if not fr or fr["days"] > int(src.get("max_ship_days") or 12) + 2:
        rec.update(status="PARKED", why=f"no US shipping within {src.get('max_ship_days', 12)} days")
        return
    stock = cj.stock([x["vid"] for x in chosen])
    if stock is not None and stock < int(src.get("min_stock") or 100):
        rec.update(status="PARKED", why=f"stock {stock} < {src.get('min_stock', 100)}")
        return
    landed = round(max(costs) + fr["price"], 2)
    ads = _gate_market(store, tkey) if engine.rules() else market.collect(store, rec["key"], v.get("shopping_queries") or [], log)
    t = profile["types"][tkey]
    rules = {**(profile.get("pricing") or {}), "discount_code_pct": float(store.get("discount_code_pct") or 0),   # the store's real standing code
             "compare_at_offset": 0.0 if store.get("compare_at") is False else 30.0}   # Solo: real prices unless the "was" price is switched on
    d = pricing.decide(landed, t["category"], ads, rules)
    rec.update(type=tkey, category=t["category"], colour=v.get("colour"), colour_plain=v.get("colour_plain") or v.get("colour"),
               variant_match=v.get("variant_match") or "", vids=[x["vid"] for x in chosen], landed=landed, ship=fr, stock=stock,
               queries=v.get("shopping_queries"), market_source=ads.get("source"), pricing=d)
    if d["decision"] != "PRICE":
        rec.update(status="PARKED" if d["decision"] == "PARK" else "REVIEW", why=d.get("reason"))
        return
    if store.get("solo") and (pv := verdict.product(d))["badge"] != "pass":
        rec.update(status="PARKED", why=pv["why"])   # Solo product verdict: fails the 15% stress case -> never listed
        return
    rec.update(status="READY", price=d["price"], compare_at=d.get("compare_at"), why="")

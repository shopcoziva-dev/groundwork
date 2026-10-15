"""Stage 2, Niche profile: Claude writes the rules this niche's store runs on; code checks them.

This is what makes the pipeline adapt to any niche. Everything Coziva hard-codes for heated apparel (photo framing per
product type, poses, hero scenes, product-alone layout, the feature shot, physical rules, colour preference, copy voice,
collections, sourcing guards) is written here once per niche, from the approved Scout corners, with Coziva's own rules
(profiles/heated-apparel.example.json) as the worked example. Every later stage reads profile.json.
Writes stores/<slug>/profile.json. Re-run any time; edit the file by hand to correct a rule (the pipeline obeys the file).
"""
from __future__ import annotations

import json

from ..lib import ai, engine, rulebook
from ..lib.core import GW, Log, Stop, Store, now

EXAMPLE = json.loads((GW / "profiles/heated-apparel.example.json").read_text())
FEATURE_KINDS = ("none", "effect", "function", "in_use", "scale")

SYSTEM = """You write the NICHE PROFILE for an automated US dropshipping store: the rules every product photo, listing and
collection in this store will follow. The AI photo generator obeys these rules literally, so be concrete and physical
(what is in frame, how many hands/feet/items, what touches what). Answer with ONE JSON object in exactly the shape of the
example profile you are given (same keys; drop "_about"). Rules for each part:
- types: one entry per product type the store will list (cover every product in the approved corners; 4-12 types).
  Key = short lowercase type name. match = words that appear in supplier product names of this type.
  category = apparel | home | other. collection = the storefront collection it goes in.
  people = true if a model shows it best (worn / held / used), false for products shown alone (most home goods, gadgets).
  When people = true: framing (studio shot crop, everything that must be in frame), face (true/false), 2-3 poses,
  outfit_hint, hero_scene + hero_moment (a quiet candid moment of real use, not a full activity).
  When people = false: framing and poses may be "" / []; hero_scene + hero_moment describe a real lifestyle setting
  with the product in use or in place (no person needed, a hand is fine).
  alone = how the product-only shot presents it (layout, angle, what is included in the box).
  feature = the 5th photo: {"kind": "none"} or
    {"kind": "function", "describe": "what the shot shows working, e.g. the lid open showing the compartments"} or
    {"kind": "in_use", "describe": "..."} or {"kind": "scale", "describe": "the product next to a common object for size"} or
    {"kind": "effect", "describe": "...", "effect": "a visible effect the product really makes (light, mist, heat glow) and where it shows"}.
  rules = hard physical rules the photo checker will count (e.g. "exactly one lamp, the cable runs to the wall plug").
  corded = true only if the product really works plugged in (then cables may show).
- photo_rules: 3-6 rules for every photo in this niche (what must never appear, how products are shown).
- colour_preference: what each buyer group picks first (only real, sellable colours, neutrals before loud ones).
- copy: short_title_pattern with 2 examples, must/never lists, tags_base (2-3 words), faq_topics (4).
- sourcing: price_band [min, max] supplier cost in USD, max_ship_days 12, min_stock 100, max_per_type 2-4, blocklist (big
  brands and trademarks to avoid in this niche), vetoes (words that mean the wrong product or a risky one).
- collections: one per type collection: {"title", "tag": "type-<type key>"}.
- genders: ["women","men"] if the products are sold in men's and women's versions, else ["unisex"].
Never invent product facts; this profile is about HOW to show and sell, not specs."""


def _check(p) -> list[str]:
    e: list[str] = []
    if not isinstance(p, dict):
        return ["not a JSON object"]
    for k in ("niche", "voice", "genders", "colour_preference", "copy", "photo_rules", "types", "sourcing", "collections"):
        if k not in p:
            e.append(f"missing {k}")
    types = p.get("types") or {}
    if not 1 <= len(types) <= 16:
        e.append("types: need 1-16 product types")
    for name, t in types.items():
        for k in ("match", "category", "collection", "people", "alone", "feature", "hero_scene"):
            if t.get(k) in (None, "", []) and k != "people":
                e.append(f"type {name}: missing {k}")
        if t.get("category") not in ("apparel", "home", "other"):
            e.append(f"type {name}: category must be apparel|home|other")
        if t.get("people") and not t.get("framing"):
            e.append(f"type {name}: people=true needs framing")
        if t.get("people") and len(t.get("poses") or []) < 2:
            e.append(f"type {name}: people=true needs 2+ poses")
        f = t.get("feature") or {}
        if f.get("kind") not in FEATURE_KINDS:
            e.append(f"type {name}: feature.kind must be one of {FEATURE_KINDS}")
        elif f["kind"] != "none" and not f.get("describe"):
            e.append(f"type {name}: feature needs describe")
        elif f["kind"] == "effect" and not f.get("effect"):
            e.append(f"type {name}: effect feature needs effect")
    g = p.get("genders") or []
    if not g or any(x not in ("women", "men", "unisex") for x in g):
        e.append("genders must be women/men or unisex")
    band = (p.get("sourcing") or {}).get("price_band") or []
    if len(band) != 2 or not (0 < float(band[0]) < float(band[1])):
        e.append("sourcing.price_band must be [min, max]")
    return e


def validate(p: dict) -> list[str]:
    return _check(p)


def run(store: Store, log: Log) -> dict:
    if engine.rules():   # Beta: the profile comes from the gate (types, category, split) and the copy pattern
        g = store.load("gate.json")
        if not g or not g.get("types"):
            raise Stop("pass step 1 (direction) first")
        p = rulebook.profile(g, store.load("copy.json"))
        errs = _check(p)
        if errs:
            raise Stop("profile rules: " + "; ".join(errs))
        p["_written"] = now()
        store.save("profile.json", p)
        if p["genders"] != store.get("genders"):
            store.settings["genders"] = p["genders"]
            store.save_settings()
        log(f"profile (rules): {len(p['types'])} product types ({', '.join(p['types'])}), genders {p['genders']}, product-only photos")
        return p
    scout = store.load("scout.json")
    g = store.load("gate.json") or {}
    if not scout and g.get("niche"):   # Solo v4: the gate is the direction; one corner = the gated niche and its types
        scout = {"niche": g["niche"], "approved": [g["niche"]], "corners": [{"name": g["niche"], "why": "passed the gate", "category": g.get("category"),
                 "products": g.get("types") or [g["niche"]], "gendered": False, "typical_price": 0, "season_note": ""}]}
        store.save("scout.json", scout)
    if not scout:
        raise Stop("run scout first")
    approved = scout.get("approved") or []
    if not approved:
        raise Stop("no Scout corners approved yet (gw.py approve --store <slug> --corners ...)")
    corners = [c for c in scout["corners"] if c["name"] in approved]
    log(f"# Niche profile: {store.get('niche')} | corners: {', '.join(approved)}")
    brief = {"niche": scout["niche"], "corners": [{k: c.get(k) for k in ("name", "why", "category", "products", "gendered", "typical_price", "season_note")} for c in corners]}
    ms = (store.load("model_stores.json") or {}).get("stores") or []
    if ms:   # what the best specialist stores in this niche actually sell (their product types, prices) shapes our types
        brief["model_stores"] = [{k: r.get(k) for k in ("name", "domain", "sells", "top_types", "median_price", "why")} for r in ms[:6]]
    ex = {k: v for k, v in EXAMPLE.items() if k != "_about"}
    p = ai.ask_json("profile", SYSTEM, [{"type": "text", "text": "Worked example (a different niche, written by the owner over many runs; match its quality and concreteness):\n"
                                         + json.dumps(ex, indent=1) + "\n\nWrite the profile for this store:\n" + json.dumps(brief, indent=1)}],
                    check=_check, max_tokens=9000)
    p["_written"] = now()
    p["_corners"] = approved
    store.save("profile.json", p)
    log(f"profile: {len(p['types'])} product types ({', '.join(p['types'])}), genders {p['genders']}")
    for name, t in p["types"].items():
        log(f"  {name:16} {t['category']:8} {'model' if t.get('people') else 'product only':12} feature={t['feature']['kind']:8} -> {t['collection']}")
    if p["genders"] != store.get("genders"):
        store.settings["genders"] = p["genders"]
        store.save_settings()
    log(ai.usage_line())
    return p


def type_for(profile: dict, product_type: str = "", name: str = "") -> tuple[str, dict] | tuple[None, None]:
    """The profile type for a product: exact key first, then match words in the type, then in the product name."""
    types = profile.get("types") or {}
    pt = str(product_type or "").lower().strip()
    if pt in types:
        return pt, types[pt]
    hay = f"{pt} {str(name).lower()}"
    best = None
    for k, t in types.items():
        hits = [w for w in t.get("match") or [] if w.lower() in hay]
        if hits and (best is None or max(map(len, hits)) > best[0]):
            best = (max(map(len, hits)), k, t)
    return (best[1], best[2]) if best else (None, None)

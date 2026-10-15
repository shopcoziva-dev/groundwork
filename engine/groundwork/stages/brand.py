"""Brand: naming direction -> names in that direction -> pick -> domain links -> brand kit.

Claude proposes 12 names for the niche; code checks each .com in the public domain registry (RDAP: "not found" = free to
register); Claude then web-searches the top free names for trademark or store conflicts and writes the kit (tagline,
colours, fonts, about text) for the best one. Jason picks the name (gw.py brand --pick NAME), or --auto takes the top.
Writes stores/<slug>/brand.json. Buying the domain stays with Jason (it costs money).
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from ..lib import ai
from ..lib.core import Log, Stop, Store, now

# Naming DIRECTIONS: a name is picked in two steps. First the direction (what kind of name), with Claude's
# recommendation for this niche; then names inside the chosen direction. Then the domain links for the picked name.
DIRECTION_KINDS = {
    "coined":      "an invented word built from a feeling + a soft ending (Coziva = cozy + -iva; Glowra, Nestly)",
    "descriptive": "says what you sell in two plain words (SunsetGlow, DuskLamps); instantly clear, easy for ads, less ownable",
    "evocative":   "one real word for the feeling or moment, not the product (Dusk, Hearth, Ember)",
    "compound":    "two short real words joined into a brand (GlowNest, WarmHaus, TrailFire)",
    "persona":     "a name that sounds like a person or place (Nora & Pine, Hollis Lane); warm, boutique, trust-heavy",
    "playful":     "rhythm or rhyme, fun to say (Snuggo, GlowGo, Toasty Tees)",
}

DIRECTIONS = """You are naming a new US online store. Niche: {niche}. Voice: {voice}. Buyers: {audience}.
Model stores that win in this niche: {models}.
For each naming direction below, say how it would work for THIS niche, then recommend ONE. Answer JSON:
{{"directions": [{{"key": one of {keys}, "fit": "how this direction would sound for this niche, with 2 quick examples",
   "pros": "one line", "cons": "one line"}} ... one per direction],
 "recommend": the key you would pick, "why": "2 sentences: why this direction sells best here (ads, trust, memorability, .com odds)"}}
Directions: {kinds}"""

NAMES = """You name a new US online store. Niche: {niche}. Voice: {voice}.
Naming direction (stay strictly inside it): {direction} = {what}.
Answer JSON: {{"names": [{{"name": "...", "why": "one short line"}} x 12]}}
Rules: easy to say and spell after hearing it once, sounds like a real brand, no hyphens or numbers, not a real company
or trademark you know of, likely .com-able (we check every one)."""


# Brand STYLE families. Claude picks the family that fits the niche; CODE picks the fonts from that family, so a
# skateboarding store can never end up with a script font (Shopify font library names).
STYLES = {
    "street":      {"for": "skate, streetwear, sneakers, edgy youth gear", "heading": ["Archivo Black", "Bebas Neue", "Anton"], "body": ["Archivo", "Work Sans"]},
    "rugged":      {"for": "outdoor, work, hunting, tools, automotive, heated gear", "heading": ["Oswald", "Barlow Condensed", "Roboto Slab"], "body": ["Barlow", "Roboto"]},
    "tech":        {"for": "gadgets, electronics, smart home, accessories", "heading": ["Space Grotesk", "Manrope", "Inter"], "body": ["Inter", "Manrope"]},
    "cozy":        {"for": "home comfort, blankets, candles, slippers, lighting with a warm mood", "heading": ["Fraunces", "Recoleta", "DM Serif Display"], "body": ["Nunito Sans", "Lato"]},
    "playful":     {"for": "kids, toys, pets, novelty, crafts", "heading": ["Fredoka", "Baloo 2", "Rubik"], "body": ["Nunito", "Rubik"]},
    "luxe":        {"for": "jewelry, beauty, fashion accessories, premium decor", "heading": ["Playfair Display", "Cormorant Garamond", "Bodoni Moda"], "body": ["Montserrat", "Lato"]},
    "natural":     {"for": "wellness, organic, garden, eco, yoga", "heading": ["Lora", "Cormorant", "DM Serif Text"], "body": ["Karla", "Source Sans Pro"]},
    "performance": {"for": "fitness, sports, cycling, running, supplements-adjacent gear", "heading": ["Montserrat", "Barlow Condensed", "Oswald"], "body": ["Barlow", "Montserrat"]},
}

KIT = """You make the brand kit for a new US online store named "{name}". Answer JSON:
{{"style": one of {styles} (pick what shoppers of THIS niche expect; the model stores below show the look that wins),
 "style_why": "one line",
 "tagline": "<= 6 words",
 "about": "2 short paragraphs for the About page, honest (no fake founder story, no years in business), what the store sells and why",
 "palette": {{"primary": "#hex", "accent": "#hex", "background": "#hex", "text": "#hex"}} (fits the style; text on background must be easy to read),
 "conflicts": ["any trademark, store or brand with the same or confusingly similar name you found; [] if none"],
 "verdict": "ok" | "risky"}}
Styles: {style_list}
Search the web for "{name}" (trademarks, stores, brands, social handles) before answering."""

TLDS = ["{n}.com", "{n}.co", "{n}.shop", "{n}.store", "get{n}.com", "shop{n}.com", "{n}store.com", "try{n}.com", "the{n}.com"]


def _contrast(a: str, b: str) -> float:
    def lum(h):
        h = h.lstrip("#")
        if len(h) != 6:
            return 0.5
        c = [int(h[k:k + 2], 16) / 255 for k in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    l1, l2 = sorted((lum(a), lum(b)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _kit_check(a) -> list[str]:
    e = []
    if not isinstance(a, dict):
        return ["not an object"]
    if a.get("style") not in STYLES:
        e.append(f"style must be one of {list(STYLES)}")
    pal = a.get("palette") or {}
    if not all(str(pal.get(k, "")).startswith("#") for k in ("primary", "accent", "background", "text")):
        e.append("palette needs primary, accent, background, text as #hex")
    elif _contrast(pal["text"], pal["background"]) < 4.5:
        e.append(f"text on background contrast {_contrast(pal['text'], pal['background']):.1f} < 4.5 (hard to read)")
    if not a.get("tagline"):
        e.append("need a tagline")
    return e


def domain_options(name: str) -> list[dict]:
    """Every domain worth buying for the name, checked in the public registries, each with a buy link."""
    n = re.sub(r"[^a-z0-9]", "", name.lower())
    out = []
    for t in TLDS:
        d = t.format(n=n)
        out.append({"domain": d, "available": dom_free(d),
                    "buy_godaddy": f"https://www.godaddy.com/domainsearch/find?domainToCheck={d}",
                    "buy_shopify": "Shopify admin of the NEW store > Settings > Domains > Buy new domain (so it attaches to that store)"})
    out.sort(key=lambda x: {True: 0, None: 1, False: 2}[x["available"]])
    return out


def dom_free(dom: str) -> bool | None:
    """True = no registration found (available to buy), False = taken, None = could not check. Public RDAP registries."""
    url = f"https://rdap.verisign.com/com/v1/domain/{dom}" if dom.endswith(".com") else f"https://rdap.org/domain/{dom}"
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/rdap+json"}), timeout=20)
        return False
    except urllib.error.HTTPError as e:
        return True if e.code == 404 else None
    except Exception:  # noqa: BLE001
        return None


def com_free(name: str) -> bool | None:
    return dom_free(re.sub(r"[^a-z0-9]", "", name.lower()) + ".com")


def _models(store: Store) -> str:
    return "; ".join(f"{r.get('name')} ({r.get('domain')})" for r in ((store.load("model_stores.json") or {}).get("stores") or [])[:6]) or "n/a"


def directions(store: Store, log: Log, profile: dict, brand: dict) -> dict:
    if not brand.get("directions"):
        keys = list(DIRECTION_KINDS)
        ans = ai.ask_json("brand_directions", DIRECTIONS.format(niche=profile["niche"], voice=profile.get("voice", ""), audience=profile.get("audience", ""),
                                                                 models=_models(store), keys=json.dumps(keys), kinds="; ".join(f"{k} = {v}" for k, v in DIRECTION_KINDS.items())),
                          "Go.", max_tokens=2000,
                          check=lambda a: [] if isinstance(a, dict) and a.get("recommend") in DIRECTION_KINDS and len(a.get("directions") or []) >= 4 else ["need the directions and a recommend key"])
        brand.update(directions=ans["directions"], recommend=ans["recommend"], recommend_why=ans.get("why", ""), names={}, at=now())
        store.save("brand.json", brand)
    log("# Naming directions")
    for d in brand["directions"]:
        star = "  <- recommended" if d["key"] == brand["recommend"] else ""
        log(f"  {d['key']:12} {DIRECTION_KINDS.get(d['key'], '')[:60]}{star}\n               for this niche: {d.get('fit', '')}")
    log(f"\nRecommend: {brand['recommend']}. {brand.get('recommend_why', '')}")
    return brand


def names_for(store: Store, log: Log, profile: dict, brand: dict, direction: str) -> list[dict]:
    if direction not in DIRECTION_KINDS:
        raise Stop(f"direction must be one of {list(DIRECTION_KINDS)}")
    have = (brand.get("names") or {}).get(direction)
    if not have:
        ans = ai.ask_json(f"brand_names_{direction}", NAMES.format(niche=profile["niche"], voice=profile.get("voice", ""), direction=direction, what=DIRECTION_KINDS[direction]),
                          "Go.", max_tokens=1500, check=lambda a: [] if len((a or {}).get("names") or []) >= 8 else ["need 12 names"])
        have = []
        for n in ans["names"]:
            nm = re.sub(r"[^A-Za-z &]", "", n["name"]).strip()
            have.append({"name": nm, "why": n.get("why", ""), "domain": re.sub(r"[^a-z0-9]", "", nm.lower()) + ".com", "com_free": com_free(nm)})
        have.sort(key=lambda c: {True: 0, None: 1, False: 2}[c["com_free"]])
        brand.setdefault("names", {})[direction] = have
        store.save("brand.json", brand)
    log(f"# {direction} names (.com available first):")
    for c in have:
        log(f"  {c['name']:18} {c['domain']:24} {'available' if c['com_free'] else 'taken' if c['com_free'] is False else '?':10} {c['why'][:60]}")
    return have


def run(store: Store, log: Log, pick: str | None = None, auto: bool = False, direction: str | None = None) -> dict:
    profile = store.load("profile.json")
    if not profile:
        raise Stop("run profile first")
    brand = store.load("brand.json") or {}
    brand = directions(store, log, profile, brand)
    direction = direction or (brand["recommend"] if (auto or pick) else None)
    if not direction:
        log("\nPick a direction: gw.py brand --store " + store.slug + " --direction KEY   (or --auto for the recommended one)")
        log(ai.usage_line())
        return brand
    cands = names_for(store, log, profile, brand, direction)
    brand["direction"] = direction
    if pick or auto:
        name = pick or next((c["name"] for c in cands if c["com_free"]), cands[0]["name"])
        ms = [f"{r.get('name')} ({r.get('domain')}): {r.get('why', '')}" for r in ((store.load("model_stores.json") or {}).get("stores") or [])[:5]]
        kit = ai.ask_json("brand_kit", KIT.format(name=name, styles=json.dumps(list(STYLES)), style_list="; ".join(f"{k} = {v['for']}" for k, v in STYLES.items())),
                          f"Niche: {profile['niche']}\nVoice: {profile.get('voice', '')}\nModel stores: {'; '.join(ms) or 'n/a'}", web=5, max_tokens=2000, check=_kit_check)
        st = STYLES[kit["style"]]
        kit["fonts"] = {"heading": st["heading"][0], "body": st["body"][0], "heading_options": st["heading"], "body_options": st["body"]}
        dom = re.sub(r"[^a-z0-9]", "", name.lower()) + ".com"
        brand.update(name=name, domain=dom, com_free=com_free(name), kit=kit, picked=now(), domains=domain_options(name))
        store.settings["name"] = name
        if not store.get("sku_prefix"):
            store.settings["sku_prefix"] = re.sub(r"[^A-Z]", "", name.upper())[:2] or "GW"
        store.save_settings()
        store.save("brand.json", brand)
        log(f"\nPICKED {name} | {dom} ({'free' if brand['com_free'] else 'CHECK: may be taken'}) | tagline: {kit.get('tagline')} | "
            f"conflicts: {kit.get('conflicts') or 'none found'} -> {kit.get('verdict')}")
        log(f"style: {kit['style']} ({kit.get('style_why', '')}) -> fonts {kit['fonts']['heading']} / {kit['fonts']['body']}")
        log("domains you can buy (buy inside the NEW store's Shopify admin so it attaches there, or on GoDaddy):")
        for d in brand["domains"]:
            log(f"  {d['domain']:24} {'available' if d['available'] else 'taken' if d['available'] is False else '?':10} {d['buy_godaddy']}")
        log(f"SKU prefix: {store.get('sku_prefix')} (first listing will be {store.get('sku_prefix')}001)")
    else:
        log(f"\nPick one: gw.py brand --store {store.slug} --direction {direction} --pick NAME   (or another --direction)")
    log(ai.usage_line())
    return brand

"""Groundwork Beta rulebook: the code-only version of every decision Solo asks Claude to make.

Same inputs and output shapes as the AI calls they replace, so the rest of the pipeline cannot tell the difference.
Results are more mechanical than Solo's (templates instead of writing, word rules instead of judgement) but they follow
the same rules, the same pricing formula and the same checks.
"""
from __future__ import annotations

import html
import re

from . import rules as house

# ---------------------------------------------------------------------------- words
BLOCK = {
    "food, drink or something swallowed": r"\b(food|snack|candy|chocolate|coffee beans?|tea leaves|drink|beverage|juice|supplements?|vitamins?|protein|gumm(y|ies)|capsules?|pills?|tablets? \(|cbd|thc|kratom|edible)\b",
    "medicine or medical device": r"\b(medicine|medical device|syringe|injectables?|injection|prescription|insulin|hearing aid|contact lens)\b",
    "weapons": r"\b(guns?|firearms?|rifles?|pistols?|ammo|ammunition|knives|switchblade|taser|stun gun|pepper spray|crossbow)\b",
    "vapes, tobacco or nicotine": r"\b(vapes?|vaping|e-?cig(arette)?s?|tobacco|nicotine|hookah|cigars?|cigarettes?)\b",
    "adult products": r"\b(adult toys?|sex|lingerie|erotic|fetish)\b",
}
ACCESSORY = {"battery", "batteries", "charger", "charging", "cable", "adapter", "replacement", "part", "parts", "spare", "accessory",
             "accessories", "cover", "case", "strap", "bag", "pouch", "sticker", "decal", "keychain", "toy", "doll", "pattern",
             "fabric", "remote", "controller", "bulb", "refill", "filter", "mount", "holder", "stand", "hook", "box"}
BRANDS = {"nike", "adidas", "puma", "apple", "iphone", "samsung", "disney", "marvel", "pokemon", "hello kitty", "gucci", "louis vuitton",
          "chanel", "the north face", "columbia", "patagonia", "yeti", "stanley", "dyson", "lego", "barbie", "nfl", "nba", "mlb", "ugg"}
COLOURS = ["black", "white", "gray", "grey", "navy", "beige", "brown", "khaki", "cream", "camel", "olive", "green", "blue", "red",
           "pink", "purple", "yellow", "orange", "silver", "gold", "coffee", "wine", "burgundy"]
PREFER = {"women": ["black", "beige", "cream", "camel", "gray", "grey", "navy", "white", "brown", "pink"],
          "men": ["black", "navy", "gray", "grey", "olive", "khaki", "brown", "blue", "green"],
          "unisex": ["black", "gray", "grey", "white", "navy", "beige", "brown"]}


def _root(w: str) -> str:
    w = w.lower()
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and not w.endswith("ss"):
            return w[: -len(suf)]
    return w


def words(t: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", str(t).lower()) if len(w) > 2]


def has_all(name: str, ws: list[str]) -> bool:
    roots = {_root(x) for x in re.findall(r"[a-z]+", name.lower())}
    return all(_root(w) in roots for w in ws)


def blocked(text: str) -> str:
    t = str(text).lower()
    for why, rx in BLOCK.items():
        if re.search(rx, t):
            return why
    return ""


def singular(t: str) -> str:
    return " ".join(_root(w) if w.lower().endswith("s") and not w.lower().endswith("ss") else w.lower() for w in str(t).split())


def title(t: str) -> str:
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in str(t).split())


# ---------------------------------------------------------------------------- step 1: the niche
def shape(niche: str, types: list[str], category: str = "") -> dict:
    """gate_shape without AI: the user names the store and its 3 product types (the lesson teaches how)."""
    types = [t.strip() for t in types if t and t.strip()][:3]
    cat = category if category in ("apparel", "home", "other") else "other"
    low = " ".join(types).lower()
    split = "women / men" if re.search(r"\bwom[ae]n'?s?\b", low) and re.search(r"(?<!wo)\bmen'?s?\b", low) else ""
    return {"niche": title(niche.strip()), "widened_from": "", "blocked": blocked(niche + " " + " ".join(types)), "category": cat,
            "split": split, "types": types, "cj_keywords": list(dict.fromkeys([singular(t) for t in types] + [niche.strip().lower()]))[:5],
            "season": "", "nearby": []}


def classify(types: list[str], rows: list[dict]) -> list[str]:
    """gate_classify without AI: the name must hold every word of one type, and no accessory word that type does not have."""
    out = []
    for r in rows:
        name = str(r.get("name") or "")
        nw = {_root(w) for w in words(name)}
        got = ""
        for t in types:
            tw = words(t)
            if not tw or not has_all(name, tw):
                continue
            extra = {_root(a) for a in ACCESSORY} - {_root(w) for w in tw}
            if nw & extra:
                continue
            got = t
            break
        out.append(got)
    return out


# ---------------------------------------------------------------------------- step 2: the reference store
SEC_MAP = [("assets_import", "other"), ("icon_with_text", "trust-bar"), ("feature_icon", "feature-cards"), ("feature-icon", "feature-cards"),
           ("bestseller", "product-carousel"), ("best_seller", "product-carousel"), ("best-seller", "product-carousel"), ("newarrival", "product-carousel"),
           ("new_arrival", "product-carousel"), ("new-arrival", "product-carousel"), ("category", "category-tiles"), ("entry_nav", "quick-links"),
           ("quick", "quick-links"), ("brands", "press-logos"), ("champion", "editorial"), ("explore", "editorial"), ("custom_content", "editorial"),
           ("story", "editorial"), ("about", "editorial"), ("comparison", "editorial"), ("guarantee", "trust-bar"), ("shipping", "trust-bar"),
           ("announcement", "announcement"), ("header", "header"), ("slideshow", "hero"), ("image-banner", "hero"), ("image_banner", "hero"),
           ("hero", "hero"), ("video", "editorial"), ("featured-collection", "product-carousel"), ("featured_collection", "product-carousel"),
           ("product-grid", "product-grid"), ("collection-list", "category-tiles"), ("collection_list", "category-tiles"),
           ("multicolumn", "feature-cards"), ("icon", "trust-bar"), ("trust", "trust-bar"), ("image-with-text", "editorial"),
           ("image_with_text", "editorial"), ("rich-text", "editorial"), ("rich_text", "editorial"), ("collage", "editorial"),
           ("multirow", "editorial"), ("testimonial", "reviews"), ("review", "reviews"), ("logo", "press-logos"), ("press", "press-logos"),
           ("newsletter", "newsletter"), ("email", "newsletter"), ("faq", "editorial"), ("footer", "footer")]
PROD_MAP = [("main-product", "title-price"), ("main_product", "title-price"), ("product-recommendations", "cross-sell"),
            ("related", "cross-sell"), ("review", "reviews"), ("faq", "faq"), ("collapsible", "tabs"), ("footer", "other"), ("header", "other")]


def _sec_type(sid: str, table) -> str:
    s = sid.lower()
    for k, v in table:
        if k in s:
            return v
    return "other"


def layout(home_html: str, secs: dict) -> dict:
    """rip_layout without AI: section ids (Shopify themes name every section) + the page's own brand name and menu."""
    m = re.search(r'<meta[^>]+property="og:site_name"[^>]+content="([^"]+)"', home_html, re.I) or re.search(r"<title>([^<|–\-]+)", home_html, re.I)
    brand = html.unescape(m.group(1)).strip() if m else ""
    home = [{"type": _sec_type(x, SEC_MAP), "what": x.replace("_", " ").replace("-", " ")[:40]} for x in secs.get("home") or []]
    home = [h for i, h in enumerate(home) if i == 0 or h["type"] != "other" or h["what"] != home[i - 1]["what"]]
    if not home:
        home = [{"type": t, "what": t} for t in ("announcement", "header", "hero", "product-grid", "feature-cards", "newsletter", "footer")]
    prod = [{"type": _sec_type(x, PROD_MAP), "what": x.replace("_", " ").replace("-", " ")[:40]} for x in secs.get("product") or []] or \
           [{"type": t, "what": t} for t in ("gallery", "title-price", "variant-picker", "add-to-cart", "description", "faq")]
    nav = []
    for a in re.findall(r'<a[^>]+href="/(?:collections|pages)/[^"#?]+"[^>]*>(.*?)</a>', home_html, re.I | re.S):
        t = re.sub(r"<[^>]+>", " ", a)
        t = html.unescape(re.sub(r"\s+", " ", t)).strip()
        if 2 < len(t) < 30 and t not in nav:
            nav.append(t)
        if len(nav) >= 7:
            break
    return {"brand": brand, "home": home, "product": prod, "collection": [{"type": "grid", "what": "products"}], "nav": nav,
            "feel": ["colours and fonts read from the store's own code", "section order copied from its homepage", "no fake reviews or press logos"]}


# ---------------------------------------------------------------------------- step 6: the copy pattern
FACTS = {"apparel": ["material", "sizes and fit", "warmth or weather use", "care and washing", "what is in the box", "colours"],
         "home": ["size and dimensions", "material", "power or setup", "where it fits", "care and cleaning", "what is in the box"],
         "other": ["size", "material", "how it works", "who it is for", "care", "what is in the box"]}


def copy_pattern(niche: str, types: list[str], category: str, split: str, params: str = "") -> dict:
    t0 = title(singular(types[0])) if types else "Product"
    who = "for women / for men" if split else "for [who it is for]"
    ex = f"{t0}, [Key Feature], [Second Feature], {'for Women' if split else 'for Everyday Use'}, Black"
    return {"title": f"[{t0} or other type], [feature shoppers search], [second feature], {who}, [colour, size]",
            "title_example": ex[:150], "site": f"[Type] in [colour], e.g. {t0} in Black",
            "desc": ["one line: what it is and who it is for", "then 4-6 facts from the supplier spec sheet"],
            "fact_types": FACTS.get(category, FACTS["other"]),
            "faq": ["How long does shipping take?", "What if it arrives damaged or defective?",
                    "How do I pick the right size?" if category == "apparel" else "How do I set it up and care for it?"],
            "voice": ["plain words a shopper can check", "numbers instead of adjectives", "no hype words"],
            "applied": [f"noted: {params}" if params else "no extra parameters"]}


# ---------------------------------------------------------------------------- the niche profile (photos, sourcing, collections)
def profile(g: dict, cp: dict | None = None) -> dict:
    cat = g.get("category") or "other"
    types = {}
    for t in g.get("types") or []:
        key = t.lower().strip()
        types[key] = {"match": list(dict.fromkeys(words(t) + [singular(t)])), "category": cat, "collection": title(t),
                      "people": False, "framing": "", "face": False, "poses": [], "outfit_hint": "",
                      "hero_scene": "the product in a real, tidy everyday setting where it is used, soft daylight",
                      "hero_moment": "the product in place, ready to use", "alone": "the product alone, whole, centred, nothing else in frame",
                      "feature": {"kind": "none"}, "rules": ["exactly one product unless the listing is a set", "no text, logos or watermarks"]}
    genders = ["women", "men"] if g.get("split") else ["unisex"]
    return {"niche": g.get("niche"), "voice": "plain, specific, no hype", "audience": "US shoppers who already buy this online",
            "genders": genders, "colour_preference": {k: v for k, v in PREFER.items() if k in genders or k == "unisex"},
            "copy": {"short_title_pattern": "[colour] [type], e.g. Black Heated Vest", "must": ["only facts from the supplier listing"],
                     "never": ["best", "#1", "premium", "ultimate"], "tags_base": words(g.get("niche") or "")[:3],
                     "faq_topics": (cp or {}).get("faq") or ["shipping", "returns", "size and fit", "care"]},
            "photo_rules": ["no text, logos or watermarks", "the product looks exactly like the supplier photo", "clean light, real setting"],
            "types": types,
            "sourcing": {"price_band": [2, 120], "max_ship_days": 12, "min_stock": 50, "max_per_type": 25, "blocklist": sorted(BRANDS), "vetoes": ["novelty", "prank"]},
            "collections": [{"title": title(t), "tag": f"type-{t.lower().strip()}"} for t in g.get("types") or []], "_engine": "rules"}


# ---------------------------------------------------------------------------- catalog: vet one supplier product
def pick_colour(variants: list[dict], gender: str) -> tuple[str, str]:
    names = [str(v.get("name") or "") for v in variants]
    for c in PREFER.get(gender) or PREFER["unisex"]:
        for n in names:
            if re.search(rf"\b{c}\b", n, re.I):
                return c, c
    for n in names:
        for c in COLOURS:
            if re.search(rf"\b{c}\b", n, re.I):
                return c, c
    return "", ""


def vet(info: dict, profile_: dict, gender: str, type_for) -> dict:
    name = str(info.get("name") or "")
    key, _t = type_for(profile_, "", name)
    low = name.lower() + " " + str(info.get("desc") or "")[:600].lower()
    brand = next((b for b in BRANDS if re.search(rf"\b{re.escape(b)}\b", low)), "")
    colour, plain = pick_colour(info.get("variants") or [], gender)
    q = [key or singular(name)[:40]] + ([f"{plain} {key}"] if plain and key else [])
    return {"product_type": key or "none", "fits_store": bool(key), "lettering": False, "lettering_what": "", "branded": bool(brand),
            "suits_buyer": True, "colour": colour, "colour_plain": plain, "variant_match": colour, "shopping_queries": q,
            "problems": ([f"looks like a {brand} product"] if brand else []), "_engine": "rules"}


# ---------------------------------------------------------------------------- listing: fact sheet + copy
def spec_lines(desc: str, n: int = 8) -> list[str]:
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", str(desc or ""))
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    out = []
    for line in re.split(r"[\n;]+", t):
        line = re.sub(r"\s+", " ", line).strip(" -•*:")
        if not (6 <= len(line) <= 120):
            continue
        if re.search(r"(?i)(cj|dropship|aliexpress|wholesale|whatsapp|http|www\.|email|note:|please|shipping|warehouse)", line):
            continue
        if house.slop_hits(line):
            continue
        if line.lower() not in (x.lower() for x in out):
            out.append(line[:1].upper() + line[1:])
        if len(out) >= n:
            break
    return out


def facts(info: dict, item: dict, type_key: str) -> dict:
    imgs = info.get("images") or []
    return {"type": type_key, "color": item.get("colour_plain") or item.get("colour") or "as shown", "product_desc": clean_name(info.get("name") or type_key),
            "units": f"one {singular(type_key)}", "scale": "true to the supplier photos", "facts": spec_lines(info.get("desc"), 6),
            "outfit": "", "expression": "calm neutral", "hero": {}, "ref_index": 0 if imgs else None,
            "detail_index": 1 if len(imgs) > 1 else None, "detail_subject": "the material and the main feature up close" if len(imgs) > 1 else None,
            "detail_box": None}


def clean_name(n: str) -> str:
    n = re.sub(r"(?i)\b(new|hot|sale|20\d\d|free shipping|dropshipping|wholesale|fashion|style)\b", "", str(n))
    return re.sub(r"\s+", " ", n).strip(" ,-/")[:90]


def copy(store_name: str, niche: str, type_key: str, gender: str, colour: str, info: dict, shots: list[str], used: list[str],
         ship_days: str, free_over, returns_days, faq: list[str]) -> dict:
    t = title(singular(type_key))
    col = title(colour) if colour else ""
    fx = spec_lines(info.get("desc"), 6)
    feats = [f for f in (re.sub(r"(?i)^(material|size|color|colour|features?)\s*", "", x) for x in fx) if 3 < len(f) < 40][:2]
    who = {"women": "for Women", "men": "for Men"}.get(gender, "")
    parts = [t] + feats + ([who] if who else []) + ([col] if col else [])
    ttl = ", ".join(p for p in parts if p)[:150].rstrip(", ")
    short = (f"{col} {t}".strip())[:40]
    n = 2
    while short in used:
        short = f"{col} {t} {n}".strip()[:40]
        n += 1
    pool = list(dict.fromkeys([w for w in words(type_key)] + ([colour.lower()] if colour else []) + ([gender] if gender != "unisex" else [])
                              + words(niche) + [singular(type_key), f"{singular(type_key)} gift", f"{niche.lower()} store"]
                              + [w for f in fx for w in words(f)][:20]))
    pool = [p for p in pool if p and len(p) < 40]
    k = 1
    while len(pool) < 13:
        pool.append(f"{singular(type_key)} {k}")
        k += 1
    tags = pool[:13]
    lis = "".join(f"<li>{html.escape(x)}</li>" for x in (fx or [f"{t} as shown in the photos"]))
    q = [x for x in faq if x][:3] or ["How long does shipping take?", "What if it arrives damaged or defective?"]
    ans = {"ship": f"Orders ship in 1-3 business days and arrive in {ship_days}. Shipping is free on orders over ${free_over}.",
           "dmg": f"Tell us within 7 days of delivery with a photo and we send a replacement or a full refund. Returns are accepted for {returns_days} days.",
           "size": "Check the size chart in the photos before ordering; if it does not fit, contact us and we will help.",
           "care": "Follow the care notes on the label; most items wipe clean or wash on a gentle cycle."}
    def a(qq: str) -> str:
        ql = qq.lower()
        return ans["ship"] if "ship" in ql else ans["dmg"] if ("damag" in ql or "defect" in ql or "return" in ql) else ans["size"] if "size" in ql else ans["care"]
    faq_html = "".join(f"<h3>{html.escape(x)}</h3><p>{html.escape(a(x))}</p>" for x in q)
    body = (f"<p>{html.escape(t)}{(' in ' + html.escape(col.lower())) if col else ''} from {html.escape(store_name)}, for anyone shopping for "
            f"{html.escape(niche.lower())}.</p><h2>Details</h2><ul>{lis}</ul><h2>Shipping and returns</h2><p>{html.escape(ans['ship'])} "
            f"{html.escape(ans['dmg'])}</p><h2>Questions</h2>{faq_html}")
    if len(body) < 800:
        body += "<p>" + html.escape(f"Every {singular(type_key)} is checked against the supplier photos and listing before it ships. "
                                    f"Questions about this item? Contact us and a person answers.") + "</p>"
    alt = {k2: f"{col + ' ' if col else ''}{t}, {lbl}".strip() for k2, lbl in zip(shots, ["studio photo", "in use", "product view", "close-up detail", "main feature"])}
    return {"title": ttl, "short_title": short, "tags": tags, "alt_by_key": alt, "descriptionHtml": body,
            "seo_title": f"{short} | {store_name}"[:70], "seo_description": f"{ttl}. Free shipping over ${free_over}."[:160], "_engine": "rules"}


# ---------------------------------------------------------------------------- store pages
def store_pages(f: dict) -> dict:
    e = {k: html.escape(str(v)) for k, v in f.items()}
    faq = [("How long does shipping take?", f"Orders are processed in 1-3 business days and arrive {e['delivery']}."),
           ("How much is shipping?", f"Shipping is {e['shipping_price']}."),
           ("What is your return policy?", e["returns"][:1].upper() + e["returns"][1:] + "."),
           ("Can I track my order?", "Yes. You get a tracking link by email as soon as your order ships."),
           ("What payment methods do you take?", "All major cards and the express checkouts shown at checkout."),
           ("Where do you ship?", f"We ship to the {e['ships_to']}."),
           ("My item arrived damaged. What now?", "Send us a photo within 7 days of delivery and we replace it or refund you."),
           ("How do I contact you?", f"Email {e['support_email']}. A person answers within one business day.")]
    return {"refund": f"<h2>Returns and refunds</h2><p>{e['returns'][:1].upper() + e['returns'][1:]}.</p><p>To start a return, email {e['support_email']} with your order number.</p><p>Refunds go back to the original payment method.</p>",
            "shipping": f"<h2>Shipping</h2><p>We ship to the {e['ships_to']}. Orders are processed in 1-3 business days and arrive {e['delivery']}.</p><p>Shipping is {e['shipping_price']}.</p><p>Products ship from partner warehouses, so items in one order may arrive separately.</p>",
            "privacy": f"<h2>Privacy</h2><p>{e['store']} collects only what is needed to take and deliver your order: your name, address, email and order details. Payments are handled by our checkout provider; we never see your full card number.</p><p>We do not sell your information. Email {e['support_email']} to see or delete your data.</p>",
            "terms": f"<h2>Terms of service</h2><p>By ordering from {e['store']} you agree to these terms, our shipping policy and our refund policy. Prices are in US dollars and can change. We may cancel an order if an item is unavailable, and refund it in full.</p><p>These terms are a template for a small business, not legal advice.</p>",
            "contact": f"<h2>Contact</h2><p>Email {e['support_email']}. A person answers within one business day.</p><p>Based in {e['business_city']}.</p>",
            "faq": "".join(f"<h3>{q}</h3><p>{a}</p>" for q, a in faq),
            "about": f"<h2>About {e['store']}</h2><p>{e['tagline'] or e['about_text'] or ('A small online shop for ' + e['sells'] + '.')}</p><p>{e['about_text'] if e['tagline'] and e['about_text'] else 'We pick every product, check it against the supplier, and stand behind every order.'}</p>",
            "_engine": "rules"}


# ---------------------------------------------------------------------------- ads text
def ad_assets(name: str, niche: str, types: list[str], domain: str = "") -> dict:
    n, ni = name[:25], niche[:30]
    ts = [title(t) for t in types][:3] or [ni]
    heads = [ni, f"Shop {ni}"[:30], f"{n}"[:30], "Free Shipping Over $70", "30-Day Returns", "Checked Before It Ships",
             "Ships Within 3 Days"] + [t[:30] for t in ts] + [f"New {t}"[:30] for t in ts] + [f"{n} Store"[:30], "Shop the Range"]
    heads = list(dict.fromkeys(h for h in heads if h and len(h) <= 30))[:15]
    while len(heads) < 15:
        heads.append(f"{ts[len(heads) % len(ts)]} Deals"[:30])
    longs = [f"{ni} from {n}, shipped to your door"[:90], f"Shop {', '.join(ts)}"[:90], f"{n}: real products, real prices, fast replies"[:90],
             "Free shipping over $70 and easy returns on every order", f"Find your next {singular(ts[0]).lower()} at {n}"[:90]]
    descs = [f"{ni}, checked before it ships."[:60], f"Shop {', '.join(ts)} at {n}. Free shipping over $70."[:90],
             "Every order tracked. A person answers your questions.", f"New {singular(ts[0]).lower()} styles added every week."[:90],
             "Easy returns. Secure checkout. Ships in 1-3 days."]
    themes = list(dict.fromkeys([t.lower() for t in types] + [singular(t) for t in types] + [niche.lower()] + [f"buy {singular(t)}" for t in types]
                                + [f"best {singular(t)}" for t in types] + [f"{singular(t)} for women" for t in types] + [f"{singular(t)} for men" for t in types]))[:25]
    return {"headlines": heads, "long_headlines": longs, "descriptions": descs, "search_themes": themes, "business_name": n, "_engine": "rules"}

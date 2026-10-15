"""Solo step 2: the ONE reference store to rip.

verify(url): the store Jason pasted from the Google ads. One-line reason on any fail. On a pass it also reads what the
ripper needs (rip.json): the store's name, price range, best sellers, colours, fonts, and the order of the sections on
its homepage, product page and collection page.
recommend(): option B. Only stores that clear a hard bar (own brand on its own catalog, REC_MIN_REVIEWS+ reviews with a
named source, our price range, on-niche). An empty list is a valid answer; it never fills the list with weak stores.
"""
from __future__ import annotations

import collections
import json
import re

from ..lib import ai, engine, rulebook
from ..lib.core import Log, Stop, Store, now
from . import stores as st

REC_MIN_REVIEWS = 1000
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
FONT = re.compile(r"font-family\s*:\s*['\"]?([A-Za-z][A-Za-z0-9 \-]{2,40})", re.I)
SECTION = re.compile(r'id="shopify-section-(?:template--\d+__)?([a-z0-9_\-]+)"', re.I)
SKIP_FONTS = {"inherit", "initial", "sans-serif", "serif", "monospace", "system-ui", "-apple-system", "arial", "helvetica", "var"}


def _gate(store: Store) -> dict:
    g = store.load("gate.json")
    if not g or not g.get("ok"):
        raise Stop("pass the direction gate first (step 1)")
    return g


def _words(g: dict) -> list[str]:
    ws = []
    for t in list(g.get("types") or []) + list(g.get("cj_keywords") or []):
        ws += [st._stem(w) if hasattr(st, "_stem") else w for w in re.findall(r"[a-z]+", t.lower()) if len(w) > 3]
    return list(dict.fromkeys(w for w in ws if w not in st.STOP))


def _top(g: dict) -> float:
    return max([v.get("H") or 0 for v in (g.get("market") or {}).values()] or [0])


def _text(html: str, n: int = 9000) -> str:
    t = re.sub(r"(?is)<(script|style|svg|noscript)[^>]*>.*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", "\n", t)
    t = re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", t))
    return t.strip()[:n]


def _look(html: str) -> dict:
    hexes = collections.Counter(h.upper() for h in HEX.findall(html))
    fonts = collections.Counter(f.strip() for f in FONT.findall(html) if f.strip().lower() not in SKIP_FONTS)
    sections = list(dict.fromkeys(SECTION.findall(html)))
    return {"colors": [h for h, _ in hexes.most_common(8)], "fonts": [f for f, _ in fonts.most_common(3)], "sections": sections}


LAYOUT = """You read an online store's pages (text extracted from the HTML, top to bottom, plus Shopify section ids when
present) and write down the page LAYOUT so another store can be built with the same structure. Answer JSON:
{"brand": "store name",
 "home": [{"type": "announcement|header|hero|quick-links|category-tiles|product-carousel|product-grid|trust-bar|feature-cards|editorial|stats|reviews|press-logos|newsletter|footer|other",
           "what": "what this section shows, max 12 words"}],
 "product": [{"type": "gallery|title-price|variant-picker|add-to-cart|trust-strip|description|tabs|faq|specs|reviews|cross-sell|sticky-bar|other", "what": "max 12 words"}],
 "collection": [{"type": "title|filters|sort|grid|pagination|intro-text|other", "what": "max 12 words"}],
 "nav": ["top menu items in order"],
 "feel": ["3 bullets on the look: colour use, photo style, spacing"]}
List sections in page order. Mark fake-proof sections (reviews, press logos, 'as seen on') with their real type; the
builder drops them."""


def _layout(home: str, prod: str, coll: str, secs: dict) -> dict:
    ask = (f"HOMEPAGE (section ids: {', '.join(secs.get('home') or []) or 'none'}):\n{_text(home)}\n\n"
           f"PRODUCT PAGE (section ids: {', '.join(secs.get('product') or []) or 'none'}):\n{_text(prod, 6000)}\n\n"
           f"COLLECTION PAGE:\n{_text(coll, 3000)}")
    return ai.ask_json("rip_layout", LAYOUT, ask, max_tokens=2500,
                       check=lambda a: [] if isinstance(a, dict) and a.get("home") and a.get("product") else ["need home and product"])


def verify(store: Store, log: Log, url: str) -> dict:
    g = _gate(store)
    d = st.domain_of(url)
    log(f"# Reference store: {d} for {g['niche']}")
    checks: list[dict] = []

    def fail(key: str, text: str) -> dict:
        checks.append({"key": key, "ok": False, "text": text})
        out = {"url": url, "domain": d, "ok": False, "reason": text, "checks": checks, "at": now()}
        store.save("reference.json", out)
        log(f"FAIL: {text}")
        return out

    if not d:
        return fail("live", "that is not a store link")
    if st.is_generalist(url):
        return fail("own_brand", "a marketplace or big multi-brand store, not one brand's own store")
    code, home = st.fetch(f"https://{d}/")
    if code != 200:
        return fail("live", f"the site did not load (HTTP {code})")
    if st.PARKED.search(home[:200_000]):
        return fail("live", "parked or for-sale domain: no store there")
    checks.append({"key": "live", "ok": True, "text": "live store"})
    if st.LUXE.search(home[:200_000]):
        return fail("luxury", "luxury / made-to-order / price on request: not our price level")
    checks.append({"key": "luxury", "ok": True, "text": "normal retail, prices shown"})
    words, top = _words(g), _top(g)
    cat = st.shopify_catalog(url)
    m = st.measure(cat, words) if cat else {}
    if cat:
        if m["vendors"] > st.VENDORS_MAX and m["top_vendor_share"] < st.VENDOR_TOP_MIN:
            return fail("own_brand", f"reseller: {m['vendors']} brands, the biggest only {m['top_vendor_share']:.0%} of products")
        checks.append({"key": "own_brand", "ok": True, "text": f"own brand ({m['top_vendor_share']:.0%} of {m['products']} products one brand)"})
        if top and m.get("median_price") and m["median_price"] > top * st.PRICE_X:
            return fail("price", f"too expensive: typical product ${m['median_price']:.0f}, our market tops at ${top:.0f}")
        checks.append({"key": "price", "ok": True, "text": f"typical product ${m.get('median_price') or 0:.0f} (market top ${top:.0f})"})
        if m["focus"] < st.FOCUS_MIN:
            return fail("on_niche", f"off-niche: only {m['focus']:.0%} of its products are {g['niche'].lower()}")
        checks.append({"key": "on_niche", "ok": True, "text": f"{m['focus']:.0%} of its products are {g['niche'].lower()}"})
    else:
        checks.append({"key": "own_brand", "ok": True, "text": "not on Shopify: brand, price and focus read from its pages"})
    handles = st.best_sellers(d) or ([p.get("handle") for p in (cat or [])[:8]])
    by_handle = {p.get("handle"): p for p in (cat or [])}
    best = []
    for h in [h for h in handles if h][:8]:
        p = by_handle.get(h, {})
        price = None
        try:
            price = float(((p.get("variants") or [{}])[0]).get("price"))
        except (TypeError, ValueError):
            pass
        best.append({"handle": h, "title": p.get("title") or h.replace("-", " "), "price": price,
                     "image": ((p.get("images") or [{}])[0]).get("src") if p else None, "url": f"https://{d}/products/{h}"})
    prod_url = best[0]["url"] if best else ""
    _, prod = st.fetch(prod_url) if prod_url else (-1, "")
    _, coll = st.fetch(f"https://{d}/collections/all")
    look = _look(home)
    secs = {"home": look["sections"], "product": _look(prod)["sections"] if prod else []}
    log("reading the page layout...")
    lay = rulebook.layout(home, secs) if engine.rules() else _layout(home, prod, coll, secs)
    prices = sorted(b["price"] for b in best if b["price"]) or ([m["median_price"]] if m.get("median_price") else [])
    out = {"url": url, "domain": d, "ok": True, "reason": "", "checks": checks, "at": now(), "name": lay.get("brand") or d,
           "platform": "shopify" if cat else "other", "products": m.get("products"), "median_price": m.get("median_price"),
           "price_range": f"${prices[0]:.0f}-${prices[-1]:.0f}" if prices else "", "best_sellers": best,
           "look": {"colors": look["colors"], "fonts": look["fonts"], "feel": lay.get("feel") or []}}
    store.save("reference.json", out)
    store.save("rip.json", {"from": d, "at": now(), "sections": secs, "layout": lay, "look": out["look"], "best_sellers": best})
    log(f"PASS: {out['name']} ({d}) {out['price_range']}; best sellers: {', '.join(b['title'][:30] for b in best[:4])}")
    log(f"layout: home {len(lay.get('home') or [])} sections, product {len(lay.get('product') or [])}; colours {', '.join(look['colors'][:5])}")
    log(ai.usage_line())
    return out


def recommend(store: Store, log: Log) -> dict:
    """Option B: run the store finder, keep only stores clearing the hard bar."""
    g = _gate(store)
    scout = {"niche": g["niche"], "approved": [g["niche"]], "corners": [{"name": g["niche"], "cj_keywords": g["cj_keywords"],
             "search": (g.get("types") or [g["niche"]])[0], "top_price": _top(g), "typical_price": 0, "products": g.get("types") or []}]}
    store.save("scout.json", scout)
    found = st.run(store, log)
    top = _top(g)
    recs, why_not = [], []
    for r in found["stores"]:
        bar = [(r.get("verified"), "own catalog not readable"), ((r.get("reviews") or 0) >= REC_MIN_REVIEWS, f"{r.get('reviews') or 0} reviews (need {REC_MIN_REVIEWS})"),
               ((r.get("focus") or 0) >= st.FOCUS_MIN, "off-niche"),
               (not (top and r.get("median_price") and r["median_price"] > top * st.PRICE_X), "above our price range")]
        miss = [t for ok, t in bar if not ok]
        if miss:
            why_not.append({"domain": r["domain"], "why": miss[0]})
        else:
            recs.append({k: r.get(k) for k in ("name", "domain", "url", "reviews", "reviews_from", "price_range", "median_price", "products", "why", "meta_ads_link")})
    out = {"at": now(), "bar": f"own brand, {REC_MIN_REVIEWS}+ reviews, our price range, on-niche", "recs": recs[:4], "not_shown": why_not}
    store.save("reference_recs.json", out)
    log(f"{len(recs)} store(s) clear the bar" + (": " + ", ".join(r['domain'] for r in recs) if recs else " (none shown)"))
    return out

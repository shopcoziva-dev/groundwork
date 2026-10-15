"""Model stores: the best SPECIALIST stores in the niche: stores that sell exactly this, not department stores.

A lighting niche wants lighting stores, not a home store that also sells chairs and couches; a heated-jacket niche wants
heated-apparel stores, not REI. Two steps:
  1 Claude searches the web for 12-15 US online stores that focus on the approved corners (DTC brands, Shopify stores,
    the ones running Google Shopping ads), with what they sell, their price range and why they are worth copying.
  2 Code checks every one, it does not take Claude's word for it:
      - parked / for-sale domains (a store that shut down and lost its domain) are dropped
      - marketplaces and department/general stores are dropped by domain (amazon, walmart, rei, wayfair, ...)
      - the store must answer (HTTP 200)
      - Shopify stores publish their catalog (/products.json): code counts what share of their products are actually
        this niche (FOCUS). Under 60% = a generalist = dropped. Also: product count, median price, top product types.
      - own brand only: a store whose catalog is many brands with no main one (a dealer like Lighting New York) is dropped
        (VENDORS_MAX different vendors and the biggest under VENDOR_TOP_MIN of the products)
      - non-Shopify stores cannot be counted: kept only if Claude says they are specialists, marked "unverified", ranked last
EVIDENCE (nobody can see another store's sales, so we count what is public and tracks sales):
  - reviews on their best sellers (JSON-LD, Judge.me, Loox, Yotpo, Okendo, Stamped): 1-3% of buyers review
  - their own best-sellers order (Shopify "best selling" sort): which products actually sell
  - Google Shopping presence: how many of their products show up for the niche's searches (SerpAPI if a key is set,
    otherwise Claude's own web search on the Anthropic key we already pay for: one call per run, about a cent)
  - a Meta Ad Library link (its API does not cover US shop ads, so it is a link for Jason, not a number)
  - Trustpilot's public review count for the store (reviews loaded by JavaScript are invisible to a plain read)
Ranked: verified own-brand stores by evidence score (reviews 75, Google Shopping 25), then focus. Used by the profile (what the best stores sell and how) and the setup
checklist (which stores to model the theme and layout on).
Writes stores/<slug>/model_stores.json.
"""
from __future__ import annotations

import json
import math
import re
import statistics
import urllib.error
import urllib.parse
import urllib.request

from ..lib import ai, market
from ..lib.core import Log, Stop, Store, now

GENERALISTS = {
    "amazon", "walmart", "target", "ebay", "etsy", "aliexpress", "alibaba", "temu", "shein", "wish", "wayfair", "overstock", "rei",
    "homedepot", "lowes", "costco", "samsclub", "bestbuy", "kohls", "macys", "nordstrom", "jcpenney", "ikea", "dickssportinggoods",
    "academy", "backcountry", "zappos", "chewy", "petsmart", "petco", "bedbathandbeyond", "potterybarn", "westelm", "crateandbarrel",
    "cb2", "article", "joss", "allmodern", "birchlane", "hayneedle", "qvc", "hsn", "kmart", "sears", "tjmaxx", "marshalls", "ross",
    "burlington", "oldnavy", "gap", "hm", "zara", "uniqlo", "asos", "fashionnova", "nike", "adidas", "underarmour", "patagonia",
    "thenorthface", "columbia", "llbean", "cabelas", "basspro", "scheels", "sportsmans", "ulta", "sephora", "cvs", "walgreens",
    "lampsplus", "homegoods", "kirklands", "biglots", "dollargeneral", "staples", "officedepot", "newegg", "bhphotovideo",
    "google", "facebook", "instagram", "tiktok", "pinterest", "youtube", "reddit",
    # multi-brand lighting dealers (resell other brands, sell fans and furniture too): not something to copy (2026-10-13)
    "lightingnewyork", "lumens", "ylighting", "bellacor", "destinationlighting", "build", "capitollighting", "lightology", "1800lighting",
    "lightsonline", "shadesoflight", "lampsusa", "lightingdirect", "foundrylighting", "lightingconnection", "houzz", "perigold",
}
# A reference has to sell at OUR price level: a $3,300 designer sconce (Apparatus, 2026-10-13) teaches nothing about a
# dropship store whose lamps sell for $70-150. Median price above PRICE_X times the direction's top price = dropped.
PRICE_X = 1.5
LUXE = re.compile(r"\binquire\b|to the trade|trade program|made to order|lead time|request a quote|price on request|one of one|1 of 1|edition of \d+|collectible design|tear sheet", re.I)
VENDORS_MAX = 8          # more different brands than this ...
VENDOR_TOP_MIN = 0.5     # ... and no brand with half the products = a reseller, not an own-brand store
FOCUS_MIN = 0.60
# A domain that lapsed and got parked shows a sale banner and ad links instead of a store (ylighting.com, 2026-10-13).
PARKED = re.compile(r"buy this domain|this domain (?:is|may be) for sale|domain (?:is )?for sale|seo\.domains|sedo|dan\.com|afternic|hugedomains|"
                    r"parkingcrew|bodis|above\.com|related searches|the domain .{0,40} (?:has expired|is parked)", re.I)

FIND = """You find the best SPECIALIST online stores for a US store niche, to study and copy.
Niche: {niche}. Corners we will sell: {corners}.
A specialist sells (almost) only this kind of product: for lighting, a lighting store, never a home store that also
sells furniture; for heated jackets, a heated-apparel store, never REI or a general outdoor store.
ONLY own-brand DTC stores: a brand selling its OWN products under its own name (like Coziva), usually on Shopify, with a
focused catalog (roughly 30-1500 products). SAME PRICE LEVEL as ours: their typical product sells for {band}.
NEVER design studios, luxury or made-to-order brands, "inquire for price" or to-the-trade stores, and NEVER multi-brand retailers or authorized dealers that resell other brands
(for lighting: no Lighting New York, Lumens, YLighting, Bellacor, Lamps Plus, Build.com). No marketplaces
(Amazon, Etsy, Walmart), no department stores, no big general retailers, no brand-new tiny stores with a handful of items.
Prefer: direct-to-consumer brands and Shopify stores that run Google Shopping ads in this niche, with real reviews.
Search the web (Google Shopping sellers, "best <niche> brands", Reddit/review roundups, store sites). Answer JSON:
{{"stores": [{{"name": "...", "url": "https://exact homepage", "kind": "own brand" | "multi-brand retailer", "sells": "what they sell in one line",
  "corners": ["which of our corners they cover"], "price_range": "$x-$y", "ads": true if you saw them advertise,
  "why": "what is worth copying: layout, offer, photos, bundles, trust signals"}}, ... 12 to 15 stores]}}
Only stores you actually found, with their real homepage URL."""

STOP = {"with", "for", "and", "the", "from", "your", "that", "this", "kit", "set", "pack", "pcs", "new", "best", "women", "mens",
        "womens", "men's", "women's", "kids", "usb", "rechargeable", "portable", "electric", "smart", "mini", "large", "small"}


def keywords(scout: dict, approved: list[str]) -> list[str]:
    """Niche words from the approved corners: search phrases, product types, CJ keywords (singular, lowercase)."""
    words: set[str] = set()
    for c in scout["corners"]:
        if c["name"] not in approved:
            continue
        for phrase in [c.get("search", "")] + list(c.get("products") or []) + list(c.get("cj_keywords") or []):
            for w in re.findall(r"[a-z]+", str(phrase).lower()):
                if len(w) > 3 and w not in STOP:
                    words.add(w[:-1] if w.endswith("s") and not w.endswith("ss") else w)
    return sorted(words)


def domain_of(url: str) -> str:
    host = urllib.parse.urlparse(url if "//" in url else "https://" + url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_generalist(url: str) -> bool:
    d = domain_of(url)
    base = d.split(".")[-2] if d.count(".") >= 1 else d
    return base.replace("-", "") in GENERALISTS


def fetch(url: str, timeout: int = 20):
    """(status, text). Any failure is (-1, '')."""
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Groundwork store research)",
                                                                        "Accept": "application/json,text/html"}), timeout=timeout)
        return r.status, r.read(3_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:  # noqa: BLE001
        return -1, ""


def shopify_catalog(url: str) -> list[dict] | None:
    """Up to 500 products from a Shopify store's public catalog, or None if it is not a Shopify store / hides it."""
    root = f"https://{domain_of(url)}"
    items: list[dict] = []
    for page in (1, 2):
        code, txt = fetch(f"{root}/products.json?limit=250&page={page}")
        if code != 200:
            break
        try:
            batch = json.loads(txt).get("products") or []
        except json.JSONDecodeError:
            break
        items += batch
        if len(batch) < 250:
            break
    return items if items else None


def _matches(p: dict, words: list[str]) -> bool:
    text = " ".join([str(p.get("title", "")), str(p.get("product_type", "")), " ".join(p.get("tags") or []) if isinstance(p.get("tags"), list) else str(p.get("tags", ""))]).lower()
    return any(re.search(rf"\b{re.escape(w)}", text) for w in words)


def measure(products: list[dict], words: list[str]) -> dict:
    hits = [p for p in products if _matches(p, words)]
    prices = []
    for p in products:
        for v in (p.get("variants") or [])[:1]:
            try:
                prices.append(float(v.get("price")))
            except (TypeError, ValueError):
                pass
    types: dict[str, int] = {}
    for p in products:
        t = str(p.get("product_type") or "").strip() or "(none)"
        types[t] = types.get(t, 0) + 1
    vendors: dict[str, int] = {}
    for p in products:
        v = str(p.get("vendor") or "").strip().lower()
        if v:
            vendors[v] = vendors.get(v, 0) + 1
    top_share = round(max(vendors.values()) / len(products), 2) if vendors and products else 1.0
    return {"products": len(products), "focus": round(len(hits) / len(products), 2) if products else 0.0, "vendors": len(vendors), "top_vendor_share": top_share,
            "median_price": round(statistics.median(prices), 2) if prices else None,
            "top_types": [t for t, _ in sorted(types.items(), key=lambda x: -x[1])[:5]]}


REVIEW_RE = [re.compile(x, re.I) for x in (
    r'"reviewCount"\s*:\s*"?(\d[\d,]*)', r'"ratingCount"\s*:\s*"?(\d[\d,]*)', r'data-number-of-reviews="(\d[\d,]*)"',
    r'data-raters="(\d[\d,]*)"', r'"reviews_count"\s*:\s*"?(\d[\d,]*)', r'data-count="(\d[\d,]*)"[^>]*stamped', r'\b(\d[\d,]{0,7})\s+reviews?\b')]
HANDLE_RE = re.compile(r'href="(?:https?://[^"/]+)?(?:/[a-z]{2}(?:-[a-z]{2})?)?(?:/collections/[^"/]+)?/products/([a-z0-9][a-z0-9\-]*)', re.I)
TOP_N = 8


DIAG: list[str] = []   # what the evidence step saw, logged so a zero can be explained


BEST_PATHS = ["/collections/all?sort_by=best-selling", "/collections/best-sellers", "/collections/bestsellers", "/collections/best-selling",
              "/collections/shop-all?sort_by=best-selling", "/collections/all-products?sort_by=best-selling"]


def best_sellers(domain: str) -> list[str]:
    """Product handles in the store's own best-selling order (Shopify sort), first TOP_N + 2. Stores often hide /collections/all,
    so the usual best-seller collections are tried too."""
    code, html = -1, ""
    for path in BEST_PATHS:
        code, html = fetch(f"https://{domain}{path}")
        if code == 200 and HANDLE_RE.search(html):
            break
    if code != 200:
        DIAG.append(f"{domain}: no best-sellers page (last HTTP {code})")
        return []
    seen: list[str] = []
    for h in HANDLE_RE.findall(html):
        h = h.lower()
        if h not in seen:
            seen.append(h)
    DIAG.append(f"{domain}: best-sellers page {len(html)} bytes, {len(seen)} product links")
    return seen[:TOP_N + 2]


def review_count(html: str) -> int:
    """Biggest review count the product page shows (the review app's own number, else JSON-LD)."""
    best = 0
    for rx in REVIEW_RE:
        for m in rx.findall(html[:600_000]):
            try:
                best = max(best, int(str(m).replace(",", "")))
            except ValueError:
                pass
    return best if best < 1_000_000 else 0


GSHOP = """You check who sells in Google Shopping for a US store niche. Search Google Shopping / the web for each search
below the way a US shopper would, and list the sellers (store names) whose product listings you actually saw, with how
many listings each had. Only sellers you saw; never guess. Answer JSON: {"sellers": [{"seller": "...", "domain": "if shown", "listings": 0}]}"""


def google_presence_web(rows: list[dict], queries: list[str]) -> dict[str, int]:
    """No SerpAPI: one Claude web-search call for all candidate stores. Returns domain -> listings seen (0 = not seen)."""
    try:
        ans = ai.ask_json("google_shopping_sellers", GSHOP, "Searches: " + "; ".join(queries[:3]), web=6, max_tokens=1500,
                          check=lambda a: [] if isinstance(a, dict) and isinstance(a.get("sellers"), list) else ["need sellers list"])
    except Stop:
        return {}
    seen = [(str(x.get("seller") or "").lower().replace(" ", ""), str(x.get("domain") or "").lower(), int(x.get("listings") or 1))
            for x in ans.get("sellers") or [] if isinstance(x, dict)]
    DIAG.append("Google Shopping sellers seen: " + (", ".join(f"{s}({n})" for s, _, n in seen[:25]) or "none"))
    out = {}
    for r in rows:
        base = r["domain"].split(".")[0].replace("-", "")
        name = str(r.get("name") or "").lower().replace(" ", "")
        out[r["domain"]] = sum(n for s, d, n in seen if (d and r["domain"] in d) or (s and (base in s or (name and (name in s or s in name)))))
    return out


TP_RE = [re.compile(r'"numberOfReviews"\s*:\s*(\d+)'), re.compile(r'"reviewCount"\s*:\s*"?(\d+)'), re.compile(r'"totalNumberOfReviews"\s*:\s*(\d+)')]


def trustpilot(domain: str) -> int:
    """Store-wide review count on Trustpilot (public page, JSON-LD). 0 when the store has no page or it cannot be read."""
    code, html = fetch(f"https://www.trustpilot.com/review/{domain}")
    if code != 200:
        DIAG.append(f"{domain}: Trustpilot HTTP {code}")
        return 0
    best = 0
    for rx in TP_RE:
        for m in rx.findall(html):
            best = max(best, int(m))
    DIAG.append(f"{domain}: Trustpilot {best} reviews")
    return best


REVIEWS = """You look up how many customer reviews online stores have, as public proof of how much they sell. For each store
below, search the web (its own site's review badge, Judge.me / Yotpo / Okendo pages, Trustpilot, Google seller ratings,
Reddit or press mentioning review counts) and report the biggest store-wide review count you actually saw, with where.
Only numbers you saw; 0 when you found none; never estimate. Answer JSON: {"stores": [{"domain": "...", "reviews": 0, "source": "where"}]}"""


def reviews_web(rows: list[dict]) -> dict[str, tuple[int, str]]:
    """Plain reads are blocked (Trustpilot 403, review widgets load by JavaScript), so one Claude web-search call
    collects the store-wide review count for every store. Returns domain -> (count, source)."""
    try:
        ans = ai.ask_json("store_reviews", REVIEWS, "Stores: " + ", ".join(f"{r.get('name')} ({r['domain']})" for r in rows), web=10, max_tokens=1500,
                          check=lambda a: [] if isinstance(a, dict) and isinstance(a.get("stores"), list) else ["need stores list"])
    except Stop as e:
        DIAG.append(f"review search failed: {e}")
        return {}
    out = {}
    for x in ans.get("stores") or []:
        d = str((x or {}).get("domain") or "").lower().replace("www.", "")
        try:
            out[d] = (int(str(x.get("reviews") or 0).replace(",", "")), str(x.get("source") or "")[:80])
        except ValueError:
            continue
    DIAG.append("review counts from web search: " + ", ".join(f"{d} {n} ({src})" for d, (n, src) in out.items()))
    return out


def fill_from_web(rows: list[dict], queries: list[str]) -> None:
    """Free route for every number a plain read could not get: review counts and Google Shopping presence by web search."""
    if not rows:
        return
    rv = reviews_web(rows)
    for r in rows:
        n, src = rv.get(r["domain"], (0, ""))
        if n > (r.get("reviews") or 0):
            r.update(reviews=n, reviews_from=src)
    if not market.serpapi_ready():
        counts = google_presence_web(rows, queries)
        for r in rows:
            r["google_ads"] = counts.get(r["domain"], 0) if counts else r.get("google_ads")
    for r in rows:
        r["evidence_score"] = _score(r.get("reviews") or 0, r.get("google_ads"))


def google_presence(name: str, domain: str, queries: list[str]) -> int | None:
    """How many Google Shopping results for the niche's searches are this store's (None without SERPAPI_KEY: filled later by web search)."""
    if not market.serpapi_ready():
        return None
    base = domain.split(".")[0].replace("-", "")
    who = {name.lower().replace(" ", ""), base}
    n = 0
    for q in queries[:3]:
        for title, _ in market.serpapi(q, n=40):
            src = title.split("|")[-1].strip().lower().replace(" ", "")
            if src and any(w and (w in src or src in w) for w in who):
                n += 1
    return n


def _score(reviews: int, ads) -> int:
    """Reviews carry 75 (1 review ~ 0, 10k+ = full), Google Shopping presence 25 (own-brand stores rarely beat Wayfair there)."""
    return round(min(1.0, math.log10((reviews or 0) + 1) / 4) * 75 + (min(ads, 10) / 10 * 25 if ads else 0))


def evidence(row: dict, catalog: list[dict] | None, queries: list[str]) -> dict:
    d = row["domain"]
    titles = {str(p.get("handle") or "").lower(): str(p.get("title") or "") for p in (catalog or [])}
    handles = best_sellers(d)
    reviews, top = 0, []
    for h in handles[:TOP_N]:
        code, html = fetch(f"https://{d}/products/{h}")
        if code != 200:
            continue
        n = review_count(html)
        if not n:
            apps = [a for a in ("judge.me", "jdgm", "yotpo", "okendo", "loox", "stamped", "reviews.io", "aggregateRating") if a in html]
            DIAG.append(f"{d}/products/{h}: 0 reviews found (review app markers: {apps or 'none'})")
        reviews += n
        top.append({"title": titles.get(h) or h.replace("-", " ").title(), "reviews": n})
    tp = trustpilot(d)
    ads = google_presence(str(row.get("name") or ""), d, queries)
    score = _score(max(reviews, tp), ads)
    return {"reviews": max(reviews, tp), "site_reviews": reviews, "trustpilot_reviews": tp, "best_sellers": top, "google_ads": ads, "evidence_score": score,
            "meta_ads_link": "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&q=" + urllib.parse.quote(str(row.get("name") or d))}


def recheck(store: Store, log: Log) -> dict:
    """Recount the evidence for the stores already found (no new store search, so no big Claude call)."""
    ms = store.load("model_stores.json")
    scout = store.load("scout.json") or {}
    if not ms or not ms.get("stores"):
        raise Stop("run `stores` first")
    approved = scout.get("approved") or []
    queries = [q for c in scout.get("corners") or [] if c["name"] in approved for q in ([c.get("search")] + list(c.get("cj_keywords") or [])[:2]) if q]
    DIAG.clear()
    for r in ms["stores"]:
        cat = shopify_catalog("https://" + r["domain"]) if r.get("platform") == "shopify" else None
        r.update(evidence(r, cat, queries))
    fill_from_web(ms["stores"], queries)
    ms["stores"].sort(key=lambda r: (not r.get("verified"), -(r.get("evidence_score") or 0), -(r.get("focus") or 0)))
    ms["rechecked"] = now()
    store.save("model_stores.json", ms)
    for r in ms["stores"]:
        log(f"{str(r.get('name'))[:26]:26} {r['domain'][:28]:28} score {r.get('evidence_score', 0):>3}  reviews {r.get('reviews', 0):>6}  google {r.get('google_ads')}  "
            f"best: {', '.join(b['title'][:30] for b in (r.get('best_sellers') or [])[:2])}")
    log("\nwhat it saw:\n  " + "\n  ".join(DIAG[:60]))
    return ms


def run(store: Store, log: Log, url: str = "") -> dict:
    scout = store.load("scout.json")
    if not scout or not scout.get("approved"):
        raise Stop("approve Scout corners first")
    approved = scout["approved"]
    words = keywords(scout, approved)
    log(f"# Model stores: {store.get('niche')} | corners: {', '.join(approved)}")
    log(f"niche words used to measure focus: {', '.join(words[:20])}")
    picked = [c for c in scout["corners"] if c["name"] in approved]
    top = max([float(c.get("top_price") or 0) or float(c.get("typical_price") or 0) * 1.5 for c in picked] or [0])
    typ = max([float(c.get("typical_price") or 0) for c in picked] or [0])
    band_text = f"${typ * 0.5:.0f}-${top:.0f}" if typ else "the same mainstream range as ours"
    if url:   # Jason named one (e.g. picked on the Solo page): check just that store, keep the earlier list
        prev = store.load("model_stores.json") or {"stores": [], "dropped": []}
        ans = {"stores": [{"name": domain_of(url), "url": url, "kind": "own brand", "sells": "", "jason_pick": True}]}
    else:
        prev = None
        ans = ai.ask_json("model_stores", FIND.format(niche=scout["niche"], corners=", ".join(approved), band=band_text), "Find them.", web=12, max_tokens=5000,
                          check=lambda a: [] if isinstance(a, dict) and len(a.get("stores") or []) >= 5 else ["need at least 5 stores"])
    queries = [q for c in scout["corners"] if c["name"] in approved for q in ([c.get("search")] + list(c.get("cj_keywords") or [])[:2]) if q]
    kept, dropped, seen = [], [], set()
    for s in ans["stores"]:
        url = str(s.get("url") or "")
        d = domain_of(url)
        if not d or d in seen:
            continue
        seen.add(d)
        row = {**s, "domain": d}
        if is_generalist(url):
            dropped.append({**row, "dropped": "marketplace / general store / multi-brand dealer"})
            continue
        if "multi" in str(s.get("kind") or "").lower() or "dealer" in str(s.get("kind") or "").lower():
            dropped.append({**row, "dropped": "multi-brand retailer (not an own brand)"})
            continue
        code, html = fetch(f"https://{d}/")
        if code != 200:
            dropped.append({**row, "dropped": f"site did not answer (HTTP {code})"})
            continue
        if LUXE.search(html[:200_000]) and not s.get("jason_pick"):
            dropped.append({**row, "dropped": "luxury / made-to-order / inquire-for-price: not our price level"})
            continue
        if PARKED.search(html[:200_000]):
            dropped.append({**row, "dropped": "parked or for-sale domain (no store there any more)"})
            continue
        cat = shopify_catalog(url)
        if cat is not None:
            m = measure(cat, words)
            row.update(platform="shopify", verified=True, **m)
            if m["vendors"] > VENDORS_MAX and m["top_vendor_share"] < VENDOR_TOP_MIN:
                dropped.append({**row, "dropped": f"reseller: {m['vendors']} brands, biggest only {m['top_vendor_share']:.0%}"})
                continue
            if top and m.get("median_price") and m["median_price"] > top * PRICE_X:
                dropped.append({**row, "dropped": f"price level: median ${m['median_price']:.0f} vs our top ${top:.0f}"})
                continue
            if m["focus"] < FOCUS_MIN:
                dropped.append({**row, "dropped": f"generalist: only {m['focus']:.0%} of {m['products']} products are this niche"})
                continue
        else:
            row.update(platform="other", verified=False, focus=None)
        row.update(evidence(row, cat, queries))
        kept.append(row)
    fill_from_web(kept, queries)   # free route: web search fills reviews and Google presence the plain reads could not
    if prev is not None:   # single-store check: merge into the earlier list
        kept += [r for r in prev.get("stores", []) if r.get("domain") not in {k["domain"] for k in kept}]
        dropped += [r for r in prev.get("dropped", []) if r.get("domain") not in {k["domain"] for k in dropped}]
    kept.sort(key=lambda r: (not r["verified"], -(r.get("evidence_score") or 0), -(r.get("focus") or 0)))
    out = {"at": now(), "words": words, "stores": kept, "dropped": dropped}
    store.save("model_stores.json", out)
    log(f"\n{'store':26} {'domain':28} {'score':>5} {'reviews':>8} {'g-ads':>5} {'focus':>6} {'items':>6} {'median':>7}  best seller")
    for r in kept:
        f = f"{r['focus']:.0%}" if r.get("focus") is not None else "  ?"
        bs = (r.get("best_sellers") or [{}])[0].get("title", "")
        ga = r.get("google_ads")
        log(f"{str(r.get('name'))[:26]:26} {r['domain'][:28]:28} {r.get('evidence_score', 0):>5} {r.get('reviews', 0):>8} {('?' if ga is None else ga):>5} {f:>6} "
            f"{str(r.get('products') or '?'):>6} {('$' + format(r['median_price'], '.0f')) if r.get('median_price') else '   ?':>7}  {bs[:50]}")
    if dropped:
        log("\ndropped: " + "; ".join(f"{r['domain']} ({r['dropped']})" for r in dropped))
    if DIAG:
        log("\nwhat the evidence step saw:\n  " + "\n  ".join(DIAG[:60]))
    log(f"\n{len(kept)} specialist stores ({sum(1 for r in kept if r['verified'])} verified by their own catalog)")
    log(ai.usage_line())
    return out

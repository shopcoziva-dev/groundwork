"""Stage 6, Listings: one shelf product -> a live, verified Shopify listing. Same steps and gates as Coziva Pipeline v2,
driven by the niche profile instead of heated-apparel constants.

  1 qualify   shelf item READY + priced, not already listed, reserve the next SKU (<prefix>001...)
  2 supplier  fresh CJ read (photos, variants of the chosen colour)
  3 facts     Claude reads the photos -> fact sheet (type, colour, units, scale, outfit, hero, reference + detail photo)
    copy      Claude writes title, short title, description + FAQ, 13 tags, alt text per shot (from the profile's copy rules)
  4 draft     Shopify product DRAFT with clean variant names, then linked to CJ (every variant), read back
  5 images    the 5 shots (stages/images.py), vision-checked
  6 finalize  price + compare-at on every variant, copy, SEO, Google Shopping fields
  7 go live   upload photos in order with alt text, remove supplier photos, publish to the channels, ACTIVE, storefront 200
A failed run ARCHIVES its own leftover draft (never touches ACTIVE products; nothing is ever deleted).
Dry run (store.json dry_run true, the default): steps 1-3 run for real (Claude, CJ reads), every Shopify/CJ write is
logged instead of sent, and images are only generated with --images.
Writes stores/<slug>/listings/<SKU>.json (+ images under stores/<slug>/work/<SKU>/, git-ignored).
"""
from __future__ import annotations

import json
import re
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..lib import ai, engine, rulebook, rules
from ..lib.cj import CJ
from ..lib.core import Log, Stop, Store, now
from ..lib.shopify import Shopify
from . import images as IMG
from .profile import type_for

SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "XXXL", "3XL", "4XL", "5XL", "6XL"]
_SIZE = re.compile(r"^(?:\d?X{0,3}[SML]|\d?XL|XXS|XS|\d?XXXXL|\d{2,3}(?:cm)?|\d{2}(?:to|-|/)\d{2})$", re.I)   # stage5: EU ranges, cm
_W = re.compile(r"\b(women'?s?|womens|female|ladies|girls?)\b", re.I)
_M = re.compile(r"(?<!wo)\b(men'?s?|mens|male|boys?)\b", re.I)


def gender_keep(variants: list[dict], gender: str) -> list[dict]:
    """stage1 rule: when a supplier product carries both men's and women's variants, a listing keeps only its buyer's."""
    has_w = [bool(_W.search(v["name"])) for v in variants]
    has_m = [bool(_M.search(v["name"])) and not _W.search(v["name"]) for v in variants]
    if gender in ("men", "women") and any(has_w) and any(has_m):
        return [v for v, w, m in zip(variants, has_w, has_m) if (m if gender == "men" else w)]
    return variants


def records_dir(store: Store) -> str:
    """Real listings live in listings/; dry-run listings in dryrun/ so they never block real ones or use real SKU numbers."""
    return "dryrun" if store.dry_run else "listings"


def records(store: Store, include_dry: bool | None = None) -> list[dict]:
    dirs = ["listings"] + (["dryrun"] if (store.dry_run if include_dry is None else include_dry) else [])
    out = []
    for d in dirs:
        for p in sorted(store.path(d).glob("*.json")):
            r = store.load(f"{d}/{p.name}")
            if r:
                out.append(r)
    return out

FACTS = """You build the per-product FACT SHEET that keeps AI product photos physically accurate. Output ONE JSON object.
You get the supplier listing text and numbered product photos. Facts come ONLY from them. Store niche: {niche}.
Product types in this store: {types}. Launch colour (already chosen): {colour}. Buyer: {gender}.
Fields:
- type: one of the store's type keys
- color: the launch colour as shown in the photos (use "{colour}" unless the photos clearly name it differently)
- product_desc: one sentence describing the exact product (buyer, colour, type, key visible features)
- units: exact count of items in one photo of the product, e.g. "Exactly ONE lamp with its round base and one cable."
- scale: true size, e.g. "About 30 cm tall, fits on a nightstand." or how it fits on the body
- facts: 2-4 short physical facts that must hold (parts count, closures, what is included)
- outfit: what the model wears if this type uses a model (modest, fits the scene, unbranded, never a second layer of the same item); "" otherwise
- expression: calm neutral to mildly happy
- hero: {{"scene": "...", "moment": "..."}} a quiet candid moment of real use (keeps to the type's usual scene unless this product needs another)
- ref_index: index of the cleanest single photo of the product alone on a plain background in the launch colour. NEVER an infographic
  (no text, icons, callouts, drawn effects): the generator copies whatever the reference shows
- detail_index: index of the sharpest photo containing the single most appealing REAL close-up feature (a control, clasp, texture, port), or null
- detail_subject: ONE feature in that photo to zoom into, or null
- detail_box: [x0, y0, x1, y1] fractions 0-1 of that photo: a TIGHT box around that one feature plus a little surrounding material, about 3 wide : 4 tall, or null
Be literal and conservative."""

COPY = """You write Shopify listing copy for {store_name}, a US online store. Niche: {niche}. Voice: {voice}
Output ONE JSON object, no prose. Rules:
- Use ONLY facts in the supplier listing text, variant names and photos. Never invent specs. Never mention suppliers (CJ,
  AliExpress, China), dropshipping, discounts, was/now prices, fake urgency or medical claims.
{must}
- Never: {never}
- Buyer words match the listing exactly ({gender_rule}). One colour only: the launch colour {colour}; never list other colours.
- title: <= 150 characters, keyword-rich, natural: '<Product> for <buyer>, <key feature>, <2-3 real specs>, for <uses>, <Colour>'.
- short_title: <= 40 characters, the name customers see on cards. Pattern: {short_title_pattern}
  Must not be any of: {used_titles}
- DIFFERENTIATE: the first bullet and the first paragraph lead with the real features that set THIS product apart from cheaper
  look-alikes, plus a short 'Why it feels different' sentence pair. Only stated facts.
- tags: exactly 13: {tags_base}, 'type-{type}', then search phrases people actually type (fill to 13).
- descriptionHtml: a <ul> of 4 <li> facts, then two <p> paragraphs of SEO prose from the real facts, then <h3>FAQ</h3> and 4
  <p><strong>Q?</strong> A.</p> items on: {faq}. The shipping answer is exactly: 'Ships in {ship_days}, free shipping on orders
  over ${free_over}, {returns}-day returns on unused items.'
- seo_title: <= 70 characters; seo_description: <= 160 characters, plain and specific.
- alt_by_key: keys exactly {shot_keys}; each a factual <= 125 character description of that photo of this product
  ({shot_desc}), no keyword stuffing.
Return keys: title, short_title, tags, descriptionHtml, seo_title, seo_description, alt_by_key."""


def _clean_variants(chosen: list[dict], gender: str, match: str) -> tuple[list[dict], str]:
    """Owner rules: clean names ('XL', not the product name + 'Women M'), only this buyer's sizes, sizes in shop order."""
    chosen = gender_keep(chosen, gender)
    if len(chosen) == 1:   # one variant = Shopify's standard single variant
        return [{**chosen[0], "clean": "Default Title"}], "Title"
    raw = [str(v["name"]) for v in chosen]
    if match:
        raw = [re.sub(re.escape(match), " ", r, flags=re.I) for r in raw]
    toks = [r.split() for r in raw]
    k = 0
    if len(raw) > 1:
        while all(len(t) > k + 1 for t in toks) and len({t[k].lower() for t in toks}) == 1:
            k += 1
    names = []
    for t in toks:
        n = " ".join(t[k:])
        n = re.sub(r"\b(women'?s?|womens|female|ladies|men'?s?|mens|male|unisex)\b", "", n, flags=re.I)
        names.append(re.sub(r"\s+", " ", n).strip(" -/,") or "Default")
    seen: dict[str, int] = {}
    for i, n in enumerate(names):   # duplicate names get a number
        seen[n] = seen.get(n, 0) + 1
        if seen[n] > 1:
            names[i] = f"{n} {seen[n]}"
    out = [{**v, "clean": n} for v, n in zip(chosen, names)]
    is_size = all(_SIZE.match(n) for n in names)
    if is_size:
        out.sort(key=lambda v: SIZE_ORDER.index(v["clean"].upper()) if v["clean"].upper() in SIZE_ORDER else 99)
    return out, ("Size" if is_size else "Variant")


_SKU_LOCK = threading.Lock()   # lanes: one SKU number per listing, never two the same


def _next_sku(store: Store, sh: Shopify) -> str:
    pre = store.get("sku_prefix") or "GW"
    nums = [0]
    for r in records(store):
        m = re.match(rf"{re.escape(pre)}(\d{{3}})", r.get("sku") or "")
        if m:
            nums.append(int(m.group(1)))
    d = sh.read('query($q:String!){ productVariants(first:250, query:$q, sortKey:SKU, reverse:true){ nodes{ sku } } }', {"q": f"sku:{pre}*"})
    for n in ((d.get("productVariants") or {}).get("nodes") or []):
        m = re.match(rf"{re.escape(pre)}(\d{{3}})", n.get("sku") or "")
        if m:
            nums.append(int(m.group(1)))
    return f"{pre}{max(nums) + 1:03d}"


def _facts(store, profile, item, info, sku, gender, log) -> dict:
    if engine.rules():   # Beta: the fact sheet from the supplier's own text and photos, no vision call
        tk, t = type_for(profile, "", info["name"])
        if not tk:
            tk = next(iter(profile["types"]))
            t = profile["types"][tk]
        f = rulebook.facts(info, item, tk)
        if f["ref_index"] is None:
            raise Stop("the supplier listing has no photos")
        imgs = info["images"]
        sheet = {"sku": sku, "type": tk, "gender": gender, "color": f["color"], "product_desc": f["product_desc"], "units": f["units"], "scale": f["scale"],
                 "facts": f["facts"], "outfit": "", "expression": f["expression"], "hero": {}, "refs": {"product": imgs[0], "detail": imgs[1] if len(imgs) > 1 else None},
                 "detail_subject": f["detail_subject"], "detail_box": None}
        log(f"  facts (rules): {tk} | {sheet['color']} | {len(sheet['facts'])} spec lines")
        return sheet
    types = ", ".join(profile["types"])
    sys_ = FACTS.format(niche=profile["niche"], types=types, colour=item.get("colour") or "", gender=gender)
    text = f"Supplier name: {info['name']}\nVariants: {[v['name'] for v in info['variants']][:40]}\nText: {info['desc'][:2500]}\nPhotos follow, numbered from 0."
    imgs = info["images"]

    def check(f):
        e = []
        ok = lambda i: isinstance(i, int) and 0 <= i < len(imgs)   # noqa: E731
        if not ok(f.get("ref_index")):
            e.append("no usable product reference photo (ref_index)")
        tk, _ = type_for(profile, f.get("type"), info["name"])
        if not tk:
            e.append(f"type '{f.get('type')}' is not one of {types}")
        for k in ("color", "product_desc", "units", "scale"):
            if not f.get(k):
                e.append(f"missing {k}")
        return e
    f = ai.ask_json("facts", sys_, [{"type": "text", "text": text}] + ai.photos(imgs, 10), check=check, max_tokens=2500)
    tk, t = type_for(profile, f.get("type"), info["name"])
    ok = lambda i: isinstance(i, int) and 0 <= i < len(imgs)   # noqa: E731
    sheet = {"sku": sku, "type": tk, "gender": gender, "color": f["color"], "product_desc": f["product_desc"], "units": f["units"], "scale": f["scale"],
             "facts": f.get("facts") or [], "outfit": f.get("outfit") or t.get("outfit_hint") or "", "expression": f.get("expression") or "calm neutral",
             "hero": f.get("hero") or {}, "refs": {"product": imgs[f["ref_index"]], "detail": imgs[f["detail_index"]] if ok(f.get("detail_index")) else None},
             "detail_subject": f.get("detail_subject") if ok(f.get("detail_index")) else None, "detail_box": f.get("detail_box") if ok(f.get("detail_index")) else None}
    if t.get("people"):
        if not sheet["outfit"]:
            sheet["outfit"] = "a plain, unbranded, modest outfit that fits the scene"
        n = int(re.sub(r"\D", "", sku) or 0)
        sheet["model"] = item.get("model") or IMG.roster_for(store, gender, n)
    log(f"  facts: {tk} | {sheet['color']} | model {sheet.get('model') or '-'} | detail {'yes' if sheet['detail_subject'] else 'no'}")
    return sheet


def _copy(store, profile, item, info, sku, gender, type_key, log) -> dict:
    c = profile.get("copy") or {}
    used = sorted({(r.get("copy") or {}).get("short_title", "") for r in records(store) if r.get("sku") != sku and r.get("status") != "FAILED"} - {""})
    shots = IMG.SHOTS
    people = bool(profile["types"][type_key].get("people"))
    shot_desc = ("studio photo of the model wearing/using it; lifestyle photo; product photo; close-up of the real detail; feature photo" if people
                 else "main studio product photo; product in its real place or in use; second product view; close-up of the real detail; feature photo")
    gender_rule = {"women": "women's", "men": "men's"}.get(gender, "no men's/women's words")
    cp = store.load("copy.json") or {}   # Solo step 5: the approved copy pattern for this store
    pattern = ("\n\nAPPROVED COPY PATTERN FOR THIS STORE (follow it):\n- Google title: " + str(cp.get("title")) + "\n- Site title: " + str(cp.get("site"))
               + "\n- Facts to cover when the spec sheet has them: " + ", ".join(cp.get("fact_types") or []) + "\n- FAQ: " + "; ".join(cp.get("faq") or [])
               + ("\n- Owner parameters (already checked against the rules): " + cp["params"] if cp.get("params") else "")) if cp.get("title") else ""
    sys_ = rules.COPY_VOICE + pattern + "\n\n" + COPY.format(store_name=store.get("name") or "the store", niche=profile["niche"], voice=profile.get("voice", ""),
                       must="\n".join(f"- {x}" for x in c.get("must") or []), never=", ".join(c.get("never") or []) or "-", gender_rule=gender_rule,
                       colour=item.get("colour_plain") or item.get("colour") or "", short_title_pattern=c.get("short_title_pattern") or "[buyer] [ONE use or real feature] [item]",
                       used_titles=used[-60:] or "-", tags_base=", ".join(f"'{t}'" for t in ([gender] if gender != "unisex" else []) + (c.get("tags_base") or [])),
                       type=type_key, faq=", ".join(c.get("faq_topics") or ["how it works", "size and fit", "care", "shipping and returns"]),
                       ship_days=store.get("ship_days"), free_over=store.get("free_shipping_over"), returns=store.get("returns_days"),
                       shot_keys=json.dumps(shots), shot_desc=shot_desc)
    text = f"Listing buyer: {gender}\nSupplier name: {info['name']}\nVariants: {[v['name'] for v in info['variants']][:40]}\nText: {info['desc'][:2500]}"

    def check(x):
        e = []
        if not x.get("title") or len(x["title"]) > 150:
            e.append(f"title length {len(x.get('title') or '')}")
        if not x.get("short_title") or len(x["short_title"]) > 40:
            e.append("short_title must be 1-40 chars")
        if x.get("short_title") in used:
            e.append("short_title already used")
        if len(x.get("tags") or []) != 13:
            e.append(f"tags {len(x.get('tags') or [])} != 13")
        if set((x.get("alt_by_key") or {})) != set(shots):
            e.append("alt_by_key keys wrong")
        if len(x.get("descriptionHtml") or "") < 800:
            e.append("description too short")
        bad = [w for w in ("CJ", "AliExpress", "dropship", "Alibaba", "Temu") if re.search(rf"\b{w}", json.dumps(x), re.I)]
        if bad:
            e.append(f"banned words {bad}")
        slop = rules.slop_hits(" ".join(str(x.get(k) or "") for k in ("title", "short_title", "descriptionHtml", "seo_title", "seo_description")))
        if slop:
            e.append(f"AI-slop wording, rewrite without: {slop}")
        t = x.get("title") or ""
        if gender == "men" and re.search(r"\bwomen", t, re.I) or gender == "women" and re.search(r"\bmen\b|\bmen's", t, re.I):
            e.append("wrong buyer word in title")
        return e
    if engine.rules() and not engine.allowed("copy"):
        out = _rules_copy(store, profile, item, info, gender, type_key, used, shots, c)
    else:
        try:
            out = ai.ask_json("copy", sys_, [{"type": "text", "text": text}] + ([] if engine.cli_mode() else ai.photos(info["images"], 3)), check=check, max_tokens=4000)
        except engine.NoAI:
            out = _rules_copy(store, profile, item, info, gender, type_key, used, shots, c)
        except Stop as e:
            if not engine.rules():
                raise
            log(f"  copy: Claude answer not usable ({e}); using the template")
            out = _rules_copy(store, profile, item, info, gender, type_key, used, shots, c)
        else:
            if engine.rules() and check(out):
                out = _rules_copy(store, profile, item, info, gender, type_key, used, shots, c)
    log(f"  copy: {out['short_title']} | {out['title'][:80]}")
    return out


def _rules_copy(store, profile, item, info, gender, type_key, used, shots, c) -> dict:
    faq = ["How long does shipping take?", "What if it arrives damaged or defective?",
           "How do I pick the right size?" if gender != "unisex" else "How do I care for it?"]
    return rulebook.copy(store.get("name") or "the store", profile["niche"], type_key, gender, item.get("colour_plain") or item.get("colour") or "",
                         info, list(shots), list(used), store.get("ship_days"), store.get("free_shipping_over"), store.get("returns_days"), faq)


PQ = """query($id:ID!){ product(id:$id){ id handle status variantsCount{ count } media(first:50){ nodes{ id alt status } }
  resourcePublicationsV2(first:30){ nodes{ isPublished publication{ id name } } } } }"""


def run(store: Store, log: Log, key: str, with_images: bool = False) -> dict:
    """One shelf item (key = pid8-gender) -> live listing. Returns the listing record."""
    profile, shelf = store.load("profile.json"), store.load("shelf.json") or {}
    item = (shelf.get("items") or {}).get(key)
    if not profile or not item:
        raise Stop(f"{key} is not on the shelf")
    if item["status"] != "READY":
        raise Stop(f"{key} is {item['status']}, not READY ({item.get('why')})")
    sh, cj = Shopify(store, log), CJ(store, log)
    for r in records(store, include_dry=False):
        if r.get("key") == key and r.get("status") in ("ACTIVE", "DRAFT") and not r.get("archived"):
            raise Stop(f"{key} is already listed as {r['sku']} ({r.get('status')})")
    if not store.dry_run:   # before any draft, link or paid photo: the store must have every sales channel
        want = store.get("channels") or ["Online Store"]
        missing = sorted(set(want) - {n for _, n in sh.publications()})
        if missing:
            raise Stop(f"sales channels not installed in this store: {missing} (install them or change channels in store.json)")
    gender = item["gender"]
    with _SKU_LOCK:   # number + record written together, so a parallel lane sees this SKU as taken
        sku = _next_sku(store, sh)
        rec = {"sku": sku, "key": key, "pid": item["pid"], "gender": gender, "started": now(), "dry_run": store.dry_run, "steps": {}, "status": "STARTED"}
        path = f"{records_dir(store)}/{sku}.json"
        store.save(path, rec)
    log(f"# Listing {sku} <- {item['name'][:70]} ({gender}) | ${item.get('price')} | {'DRY RUN' if store.dry_run else 'LIVE'}")
    pid_gid = None
    try:
        info = cj.product(item["pid"])
        chosen = [v for v in info["variants"] if v["vid"] in set(item.get("vids") or [])]
        if not chosen:
            raise Stop("the shelf's chosen variants are no longer on CJ (re-run catalog for this product)")
        rec["steps"]["supplier"] = f"{len(info['images'])} photos, {len(chosen)} variant(s) of '{item.get('colour')}'"
        log("  supplier: " + rec["steps"]["supplier"])
        with ThreadPoolExecutor(2) as ex:
            ff = ex.submit(_facts, store, profile, item, info, sku, gender, log)
            facts = ff.result()
            fc = ex.submit(_copy, store, profile, item, info, sku, gender, facts["type"], log)
            copy = fc.result()
        copy["tags"] = list(dict.fromkeys([t for t in copy["tags"] if t != f"type-{facts['type']}"] + [f"type-{facts['type']}"]))
        rec.update(facts=facts, copy=copy)
        store.save(path, rec)

        # ---- 4 draft + CJ link ----
        variants, opt = _clean_variants(chosen, gender, item.get("variant_match") or "")
        price = float(item["price"])
        vin = [{"optionValues": [{"optionName": opt, "name": v["clean"]}], "price": f"{price:.2f}",
                "compareAtPrice": (f"{item['compare_at']:.2f}" if item.get("compare_at") else None),
                "inventoryItem": {"sku": sku if len(variants) == 1 else f"{sku}-{i:02d}", "tracked": False}} for i, v in enumerate(variants, 1)]
        pc = {"title": copy["title"], "descriptionHtml": copy["descriptionHtml"], "vendor": store.get("name") or "Store", "status": "DRAFT",
              "productType": facts["type"], "tags": copy["tags"] + ["groundwork", f"gw-{sku.lower()}"], "productOptions": [{"name": opt, "values": [{"name": v["clean"]} for v in variants]}],
              "seo": {"title": copy.get("seo_title") or copy["short_title"], "description": copy.get("seo_description") or ""}}
        r = sh.write("create the DRAFT product", "mutation($p:ProductCreateInput!){ productCreate(product:$p){ product{ id } userErrors{ field message } } }",
                     {"p": pc}, "productCreate")
        rec["steps"]["draft"] = {"variants": [v["clean"] for v in variants], "option": opt, "price": price}
        if not store.dry_run:
            if not (r and (r.get("product") or {}).get("id")):
                raise Stop("productCreate returned no product")
            pid_gid = r["product"]["id"]
            rv = sh.write("add the variants", """mutation($id:ID!,$v:[ProductVariantsBulkInput!]!){ productVariantsBulkCreate(productId:$id, variants:$v, strategy:REMOVE_STANDALONE_VARIANT){
                productVariants{ id sku selectedOptions{ value } } userErrors{ field message } } }""", {"id": pid_gid, "v": vin}, "productVariantsBulkCreate")
            by = {x["selectedOptions"][0]["value"]: x for x in rv["productVariants"]}
            rec.update(shopify_id=pid_gid.rsplit("/", 1)[-1], status="DRAFT")
            store.save(path, rec)
            pairs = [{"cj_vid": v["vid"], "shop_vid": by[v["clean"]]["id"].rsplit("/", 1)[-1], "sku": by[v["clean"]]["sku"], "name": v["clean"]} for v in variants]
            link = cj.link(rec["shopify_id"], copy["title"], info["images"][0] if info["images"] else "", pairs, info["pid"], item["ship"]["method"], price,
                           origin=item["ship"].get("origin") or "CN")
            rec["steps"]["cj_link"] = link
            log(f"  CJ link: {'VERIFIED' if link['verified'] else 'NOT VERIFIED'} ({link.get('detail')})")
            if not link["verified"]:
                raise Stop("CJ link not verified (an unlinked product would take orders nobody ships)")
        else:
            rec["steps"]["cj_link"] = {"verified": None, "detail": f"dry run: would link {len(variants)} variant(s) via '{item['ship']['method']}'"}

        # ---- 5 images ----
        work = store.path("work", "dryrun", sku) if store.dry_run else store.path("work", sku)
        if work.exists():   # never reuse another run's reference photos
            import shutil
            shutil.rmtree(work)
        if with_images or not store.dry_run:
            IMG.fetch_refs(facts, work)
            n = int(re.sub(r"\D", "", sku) or 0)
            jobs = IMG.run_jobs(store, IMG.build_jobs(store, profile, facts, sku, n, work), log)
        else:
            jobs = [{**j, "status": "planned"} for j in IMG.build_jobs(store, profile, facts, sku, int(re.sub(r"\D", "", sku) or 0), work, plan_only=True)]
            log(f"  images: dry run, {len(jobs) or 'up to 5'} shot(s) planned, not generated (add --images to generate)")
        rec["images"] = [{k: j.get(k) for k in ("key", "status", "model", "issues", "out")} for j in jobs]
        store.save(path, rec)
        must = {"01-studio", "02-hero"}
        if (with_images or not store.dry_run) and any(j["key"] in must and j["status"] == "failed" for j in jobs):
            raise Stop("studio or hero photo failed to generate")
        flagged = [j["key"] for j in jobs if j["status"] == "flagged"]

        # ---- 6 + 7 go live ----
        gallery = [j for j in jobs if j["status"] in ("done", "flagged") and Path(j["out"]).exists()]
        if pid_gid:
            ups = [(j["key"], sh.staged_upload(Path(j["out"]), f"{sku.lower()}-{j['key']}.png"), copy["alt_by_key"].get(j["key"], "")) for j in gallery]
            if len(ups) < 3:
                raise Stop(f"only {len(ups)} photos ready; need at least 3")
            cm = sh.write("attach the photos", "mutation($p:ID!,$m:[CreateMediaInput!]!){ productCreateMedia(productId:$p, media:$m){ media{ id } mediaUserErrors{ field message } } }",
                          {"p": pid_gid, "m": [{"originalSource": u, "alt": al, "mediaContentType": "IMAGE"} for _, u, al in ups]}, "productCreateMedia")
            new_ids = [m["id"] for m in cm.get("media") or []]
            if len(new_ids) != len(ups):
                raise Stop(f"attached {len(new_ids)} of {len(ups)} photos")
            import time as _t
            ready = False
            for _ in range(30):
                nodes = sh.gql(PQ, {"id": pid_gid})["product"]["media"]["nodes"]
                ready = all(next((n["status"] for n in nodes if n["id"] == i), "") == "READY" for i in new_ids)
                if ready:
                    break
                _t.sleep(2)
            if not ready:
                raise Stop("photos never became READY in Shopify")
            sh.write("put our photos first", "mutation($id:ID!,$moves:[MoveInput!]!){ productReorderMedia(id:$id, moves:$moves){ mediaUserErrors{ field message } } }",
                     {"id": pid_gid, "moves": [{"id": i, "newPosition": str(k)} for k, i in enumerate(new_ids)]}, "productReorderMedia")
            ok_order = False
            for _ in range(20):
                nodes = sh.gql(PQ, {"id": pid_gid})["product"]["media"]["nodes"]
                ok_order = [n["id"] for n in nodes[:len(new_ids)]] == new_ids and [n["alt"] for n in nodes[:len(new_ids)]] == [al for _, _, al in ups]
                if ok_order:
                    break
                _t.sleep(2)
            if not ok_order:
                raise Stop("gallery order or alt text did not verify; product left DRAFT")
            old = [n["id"] for n in nodes if n["id"] not in set(new_ids)]
            if old:
                sh.write("remove the supplier photos", "mutation($id:ID!,$m:[ID!]!){ productDeleteMedia(productId:$id, mediaIds:$m){ deletedMediaIds mediaUserErrors{ field message } } }",
                         {"id": pid_gid, "m": old}, "productDeleteMedia")
            g = {"men": "male", "women": "female"}.get(gender, "unisex")
            mf = [{"namespace": "mm-google-shopping", "key": k, "type": "single_line_text_field", "value": v} for k, v in (("gender", g), ("age_group", "adult"), ("condition", "new"))]
            mf.append({"namespace": "custom", "key": "short_title", "type": "single_line_text_field", "value": copy["short_title"]})
            tags = copy["tags"] + ["groundwork", f"gw-{sku.lower()}"] + (["check-photos"] if flagged else [])
            sh.write("set Google fields, short title and tags", "mutation($p:ProductUpdateInput!){ productUpdate(product:$p){ userErrors{ field message } } }",
                     {"p": {"id": pid_gid, "metafields": mf, "tags": tags}}, "productUpdate")
            want = store.get("channels") or ["Online Store"]
            chan = [(i, n) for i, n in sh.publications() if n in want]
            missing = sorted(set(want) - {n for _, n in chan})
            if missing:
                raise Stop(f"sales channels not installed in this store: {missing}")
            if chan:
                sh.write(f"publish to {[n for _, n in chan]}", "mutation($id:ID!,$in:[PublicationInput!]!){ publishablePublish(id:$id, input:$in){ userErrors{ field message } } }",
                         {"id": pid_gid, "in": [{"publicationId": i} for i, _ in chan]}, "publishablePublish")
            sh.write("set ACTIVE", "mutation($p:ProductUpdateInput!){ productUpdate(product:$p){ userErrors{ field message } } }",
                     {"p": {"id": pid_gid, "status": "ACTIVE"}}, "productUpdate")
            back = sh.gql(PQ, {"id": pid_gid})["product"]
            url, code = sh.storefront_ok(back["handle"])
            pubs = {n["publication"]["name"] for n in back["resourcePublicationsV2"]["nodes"] if n["isPublished"]}
            live = back["status"] == "ACTIVE" and code == 200 and set(want) <= pubs
            rec.update(status=back["status"], url=url, storefront_http=code, channels=sorted(pubs), photos=len(ups), flagged_photos=flagged, live=live, finished=now())
            log(f"  {'LIVE and verified' if live else 'ACTIVE but a check failed'}: {url} -> HTTP {code} | channels {sorted(pubs)} | {len(ups)} photos" + (f" | check-photos: {flagged}" if flagged else ""))
        else:
            rec.update(status="DRY_RUN_OK", finished=now(), would_write=[w["what"] for w in sh.writes])
            log(f"  DRY RUN complete: {len(sh.writes)} Shopify write(s) planned; nothing sent")
        store.save(path, rec)
        return rec
    except Exception as e:  # noqa: BLE001 - every failure is recorded, and the leftover draft archived
        rec.update(status="FAILED", error=str(e)[:500], failed=now())
        if not isinstance(e, Stop):
            rec["trace"] = traceback.format_exc()[-1500:]
        log(f"  FAILED: {e}")
        if not store.dry_run and sh.configured:
            try:   # every DRAFT carrying this run's tag (also one a timed-out create may have left behind)
                found = (sh.gql('query($q:String!){ products(first:10, query:$q){ nodes{ id status } } }', {"q": f"tag:gw-{sku.lower()}"}).get("products") or {}).get("nodes") or []
                ids = {n["id"] for n in found if n["status"] == "DRAFT"} | ({pid_gid} if pid_gid and not found else set())
                for i in ids:
                    sh.gql("mutation($p:ProductUpdateInput!){ productUpdate(product:$p){ userErrors{ message } } }", {"p": {"id": i, "status": "ARCHIVED"}})
                if ids:
                    rec["archived"] = True
                    log(f"  leftover draft(s) ARCHIVED ({len(ids)}), never deleted")
            except Exception as e2:  # noqa: BLE001
                log(f"  could not archive the draft: {e2}")
        store.save(path, rec)
        return rec


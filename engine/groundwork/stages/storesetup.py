"""Stage 5, Store setup: everything in Shopify that is not a product, from the brand kit and store settings.

  policies     refund, shipping, privacy, terms, contact (shopPolicyUpdate), written by Claude from the store's real
               settings so the checkout, the policies, the FAQ and every listing say the same thing (Merchant Center checks this)
  pages        About, FAQ, Shipping, Returns, Contact (pageCreate; skipped if a page with that handle exists)
  collections  one smart collection per profile type (tag rule type-<type>) + "New arrivals" (tag groundwork)
  menu         main menu = Home, each collection, About, FAQ, Contact (the theme footer shows the policies)
  checklist    the theme colours/fonts from the brand kit and the steps only Jason can do (payments, domain, apps)
Settings that matter here (store.json): free_shipping_over, returns_days, returns_policy (standard | defective_only),
ship_days, support_email, business_city (shown publicly; never the street address).
Writes stores/<slug>/store_setup.json. Dry run logs every write instead of sending it.
"""
from __future__ import annotations

import json

from ..lib import ai, engine, rulebook, rules
from ..lib.core import Log, Stop, Store, now, slugify
from ..lib.shopify import Shopify

APP_SCOPES = ("read_products, write_products, read_inventory, write_inventory, read_locations, read_publications, write_publications, "
              "write_online_store_pages, write_online_store_navigation, write_legal_policies, read_themes, write_themes, read_orders, read_reports")
POLICY_TYPES = {"refund": "REFUND_POLICY", "shipping": "SHIPPING_POLICY", "privacy": "PRIVACY_POLICY", "terms": "TERMS_OF_SERVICE", "contact": "CONTACT_INFORMATION"}

WRITE = """You write the legal and help pages for a small US online store. Facts (use exactly these, invent nothing):
{facts}
Answer JSON with HTML bodies (simple <h2>/<p>/<ul>, no styles):
{{"refund": "...", "shipping": "...", "privacy": "...", "terms": "...", "contact": "...",
  "faq": "8-10 <h3>question</h3><p>answer</p> pairs a shopper of this niche asks (shipping, returns, sizing/fit or use, care, payment, order tracking, contact)",
  "about": "the About page from the brand kit text, lightly formatted"}}
Rules: plain words, short paragraphs. Shipping and returns wording must match the facts exactly (Google Merchant Center
compares them with checkout). No fake guarantees, no founder story, no years in business, no street address. Products ship
from overseas partner warehouses: say delivery times honestly. These are templates for a small business, not legal advice.
""" + rules.COPY_VOICE.replace("{", "{{").replace("}", "}}")


def run(store: Store, log: Log) -> dict:
    brand, profile = store.load("brand.json") or {}, store.load("profile.json")
    if not profile and (engine.rules() or engine.solo_max()):
        from . import profile as _prof
        profile = _prof.run(store, log)
    if not profile:
        raise Stop("run profile first")
    if not brand.get("name"):
        raise Stop("pick a brand name first (gw.py brand --pick NAME)")
    sh = Shopify(store, log)
    kit = brand.get("kit") or {}
    out = store.load("store_setup.json") or {}
    log(f"# Store setup: {brand['name']} ({store.get('shop_domain') or 'no shop yet'}) {'DRY RUN' if store.dry_run else 'LIVE'}")

    facts = {
        "store": brand["name"], "website": brand.get("domain"), "sells": profile["niche"], "support_email": store.get("support_email") or "(set support_email)",
        "business_city": store.get("business_city") or "(set business_city)", "ships_to": "United States",
        "delivery": f"{store.get('ship_days')} after dispatch; orders are processed in 1-3 business days",
        "shipping_price": f"free on orders over ${store.get('free_shipping_over')}; a flat standard rate below that (set in Shopify checkout)",
        "returns": ("defective, damaged or not-as-described items reported within 7 days of delivery with photos: replacement or full refund; no change-of-mind returns"
                    if store.get("returns_policy") == "defective_only" else
                    f"{store.get('returns_days')}-day returns on unused items in original condition; refunds to the original payment method within 5-10 business days of approval"),
        "about_text": kit.get("about", ""), "tagline": kit.get("tagline", ""),
    }
    if not out.get("texts") and engine.rules():   # Beta: policy and help pages from fixed templates and the real settings
        out["texts"] = rulebook.store_pages(facts)
        out["facts"] = facts
        store.save("store_setup.json", out)
    if not out.get("texts"):
        out["texts"] = ai.ask_json("store_pages", WRITE.format(facts="\n".join(f"- {k}: {v}" for k, v in facts.items())), "Write them.", max_tokens=9000,
                                   check=lambda a: [f"missing {k}" for k in list(POLICY_TYPES) + ["faq", "about"] if not (a or {}).get(k)]
                                   + ([f"AI-slop wording, rewrite without: {h}"] if (h := rules.slop_hits(json.dumps(a or {}))) else []))
        out["facts"] = facts
        store.save("store_setup.json", out)
    T = out["texts"]

    # policies
    for k, typ in POLICY_TYPES.items():
        sh.write(f"set the {k} policy", "mutation($p:ShopPolicyInput!){ shopPolicyUpdate(shopPolicy:$p){ userErrors{ field message } } }",
                 {"p": {"type": typ, "body": T[k]}}, "shopPolicyUpdate")
    # pages
    have = {n["handle"]: n["id"] for n in ((sh.read("query{ pages(first:100){ nodes{ id handle } } }").get("pages") or {}).get("nodes") or [])}
    pages = {"about": ("About", T["about"]), "faq": ("FAQ", T["faq"]), "shipping": ("Shipping", T["shipping"]), "returns": ("Returns & Refunds", T["refund"]),
             "contact": ("Contact", T["contact"])}
    page_ids = {}
    for handle, (title, body) in pages.items():
        if handle in have:
            log(f"  page /{handle} exists, left alone")
            page_ids[handle] = have[handle]
            continue
        r = sh.write(f"create page /{handle}", "mutation($p:PageCreateInput!){ pageCreate(page:$p){ page{ id } userErrors{ field message } } }",
                     {"p": {"title": title, "handle": handle, "body": body, "isPublished": True}}, "pageCreate")
        if r:
            page_ids[handle] = r["page"]["id"]
    # collections (smart, by type tag)
    cols = [{"title": c["title"], "tag": c["tag"]} for c in profile.get("collections") or []] + [{"title": "New Arrivals", "tag": "groundwork"}]
    have_c = {n["handle"]: n["id"] for n in ((sh.read("query{ collections(first:100){ nodes{ id handle } } }").get("collections") or {}).get("nodes") or [])}
    col_ids = {}
    for c in cols:
        h = slugify(c["title"])
        if h in have_c:
            col_ids[c["title"]] = have_c[h]
            continue
        r = sh.write(f"create collection '{c['title']}' (tag {c['tag']})", "mutation($c:CollectionInput!){ collectionCreate(input:$c){ collection{ id } userErrors{ field message } } }",
                     {"c": {"title": c["title"], "handle": h, "ruleSet": {"appliedDisjunctively": False, "rules": [{"column": "TAG", "relation": "EQUALS", "condition": c["tag"]}]}}},
                     "collectionCreate")
        if r:
            col_ids[c["title"]] = r["collection"]["id"]
            online = [i for i, n in sh.publications() if n == "Online Store"]
            if online:   # a new collection is not on the storefront until published
                sh.write(f"publish collection '{c['title']}'", "mutation($id:ID!,$in:[PublicationInput!]!){ publishablePublish(id:$id, input:$in){ userErrors{ field message } } }",
                         {"id": r["collection"]["id"], "in": [{"publicationId": online[0]}]}, "publishablePublish")
    # main menu
    items = [{"title": "Home", "type": "FRONTPAGE", "url": "/"}] + [{"title": t, "type": "COLLECTION", "resourceId": i} for t, i in col_ids.items()]
    items += [{"title": t, "type": "PAGE", "resourceId": i} for t, i in ((pages[h][0], page_ids[h]) for h in ("about", "faq", "contact") if h in page_ids)]
    menus = (sh.read("query{ menus(first:10){ nodes{ id handle } } }").get("menus") or {}).get("nodes") or []
    main = next((m for m in menus if m["handle"] == "main-menu"), None)
    if main:
        sh.write("set the main menu", "mutation($id:ID!,$t:String!,$items:[MenuItemUpdateInput!]!){ menuUpdate(id:$id, title:$t, items:$items){ userErrors{ field message } } }",
                 {"id": main["id"], "t": "Main menu", "items": items}, "menuUpdate")
    else:
        log(f"  main menu not found{' (dry run)' if store.dry_run else ''}; planned items: {[i['title'] for i in items]}")

    out["checklist"] = checklist(store, brand)
    out["done"] = now()
    out["writes"] = [w["what"] for w in sh.writes]
    store.save("store_setup.json", out)
    log(f"\n{len(sh.writes)} store writes {'planned' if store.dry_run else 'sent'}. Jason's checklist:")
    for c in out["checklist"]:
        log(f"  [ ] {c}")
    log(ai.usage_line())
    return out


def checklist(store: Store, brand: dict) -> list[str]:
    ms = [r["domain"] for r in ((store.load("model_stores.json") or {}).get("stores") or [])[:3]]
    kit = brand.get("kit") or {}
    pal = kit.get("palette") or {}
    fonts = kit.get("fonts") or {}
    return [
        f"Buy the domain {brand.get('domain')} (GoDaddy or Shopify Domains) and connect it in Shopify > Settings > Domains; then set public_domain in store.json",
        "Shopify plan: start on the $1 offer (Advanced), switch down before the offer ends",
        "Shopify Payments: finish business details and payouts (your ID and bank)",
        *([f"Model the homepage and product page layout on: {', '.join(ms)} (stores/<slug>/model_stores.json has why)"] if ms else []),
        f"Theme > Customize: colours primary {pal.get('primary')} accent {pal.get('accent')} background {pal.get('background')} text {pal.get('text')}; "
        f"fonts {fonts.get('heading')} / {fonts.get('body')}; logo = the name '{brand.get('name')}' in the heading font",
        f"Checkout shipping: standard flat rate + free over ${store.get('free_shipping_over')} (must match the shipping policy)",
        "Install the CJdropshipping app and connect this store; then run `gw.py cj-shops` and put the id in store.json (cj_shop_id)",
        "Install Google & YouTube; connect Merchant Center (business info = city only publicly) and Google Ads (your payment)",
        f"Create the Dev Dashboard app for this store with scopes {APP_SCOPES}; in GitHub add an environment named {store.slug} with secrets GW_SHOPIFY_CLIENT_ID and GW_SHOPIFY_CLIENT_SECRET",
        "Place one real test order end to end before ads go on",
    ]

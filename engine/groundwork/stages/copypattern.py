"""Solo step 5: the store's product-copy pattern. Strict: our copy system decides; the user may add parameters, which are
applied only inside the rules (lib/rules: banned words, title rules, facts only from the supplier spec sheet).
Writes copy.json; listing copy reads it as extra guidance.
"""
from __future__ import annotations

from ..lib import ai, engine, rulebook, rules
from ..lib.core import Log, Stop, Store, now

SYSTEM = """You write the PRODUCT COPY PATTERN for a US Shopify store sold through Google Shopping ads. Our copy system:
- Google title: product type FIRST, then the 2 features shoppers search, then who/where it is for, then colour/size.
  150 characters max. Words shoppers type, no brand fluff.
- Site title: short and clean, e.g. "[Type] in [colour]".
- Description: one line on what it is and who it is for, then 4-6 facts a shopper can check.
- FAQ: the 2-3 questions that stop a purchase for THIS niche, answered with real numbers from the policy/spec sheet.
- Facts only from the supplier spec sheet; never invent specs, reviews, certifications or delivery times.
""" + rules.COPY_VOICE + """
Answer JSON for this store:
{"title": "the Google title pattern with [slots], e.g. [Bed type], [2 key features], for [dog size], [colour]",
 "title_example": "one filled-in example title for a typical product of this store",
 "site": "site title pattern", "desc": ["the lead-line rule", "the 4-6 fact types for this niche, as one line"],
 "fact_types": ["5-6 fact types a shopper checks for this niche"], "faq": ["2-3 FAQ questions"],
 "voice": ["3 short bullets on this store's tone"], "applied": ["how each user parameter was applied, or why it was not (it would break a rule)"]}"""


def run(store: Store, log: Log, params: str = "") -> dict:
    g = store.load("gate.json")
    if not g or not g.get("ok"):
        raise Stop("pass the direction gate first")
    ref = store.load("reference.json") or {}
    ask = (f"Store niche: {g['niche']}. Product types: {', '.join(g['types'])}. Shopper split: {g.get('split') or 'none'}.\n"
           f"Reference store best sellers: {', '.join(b['title'] for b in (ref.get('best_sellers') or [])[:6]) or 'n/a'}.\n"
           f"User parameters: {params or 'none'}")
    if engine.rules():   # Beta: the fixed pattern from the store's types and category
        ans = rulebook.copy_pattern(g["niche"], g["types"], g.get("category") or "other", g.get("split") or "", params)
        out = {"at": now(), "niche": g["niche"], "params": params, **ans}
        store.save("copy.json", out)
        log(f"# Copy pattern (rules): {g['niche']}\ntitle: {ans['title']}\nexample: {ans['title_example']}\nfaq: {'; '.join(ans['faq'])}")
        return out
    ans = ai.ask_json("copy_pattern", SYSTEM, ask, max_tokens=1500,
                      check=lambda a: (["missing title/site/faq"] if not all((a or {}).get(k) for k in ("title", "site", "faq")) else [])
                      + ([f"title example over 150 characters"] if len(str((a or {}).get("title_example") or "")) > 150 else [])
                      + ([f"AI-slop wording, rewrite without: {h}"] if (h := rules.slop_hits(str(a))) else []))
    out = {"at": now(), "niche": g["niche"], "params": params, **ans}
    store.save("copy.json", out)
    log(f"# Copy pattern: {g['niche']}\ntitle: {ans['title']}\nexample: {ans.get('title_example')}\nsite: {ans['site']}\nfaq: {'; '.join(ans['faq'])}")
    log(ai.usage_line())
    return out

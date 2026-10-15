"""Solo step 1: the direction gate. A GENERAL niche passes only when the real numbers say a 50-product store works.

  1. Shape   - Claude widens a too-specific niche to its general store ("floor lamps" -> "Indoor Lamps"), blocks what
               Google Ads will not run, names 3 product types to test, CJ search words, the season, and 3 nearby niches.
  2. Supply  - CJ: distinct real matches across the search words (need SUPPLY_MIN).
  3. Market  - real US ad prices per product type (market.collect: SerpAPI when set, else Claude web search), and how
               many different stores sell it (need MIN_STORES).
  4. Profit  - the EXACT live formula (lib/pricing.decide: market price, markup curve, x9.99 up, cap at the top
               mainstream ad, 10% code, fees, ad cost, floor $20 and 25%) on SAMPLE real CJ products with their real
               freight. Pass only when enough clear the floor to fill a 50-product shelf.
  Season is a warning, never a block. Writes gate.json; the Solo page shows every check with its one-line reason.
"""
from __future__ import annotations

import re

from ..lib import ai, demand, engine, rulebook, trends, verdict, market, pricing
from ..lib.core import Log, Stop, Store, now
from .scout import SHIP_GUESS, SUPPLY_MIN, _stem, cj_client

MIN_STORES = 5        # different stores selling it in the ads we read
SAMPLE = 20           # CJ products priced for real
SHELF = 50
MIN_PASS_SHARE = 0.30  # at least 30% of the sample must clear the floor, and the supply must hold 50 of them

BLOCKED = ("anything eaten, drunk or swallowed (food, drinks, supplements, vitamins, CBD), injectables, medicines and "
           "medical devices, weapons and weapon parts, vapes/tobacco/nicotine, adult products, and anything else Google "
           "Shopping ads restrict")

SHAPE = """You set up a US dropshipping store direction for Groundwork Solo. Stores are GENERAL niches: one store a shopper
would recognise ("Indoor Lamps", "Heated Apparel", "Dog Beds & Blankets"), never one style, colour or material.
Answer JSON:
{"niche": "the general store name, 2-4 words",
 "widened_from": "what the user typed if you widened it, else empty",
 "blocked": "one-line reason if the niche is in a blocked category, else empty",
 "category": "apparel|home|other",
 "split": "the natural shopper split if the niche has one (e.g. 'men / women', 'dog / cat'), else empty",
 "types": ["the 3 product types that make up most of this store, 1-3 words each, as shoppers search them"],
 "cj_keywords": ["5 short supplier-catalog search words covering the store"],
 "season": "the months it mostly sells if strongly seasonal (e.g. 'Oct-Feb'), else empty",
 "nearby": ["3 other GENERAL niches for the same kind of buyer"]}
Widen only when the input names one product type or a style ("floor lamps" -> "Indoor Lamps"); keep a niche that is
already a store. Blocked: """ + BLOCKED + "."


def root(w: str) -> str:
    """Rough word root so 'heated'/'heating', 'jacket'/'jackets', 'lamps'/'lamp' match each other."""
    w = w.lower()
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and not w.endswith("ss"):
            return w[: -len(suf)]
    return w


def has_all(name: str, words: list[str]) -> bool:
    roots = {root(x) for x in re.findall(r"[a-z]+", name.lower())}
    return all(root(w) in roots for w in words)


def singular(t: str) -> str:
    return " ".join(root(w) if w.lower().endswith("s") and not w.lower().endswith("ss") else w.lower() for w in t.split())


def _shape_ok(a) -> list[str]:
    errs = []
    for k in ("niche", "category", "types", "cj_keywords", "nearby"):
        if not a.get(k):
            errs.append(f"missing {k}")
    if a.get("category") not in ("apparel", "home", "other"):
        errs.append("category must be apparel|home|other")
    if len(a.get("types") or []) < 2:
        errs.append("need 3 product types")
    return errs


def shape(niche: str, types: list[str] | None = None, category: str = "") -> dict:
    if engine.rules():   # Beta: the user names the store and its 3 product types (lesson 1)
        if len([t for t in types or [] if t.strip()]) < 2:
            raise Stop("add your 3 product types (e.g. heated jackets, heated vests, heated gloves)")
        return rulebook.shape(niche, types or [], category)
    return ai.ask_json("gate_shape", SHAPE, f"Niche typed: {niche}", max_tokens=900, check=_shape_ok)


def _supply(cj, kws: list[str], hi: float, log: Log) -> dict:
    found: dict[str, dict] = {}
    per = {}
    for kw in kws[:8]:
        words = [_stem(w) for w in re.findall(r"[a-z]+", kw.lower()) if len(w) > 3] or [kw.lower()]
        rows = cj.search(kw, size=100, min_price=2, max_price=hi)
        hits = [r for r in rows if any(re.search(rf"\b{re.escape(w)}", r["name"].lower()) for w in words)]
        per[kw] = len(hits)
        for r in hits:
            found.setdefault(r["pid"], r)
        log(f"  CJ '{kw}': {len(hits)} matching")
    return {"n": len(found), "by_keyword": per, "rows": list(found.values())}


def _type_of(name: str, types: list[str]) -> str:
    n = name.lower()
    best, score = types[0], 0
    for t in types:
        ws = [_stem(w) for w in re.findall(r"[a-z]+", t.lower()) if len(w) > 2]
        s = sum(1 for w in ws if re.search(rf"\b{re.escape(w)}", n))
        if s > score:
            best, score = t, s
    return best


CLASSIFY = """You sort supplier products for a US store. For each numbered product name, answer which of the store's
product types it is, or "" when it is not one of them (a different product, an accessory, a part, a toy, a bundle).
Answer JSON: {"items": [{"i": 0, "type": "type or empty"}, ...]} with one entry per product number (the number before the dot),
and the type copied EXACTLY from the store's list.
Be strict: the product must PRIMARILY be that type, the way a shopper searching that type expects it. A night
light, projector, speaker, charger or novelty gadget with a light is not a table lamp; a flashlight is not a desk lamp. """


def classify(niche: str, types: list[str], rows: list[dict]) -> list[str]:
    """Type for each row ("" = not one of the store's types). Asked in chunks of 30 by index, so a miscounted
    answer can never shift types onto the wrong products; anything the model leaves out counts as off-type."""
    def norm(x: str) -> str:
        return " ".join(_stem(w) for w in re.findall(r"[a-z]+", str(x).lower()))
    ok = {norm(t): t for t in types}
    if engine.rules() and not engine.allowed("gate_classify"):
        return rulebook.classify(types, rows)
    out: list[str] = []
    for c in range(0, len(rows), 30):
        chunk = rows[c:c + 30]
        names = "\n".join(f"{i}. {r['name'][:110]}" for i, r in enumerate(chunk))
        try:
            ans = ai.ask_json("gate_classify", CLASSIFY, f"Store: {niche}. Types: {', '.join(types)}.\n{names}", max_tokens=1800,
                              check=lambda a: [] if isinstance(a, dict) and isinstance(a.get("items"), list) else ["need items"])
        except engine.NoAI:
            out += rulebook.classify(types, chunk)   # Beta: Claude not connected or over its cap -> word rules
            continue
        got = {}
        for x in ans["items"]:
            try:
                got[int(x.get("i"))] = ok.get(norm(x.get("type") or ""), "")
            except (TypeError, ValueError, AttributeError):
                continue
        out += [got.get(i, "") for i in range(len(chunk))]
    return out


def price_rules(store: Store) -> dict:
    """New stores show real prices: the compare-at ("was") price is opt-in (store setting compare_at, off by default)."""
    return {"compare_at_offset": 0.0 if store.get("compare_at") is False else 30.0}


def override(store: Store, log: Log, reason: str) -> dict:
    """Jason keeps a niche the verdict flagged (demand or trend). Recorded with the reason; the live-formula margin
    check and the gate's own checks still apply."""
    g = store.load("gate.json")
    if not g or not g.get("verdict"):
        raise Stop("run the direction gate first")
    if g["verdict"]["badge"] != "fail":
        log("verdict already passes: nothing to override")
        return g
    if "margin" in g["verdict"]["fails"]:
        raise Stop("the margin check failed: that one is the profit floor and cannot be overridden")
    g["verdict"]["override"] = {"at": now(), "reason": reason or "Jason's call"}
    store.save("gate.json", g)
    log(f"override recorded for {g['niche']}: {reason or 'Jason' + chr(39) + 's call'}")
    return g


def run(store: Store, log: Log, niche: str, types: list[str] | None = None, category: str = "") -> dict:
    log(f"# Direction gate: {niche}")
    prev = store.load("gate.json") or {}
    if engine.rules():
        sh = shape(niche, types or prev.get("types"), category or prev.get("category") or "")
    elif prev.get("typed", "").strip().lower() == niche.strip().lower() and prev.get("types"):
        # same niche checked before: keep the same store shape so the verdict only moves when the market does
        sh = {k: prev.get(k) for k in ("niche", "widened_from", "category", "split", "types", "cj_keywords", "season", "nearby")}
        sh["blocked"] = ""
        log("same niche as last time: product types locked to " + ", ".join(sh["types"]))
    else:
        sh = shape(niche)
    out = {"typed": niche, "niche": sh["niche"], "widened_from": sh.get("widened_from") or "", "category": sh["category"],
           "split": sh.get("split") or "", "types": sh["types"][:3], "cj_keywords": sh["cj_keywords"][:5],
           "season": sh.get("season") or "", "nearby": sh.get("nearby") or [], "checked": now(), "checks": {}}
    if sh.get("blocked"):
        out.update(ok=False, reason=f"Blocked: {sh['blocked']}")
        out["checks"]["policy"] = {"ok": False, "text": sh["blocked"]}
        store.save("gate.json", out)
        log(out["reason"])
        return out
    out["checks"]["policy"] = {"ok": True, "text": "Google Ads allows it"}
    if out["widened_from"]:
        log(f"widened '{out['widened_from']}' -> '{out['niche']}'")

    # market: real ad prices per product type (2 searches each, the formula needs 2+ searches with 3+ ads)
    ads_by_type: dict[str, dict] = {}
    sellers: set[str] = set()
    for t in out["types"]:
        qs = [t, f"{t} for home" if out["category"] == "home" else f"best {t}", f"{t} sale"]   # 3 searches: steadier market numbers
        if engine.rules():
            qs = qs[:2]   # Beta: 2 searches per type (the formula's minimum) to keep the user's Claude use small
        rows = {}
        for q in qs:
            try:
                got = market.serpapi(q) if market.serpapi_ready() else market.web(q)
            except engine.NoAI as e:
                raise Stop(f"market prices need your Claude connected (step 0): {e}")
            rows[q] = got
            sellers |= {r[0].split("|")[-1].strip().lower() for r in got if "|" in r[0]}
            log(f"  ads '{q}': {len(got)} prices" + (f" ${min(p for _, p in got):.0f}-${max(p for _, p in got):.0f}" if got else ""))
        ads_by_type[t] = {"niches": rows, "source": "serpapi" if market.serpapi_ready() else "web", "collected": now()}
    sellers.discard("")
    m_ok = len(sellers) >= MIN_STORES
    out["checks"]["market"] = {"ok": m_ok, "stores": len(sellers),
                               "text": f"{len(sellers)} different stores selling it" + ("" if m_ok else f" (need {MIN_STORES})")}
    out["market"] = {t: {"M": pricing.market(a, pricing.rules_for())[0], "H": pricing.market(a, pricing.rules_for())[1],
                         "searches": {q: [[r[0][:60], r[1]] for r in rows[:8]] for q, rows in a["niches"].items()}}
                     for t, a in ads_by_type.items()}
    tops = [v["H"] for v in out["market"].values() if v["H"]]
    hi = max(15.0, round(max(tops) * 0.6, 2)) if tops else 80.0

    # supply: per product type, CJ products whose name holds every word of the type ("heated" AND "jacket")
    cj = cj_client(store, log)
    if cj is None:
        raise Stop("CJ_API_KEY is not set: the gate needs real supply and costs")
    strict: dict[str, dict] = {}
    by_type: dict[str, int] = {}
    for t in out["types"]:
        need = [w for w in re.findall(r"[a-z]+", t.lower()) if len(w) > 2]
        rows = cj.search(singular(t), size=100, min_price=2, max_price=hi)
        hits = [r for r in rows if has_all(r["name"], need)]
        by_type[t] = len(hits)
        for r in hits:
            strict.setdefault(r["pid"], {**r, "_type": t})
        log(f"  CJ '{t}': {len(hits)} with every word of the type")
    n = len(strict)
    s_ok = n >= SUPPLY_MIN
    out["checks"]["supply"] = {"ok": s_ok, "n": n, "by_type": by_type,
                               "text": f"{n} real products on CJ (" + ", ".join(f"{k} {v}" for k, v in by_type.items()) + ")" + ("" if s_ok else f", need {SUPPLY_MIN}")}

    # the sample: drop accessories/parts the word match lets through (a heated jacket BATTERY is not a heated jacket)
    cand = sorted(strict.values(), key=lambda r: -r["stores"])[:45]
    typed = classify(out["niche"], out["types"], cand)
    on = [(r, t) for r, t in zip(cand, typed) if t]
    real_share = len(on) / len(cand) if cand else 0
    log(f"  {len(on)} of {len(cand)} checked are really the product (not an accessory or part)")

    # profit: the live formula on real products with real freight
    rows = on[:SAMPLE]
    priced, passed = [], 0
    for r, t in rows:
        try:
            det = cj.product(r["pid"])
            vids = [v["vid"] for v in det["variants"] if v.get("vid")]
            fr = cj.freight(vids[:1]) if vids else None
        except Stop as e:
            log(f"  skip {r['pid']}: {e}")
            continue
        cost = r.get("price") or 0
        ship = fr["price"] if fr else SHIP_GUESS
        landed = round(cost + ship, 2)
        d = pricing.decide(landed, out["category"], ads_by_type[t], price_rules(store))
        pv = verdict.product(d)
        ok = pv["badge"] == "pass"
        passed += ok
        priced.append({"pid": r["pid"], "name": r["name"][:80], "type": t, "cost": cost, "ship": ship, "ship_days": fr["days"] if fr else None,
                       "landed": landed, "image": r.get("image"), "cj_stores": r["stores"], "decision": d.get("decision"),
                       "price": d.get("price"), "keep": d.get("net_after_code"), "keep_pct": d.get("net_pct_after_code"),
                       "M": d.get("M"), "H": d.get("market_high"), "F": d.get("F"), "reason": d.get("reason", "") or pv["why"],
                       "stress_pct": d.get("stress_pct_after_code"), "badge": pv["badge"], "aim": pv["aim"]})
        log(f"  {r['name'][:46]:46} landed ${landed:6.2f} -> {d.get('decision')} {('$' + str(d.get('price'))) if ok else (d.get('reason') or pv['why'])[:60]}")
    share = passed / len(priced) if priced else 0
    est = int(share * n * real_share)
    p_ok = bool(priced) and share >= MIN_PASS_SHARE and est >= SHELF
    if not priced:
        ptxt = "no product could be priced (no market data)"
    elif p_ok:
        ptxt = f"{passed} of {len(priced)} real products clear the floor -> about {est} of {n} on CJ would"
    else:
        worst = next((p["reason"] for p in priced if p["badge"] != "pass" and p["reason"]), "")
        ptxt = f"only {passed} of {len(priced)} clear the floor (~{est} of {n} on CJ, need {SHELF})" + (f": {worst}" if worst else "")
    out["checks"]["profit"] = {"ok": p_ok, "passed": passed, "sampled": len(priced), "estimate": est, "text": ptxt}
    out["sample"] = priced
    out["checks"]["season"] = {"ok": True, "warn": bool(out["season"]),
                               "text": f"sells mostly {out['season']}" if out["season"] else "sells all year"}
    out["ok"] = all(out["checks"][k]["ok"] for k in ("policy", "market", "supply", "profit"))
    fails = [out["checks"][k]["text"] for k in ("market", "supply", "profit") if not out["checks"][k]["ok"]]
    out["reason"] = "" if out["ok"] else "; ".join(fails)
    # Scout checks 1-3: Keyword Planner demand, 5-year trend, one verdict badge (fail soft: never blocks the run)
    out["demand"] = demand.check(out["niche"], out["types"], store)
    out["trend"] = trends.check(out["niche"])
    if engine.rules() and out["trend"].get("tag") == "seasonal" and out["trend"].get("peaks"):   # Beta: the season comes from Trends
        out["season"] = " ".join(out["trend"]["peaks"])
        out["checks"]["season"] = {"ok": True, "warn": True, "text": f"sells mostly {out['season']} (Google Trends)"}
    out["verdict"] = verdict.niche(out["demand"], out["trend"], verdict.margin_from_gate(out))
    if prev.get("typed", "").strip().lower() == niche.strip().lower() and (prev.get("verdict") or {}).get("override"):
        out["verdict"]["override"] = prev["verdict"]["override"]   # Jason's override stands for the same niche
    store.save("gate.json", out)
    log(f"\n{'PASS' if out['ok'] else 'BLOCKED'}: {out['niche']}" + (f" - {out['reason']}" if out["reason"] else ""))
    for k in ("market", "supply", "profit", "season"):
        log(f"  {k:7} {out['checks'][k]['text']}")
    log(f"  demand  {out['demand']['text']}\n  trend   {out['trend']['text']}\n  verdict {out['verdict']['badge'].upper()}"
        + (f" - {out['verdict']['why']}" if out['verdict']['why'] else ""))
    log(ai.usage_line())
    return out


SUGGEST = """Suggest GENERAL niches for a new US dropshipping store sold through Google Shopping ads (Groundwork Solo).
Rules: everyday consumer products many people already buy online; a crowded market is good (proof it sells); typical
ad price $70-$250: with about $40 of ad cost per sale and a $20 profit floor, cheaper markets cannot work; products CJdropshipping carries plenty of (small, not fragile, not
installed, not branded). Never blocked: """ + BLOCKED + """.
Each is a whole store a shopper recognises, never a style. Answer JSON:
{"niches": [{"niche": "2-4 words", "why": "who buys it, max 8 words", "category": "apparel|home|other",
             "cj_keywords": ["3 short supplier search words"], "season": "months if strongly seasonal, else empty"}]}"""


def suggest(store: Store, log: Log, n: int = 6, avoid: list[str] | None = None) -> dict:
    """5-6 general niches with a real CJ supply count each (cheap); the full gate runs when one is picked."""
    ask = f"Give {n} niches. Today: {now()}." + (f" Do not repeat: {', '.join(avoid)}." if avoid else "")
    ans = ai.ask_json("gate_suggest", SUGGEST, ask, web=4, max_tokens=1600,
                      check=lambda a: [] if len((a or {}).get("niches") or []) >= n - 1 else [f"need {n} niches"])
    cj = cj_client(store, log)
    out = []
    for x in ans["niches"][:n]:
        sp = _supply(cj, x.get("cj_keywords") or [x["niche"]], 80.0, log) if cj else {"n": None}
        dm = demand.check(x["niche"], x.get("cj_keywords") or [], store)
        tr = trends.check(x["niche"])
        sup = {"status": "unavailable" if sp["n"] is None else "pass" if sp["n"] >= SUPPLY_MIN else "fail",
               "text": f"Supply: {sp['n']} on CJ" + ("" if (sp["n"] or 0) >= SUPPLY_MIN else f" (need {SUPPLY_MIN})")}
        v = verdict.niche(dm, tr, None)
        if sup["status"] == "fail":
            v.update(badge="fail", fails=v["fails"] + ["supply"], why="; ".join(x for x in (v["why"], sup["text"]) if x))
        out.append({**x, "supply": sp["n"], "supply_ok": (sp["n"] or 0) >= SUPPLY_MIN, "demand": dm,
                    "trend": {k: tr.get(k) for k in ("status", "tag", "peaks", "text", "series", "change", "error")}, "verdict": v})
        log(f"{x['niche']:30} CJ {sp['n']} | {dm['text']} | {tr['text']}{(' (' + tr['error'] + ')') if tr.get('error') else ''} | {v['badge'].upper()}")
    res = {"at": now(), "niches": out}
    store.save("suggest.json", res)
    log(ai.usage_line())
    return res

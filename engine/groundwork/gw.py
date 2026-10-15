#!/usr/bin/env python3
"""Groundwork: type a niche, get a store.  python groundwork/gw.py <command> ...

  new "<niche>" [--slug S] [--auto]   create stores/<slug>/ and run Scout; --auto keeps going through every stage
  run --store S [--from STAGE] [--auto] [--listings N]   run the chain from a stage (stops where Jason must decide unless --auto)
  scout --store S [--angle "..."] [--like "Direction"]   another Scout round: your angle, or more directions like one you liked
  stores | profile | catalog | brand | setup | ads --store S   run one stage (stores = the best specialist stores in the niche)
  approve --store S [--corners "A,B"]  approve Scout corners (default: every GO corner)
  brand --store S [--pick NAME | --auto]
  list --store S [--key PID8-GENDER | --next N] [--images]   build listings from the shelf (one at a time, in order)
  status --store S                     where everything stands, what is waiting on Jason
  cj-shops --store S                   CJ's ids for your connected stores (for cj_shop_id)
  set --store S key=value ...          change store.json settings (shop_domain, sku_prefix, cj_shop_id, ...)
  golive --store S --confirm S         turn OFF the dry run for this store (real writes to Shopify and CJ from then on)

Every store starts in DRY RUN: Claude/CJ reads and planning happen for real, Shopify and CJ writes are only logged.
Order of stages: scout (rounds with Jason) -> approve -> stores -> profile -> catalog -> brand -> setup -> list -> ads
After approve everything runs on its own (brand name auto-picked; --pick-brand stops to let you choose)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundwork.lib.core import Log, Stop, Store, now, today  # noqa: E402
from groundwork.stages import ads, brand, catalog, clone, copypattern, gate, listing, reference, shelf, profile, scout, stores, storesetup  # noqa: E402

CHAIN = ["scout", "approve", "stores", "profile", "catalog", "brand", "setup", "clone", "list", "ads"]


def _store(a) -> Store:
    s = Store(a.store)
    if not s.exists:
        raise Stop(f"no store '{a.store}' (create it with: gw.py new \"<niche>\")")
    return s


def do_approve(s: Store, log: Log, corners: str = "") -> list[str]:
    sc = s.load("scout.json")
    if not sc:
        raise Stop("run scout first")
    names = [c["name"] for c in sc["corners"]]
    if corners:
        want = [x.strip() for x in corners.split(",") if x.strip()]
        bad = [w for w in want if w not in names]
        if bad:
            raise Stop(f"unknown corner(s) {bad}; choose from {names}")
    else:
        want = [c["name"] for c in sc["corners"] if c["score"]["verdict"] == "go"]
        if not want:
            raise Stop("Scout found no GO corner. Pick corners by name with --corners if you want to build anyway.")
    sc["approved"] = want
    s.save("scout.json", sc)
    log(f"approved corners: {', '.join(want)}")
    return want


def do_doctor(s: Store, log: Log) -> dict:
    """Beta step 0: is every account connected and working? Never prints a key; writes doctor.json."""
    import shutil
    import subprocess
    from groundwork.lib import engine
    from groundwork.stages.scout import cj_client
    out = {"at": now(), "engine": "rules" if engine.rules() else "solo"}

    def put(k, ok, text):
        out[k] = {"ok": ok, "text": text}
        log(f"  {k:8} {'OK ' if ok else 'NO '} {text}")
    log("# Connections")
    try:
        cj = cj_client(s, log)
        rows = cj.search("phone case", size=5) if cj else None
        put("cj", bool(cj) and rows is not None, f"CJ answered ({len(rows)} sample products)" if cj and rows is not None else "CJ_API_KEY missing or rejected")
    except Exception as e:  # noqa: BLE001
        put("cj", False, f"CJ: {str(e)[:120]}")
    try:
        if not os.environ.get("GEMINI_API_KEY", "").strip():
            raise Stop("GEMINI_API_KEY missing")
        from google import genai
        n = sum(1 for _ in genai.Client(api_key=os.environ["GEMINI_API_KEY"]).models.list())
        put("gemini", n > 0, "Gemini key works (photos)")
    except Exception as e:  # noqa: BLE001
        put("gemini", False, f"Gemini: {str(e)[:120]}")
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        put("claude", True, "Claude API key set (pay per use)")
    elif os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        exe = shutil.which("claude")
        if not exe:
            put("claude", False, "Claude token set, but Claude Code is not installed on the runner")
        else:
            try:
                r = subprocess.run([exe, "-p", "Reply with the single word OK", "--output-format", "json", "--max-turns", "1"],
                                   capture_output=True, text=True, timeout=180)
                ok = r.returncode == 0 and "ok" in (r.stdout or "").lower()
                put("claude", ok, "Claude subscription connected" if ok else f"Claude Code: {((r.stderr or r.stdout) or '').strip()[-160:]}")
            except Exception as e:  # noqa: BLE001
                put("claude", False, f"Claude Code: {str(e)[:120]}")
    else:
        put("claude", False, "CLAUDE_CODE_OAUTH_TOKEN missing (market prices need it)")
    if s.get("shop_domain"):
        try:
            from groundwork.lib.shopify import Shopify
            d = Shopify(s, log).read("query{ shop{ name myshopifyDomain } }")
            put("shopify", bool((d or {}).get("shop")), f"Shopify: {((d or {}).get('shop') or {}).get('name') or 'no answer'}")
        except Exception as e:  # noqa: BLE001
            put("shopify", False, f"Shopify: {str(e)[:120]}")
    else:
        out["shopify"] = {"ok": None, "text": "connect in step 4"}
    s.save("doctor.json", out)
    return out


def do_ladder(s: Store, log: Log, a) -> dict:
    """ROAS ladder: read the store's daily ad numbers (Google Ads API, read-only, or ads_daily.json), write ladder.json
    with a proposal. --approve records Jason's approval of the pending proposal; Jason sets the budget in Google Ads."""
    from groundwork.lib import gads, ladder
    lad = s.load("ladder.json") or {"changes": []}
    if a.config:
        s.settings["ladder"] = {**(s.get("ladder") or {}), **json.loads(a.config)}
        s.save_settings()
    if a.approve:
        p = lad.get("proposal") or {}
        if not p.get("needs_approval") or p.get("approved"):
            raise Stop("no step waiting for approval")
        p["approved"] = now()
        lad["changes"].append({"date": today(), "from": p["from"], "to": p["to"], "action": p["action"], "approved": p["approved"]})
        lad["budget"] = p["to"]
        s.save("ladder.json", lad)
        log(f"approved: {p['action']} ${p['from']} -> ${p['to']}/day. Set it in Google Ads (this run changes nothing).")
        return lad
    daily, src = s.load("ads_daily.json") or [], "ads_daily.json"
    cid = s.get("google_ads_customer_id") or ""
    if cid and gads.ready(cid):
        try:
            daily, src = ladder.pull(cid), "Google Ads API"
            s.save("ads_daily.json", daily)
        except Exception as e:  # noqa: BLE001
            log(f"Google Ads read failed, using the last saved numbers: {e}")
    budget = a.budget or lad.get("budget") or ((s.load("ads.json") or {}).get("campaign") or {}).get("budget_per_day") or 30
    lad.update(budget=budget, config=ladder.config(s.get("ladder")), source=src, at=now(),
               proposal=ladder.propose(daily, budget, lad.get("changes"), s.get("ladder")))
    s.save("ladder.json", lad)
    p = lad["proposal"]
    log(f"ROAS ladder: {p['action'].upper()} ${p['from']} -> ${p['to']}/day | {p['why']}")
    return lad


def do_list(s: Store, log: Log, key: str = "", n: int = 1, images: bool = False, lanes: int = 1) -> list[dict]:
    shelf = (s.load("shelf.json") or {}).get("items", {})
    done = {r.get("key") for r in listing.records(s) if r.get("status") in ("ACTIVE", "DRAFT", "DRY_RUN_OK") and not r.get("archived")}
    keys = [key] if key else [k for k, x in shelf.items() if x["status"] == "READY" and k not in done][:n]
    if not keys:
        log("nothing READY left on the shelf")
    lanes = max(1, min(int(lanes or 1), 5, len(keys) or 1))
    if lanes == 1:
        return [listing.run(s, log, k, with_images=images) for k in keys]
    # Lanes: several listings at once. CJ calls share one 1-per-second clock (lib/cj), SKU numbers are taken under a lock
    # (stages/listing), each listing writes only its own record file. A failed listing does not stop the others.
    from concurrent.futures import ThreadPoolExecutor
    log(f"{len(keys)} listings in {lanes} lanes")

    def one(k):
        try:
            return listing.run(s, log, k, with_images=images)
        except Stop as e:
            log(f"{k}: stopped: {e}")
            return {"key": k, "status": "FAILED", "error": str(e)}
    with ThreadPoolExecutor(max_workers=lanes) as ex:
        return list(ex.map(one, keys))


def status(s: Store, log: Log) -> None:
    st = s.settings
    log(f"# {st.get('name') or s.slug} | niche: {st.get('niche')} | {'DRY RUN' if s.dry_run else 'LIVE'} | shop: {st.get('shop_domain') or '-'} | SKU prefix: {st.get('sku_prefix') or '-'}")
    sc = s.load("scout.json")
    log(f"scout:   {sc['verdict'].upper() + ' | ' + str(len(sc.get('rounds') or [])) + ' round(s) | approved: ' + ', '.join(sc.get('approved') or ['none yet']) if sc else 'not run'}")
    ms = s.load("model_stores.json")
    log(f"stores:  {str(len(ms['stores'])) + ' specialist stores (' + ', '.join(r['domain'] for r in ms['stores'][:4]) + ')' if ms else 'not run'}")
    pr = s.load("profile.json")
    log(f"profile: {str(len(pr['types'])) + ' product types' if pr else 'not run'}")
    sh = (s.load("shelf.json") or {}).get("items", {})
    by = {}
    for x in sh.values():
        by[x["status"]] = by.get(x["status"], 0) + 1
    log(f"shelf:   {by or 'empty'} (target {st.get('catalog_target')})")
    b = s.load("brand.json") or {}
    log(f"brand:   {b.get('name') or 'not picked'} {b.get('domain') or ''}")
    ss = s.load("store_setup.json")
    log(f"setup:   {'done ' + ss.get('done', '') if ss and ss.get('done') else 'not run'}")
    ls = listing.records(s, include_dry=True)
    cnt = {}
    for r in ls:
        cnt[r.get("status")] = cnt.get(r.get("status"), 0) + 1
    log(f"listings: {cnt or 'none'}")
    for r in ls[-8:]:
        log(f"   {r['sku']} {r.get('status'):12} {r.get('url') or (r.get('error') or '')[:80]}")
    log(f"ads:     {'planned' if s.load('ads.json') else 'not run'}")
    waits = []
    if not s.get("shop_domain"):
        waits.append("create the Shopify store and `set shop_domain=xxx.myshopify.com`")
    if not s.get("cj_shop_id"):
        waits.append("connect CJ to the store and `set cj_shop_id=...` (gw.py cj-shops)")
    if sc and not sc.get("approved"):
        waits.append("approve Scout corners")
    if pr and not b.get("name"):
        waits.append("pick a brand name")
    if s.dry_run:
        waits.append("when the dry run looks right: gw.py golive --store " + s.slug + " --confirm " + s.slug)
    if waits:
        log("\nwaiting on Jason:\n" + "\n".join(f"  - {w}" for w in waits))


def chain(s: Store, log: Log, start: str, auto: bool, listings_n: int, pick_brand: bool = False) -> None:
    for stage in CHAIN[CHAIN.index(start):]:
        log(f"\n===== {stage} =====")
        if stage == "scout":
            r = scout.run(s, log)
            if r["verdict"] == "no" and not auto:
                log("Scout says NO. Stopping here; pick corners yourself with approve --corners to build anyway.")
                return
        elif stage == "approve":
            sc = s.load("scout.json")
            if not sc.get("approved"):
                if not auto:
                    log("Scout is a conversation: talk the corners through, run more rounds with `scout --store " + s.slug + " --angle \"...\"`, "
                        "then `approve --store " + s.slug + " --corners \"A,B\"` and `run --store " + s.slug + " --from stores`.")
                    return
                do_approve(s, log)
        elif stage == "stores":
            stores.run(s, log)
        elif stage == "profile":
            profile.run(s, log)
        elif stage == "catalog":
            sh = catalog.run(s, log)
            ready = [x for x in sh["items"].values() if x["status"] == "READY"]
            need = int(s.get("catalog_min") or 20)
            if len(ready) < need:
                log(f"\nSTOP: the market is not doable with this cost structure: only {len(ready)} products clear the profit floor (need {need}). "
                    "Nothing after this (brand, domain, store) is worth doing yet. Options: another Scout angle, different corners, or a lower catalog_min.")
                return
        elif stage == "brand":
            b = brand.run(s, log, auto=not pick_brand)
            if not b.get("name"):
                log("Waiting on you: pick a name (gw.py brand --store " + s.slug + " --pick NAME), then: gw.py run --store " + s.slug + " --from setup")
                return
        elif stage == "setup":
            storesetup.run(s, log)
        elif stage == "clone":
            try:
                clone.run(s, log)
            except Stop as e:
                log(f"clone skipped: {e}")
        elif stage == "list":
            do_list(s, log, n=listings_n, lanes=min(4, listings_n))
        elif stage == "ads":
            try:
                ads.run(s, log)
            except Stop as e:
                log(f"ads skipped: {e}")
    status(s, log)


def main() -> int:
    ap = argparse.ArgumentParser(description="Groundwork: niche in, store out")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new"); p.add_argument("niche"); p.add_argument("--slug"); p.add_argument("--auto", action="store_true"); p.add_argument("--listings", type=int, default=3)
    p.add_argument("--pick-brand", action="store_true")
    p = sub.add_parser("run"); p.add_argument("--store", required=True); p.add_argument("--from", dest="start", default="scout", choices=CHAIN)
    p.add_argument("--auto", action="store_true"); p.add_argument("--listings", type=int, default=3); p.add_argument("--pick-brand", action="store_true")
    p = sub.add_parser("scout"); p.add_argument("--store", required=True); p.add_argument("--angle", default="")
    p.add_argument("--like", default="", help="more store directions like this one (a GO direction's name)")
    p = sub.add_parser("pick", help="Solo page pick: CJ check, approve and rank real stores in one run")
    p.add_argument("--store", required=True); p.add_argument("--direction", required=True); p.add_argument("--niche", default="")
    p.add_argument("--typical", type=float, default=0); p.add_argument("--top", type=float, default=0); p.add_argument("--ads", type=int, default=0)
    p.add_argument("--category", default="other"); p.add_argument("--keywords", default=""); p.add_argument("--products", default="")
    p = sub.add_parser("gate", help="Solo step 1: widen to a general niche, block what Google won't run, check CJ supply, market and the live pricing formula on real products")
    p.add_argument("--store", required=True); p.add_argument("--niche", required=True)
    p.add_argument("--types", default="", help="Beta: the store's 3 product types, comma separated")
    p.add_argument("--category", default="", help="Beta: apparel | home | other")
    p = sub.add_parser("doctor", help="Beta: check every connected account (CJ, Gemini, Claude, Shopify) and save doctor.json")
    p.add_argument("--store", required=True)
    p = sub.add_parser("suggest", help="Solo step 1: 5-6 general niches with a real CJ supply count each")
    p.add_argument("--store", required=True); p.add_argument("--avoid", default="")
    p = sub.add_parser("override", help="Solo step 1: keep a niche the verdict flagged (demand/trend), with the reason recorded")
    p.add_argument("--store", required=True); p.add_argument("--reason", default="")
    p = sub.add_parser("demand", help="Solo step 1: Keyword Planner + Google Trends for one niche (no AI cost)")
    p.add_argument("--store", required=True); p.add_argument("--kw", required=True); p.add_argument("--refresh", action="store_true")
    p = sub.add_parser("ladder", help="Launch: ROAS ladder proposal (never changes the budget); --approve records Jason's yes")
    p.add_argument("--store", required=True); p.add_argument("--budget", type=float, default=0); p.add_argument("--approve", action="store_true")
    p.add_argument("--config", default="", help='JSON overrides, e.g. {"up_roas": 3, "up_pct": 0.25}')
    p = sub.add_parser("copy", help="Solo step 5: the store's product-copy pattern (strict; optional parameters applied within the rules)")
    p.add_argument("--store", required=True); p.add_argument("--params", default="")
    p = sub.add_parser("kit", help="Solo step 3: save the brand kit picked on the page")
    p.add_argument("--store", required=True); p.add_argument("--json", required=True)
    p = sub.add_parser("batch", help="Solo step 8: vet the next N shelf products and build them as listings (photos, copy, price, CJ link)")
    p.add_argument("--store", required=True); p.add_argument("--n", type=int, default=10); p.add_argument("--lanes", type=int, default=4)
    p = sub.add_parser("shelf", help="Solo step 8: draft the 50-product shelf (reference best sellers first, live formula, diversity)")
    p.add_argument("--store", required=True)
    p = sub.add_parser("ref", help="Solo step 2: verify the reference store you pasted and read its layout for the ripper")
    p.add_argument("--store", required=True); p.add_argument("--url", required=True)
    p = sub.add_parser("recs", help="Solo step 2: reference stores that clear the hard bar (may be none)")
    p.add_argument("--store", required=True)
    p = sub.add_parser("pack", help="beta niche pack: several directions with their numbers -> CJ check each -> approve the ones CJ can supply -> rank real stores. One run.")
    p.add_argument("--store", required=True); p.add_argument("--niche", required=True)
    p.add_argument("--spec", required=True, help='JSON list: [{"name","typical","top","category","ads","keywords":[..],"products":[..]}]')
    p = sub.add_parser("supply"); p.add_argument("--store", required=True); p.add_argument("--direction", default="", help="check one direction by name (adds it if new)")
    p = sub.add_parser("stores"); p.add_argument("--store", required=True); p.add_argument("--url", default="", help="check one store (e.g. Jason's pick) and merge it into the list"); p.add_argument("--recheck", action="store_true", help="recount reviews/Google for the stores already found (cheap)")
    for c in ("profile", "setup", "clone", "ads", "status", "cj-shops"):
        p = sub.add_parser(c); p.add_argument("--store", required=True)
    p = sub.add_parser("catalog"); p.add_argument("--store", required=True); p.add_argument("--limit", type=int)
    p = sub.add_parser("approve"); p.add_argument("--store", required=True); p.add_argument("--corners", default="")
    p = sub.add_parser("brand"); p.add_argument("--store", required=True); p.add_argument("--pick"); p.add_argument("--auto", action="store_true")
    p.add_argument("--direction", help="naming direction: coined | descriptive | evocative | compound | persona | playful")
    p = sub.add_parser("list"); p.add_argument("--store", required=True); p.add_argument("--key", default=""); p.add_argument("--next", type=int, default=1)
    p.add_argument("--images", action="store_true"); p.add_argument("--lanes", type=int, default=1, help="listings at once (1-5); 4 is the sweet spot")
    p = sub.add_parser("set"); p.add_argument("--store", required=True); p.add_argument("pairs", nargs="+")
    p = sub.add_parser("golive"); p.add_argument("--store", required=True); p.add_argument("--confirm", required=True)
    a = ap.parse_args()

    s = None
    log = Log(None, a.cmd)
    rc = 0
    try:
        if a.cmd == "pick":
            # Solo page: the direction Jason picked, with its numbers -> CJ check -> approve -> real stores ranked. One run.
            s = Store(a.store)
            if not s.exists:
                s = Store.create(a.niche or a.direction, a.store)
            log.store = s
            given = {"typical_price": a.typical, "top_price": a.top, "category": a.category, "advertisers": a.ads,
                     "cj_keywords": [k.strip() for k in a.keywords.split(",") if k.strip()], "products": [k.strip() for k in a.products.split(",") if k.strip()]}
            res = scout.run_supply(s, log, direction=a.direction, given=given).get(a.direction) or {}
            if res.get("ok") is False:
                log(f"STOP: {res.get('text')}")
            else:
                do_approve(s, log, a.direction)
                stores.run(s, log)
        elif a.cmd == "copy":
            s = Store(a.store); log.store = s
            copypattern.run(s, log, a.params)
        elif a.cmd == "kit":
            s = Store(a.store); log.store = s
            shelf.write_kit(s, log, a.json)
        elif a.cmd == "batch":
            s = Store(a.store); log.store = s
            if not s.load("profile.json"):
                profile.run(s, log)
            keys = shelf.to_catalog(s, log, a.n)
            if keys:
                do_list(s, log, n=len(keys), images=True, lanes=a.lanes)
        elif a.cmd == "shelf":
            s = Store(a.store); log.store = s
            shelf.run(s, log)
        elif a.cmd in ("ref", "recs"):
            s = Store(a.store); log.store = s
            if a.cmd == "ref":
                reference.verify(s, log, a.url)
            else:
                reference.recommend(s, log)
        elif a.cmd in ("gate", "suggest"):
            s = Store(a.store)
            if not s.exists:
                s = Store.create(getattr(a, "niche", "") or "solo", a.store)
            log.store = s
            if a.cmd == "gate":
                g = gate.run(s, log, a.niche, [t.strip() for t in a.types.split(",") if t.strip()] or None, a.category)
                if s.get("compare_at") is None:
                    s.settings["compare_at"] = False   # Solo: real prices; the "was" price is an opt-in setting
                s.settings["solo"] = True
                s.save_settings()
                if g.get("ok"):
                    sp = (g.get("split") or "").lower()
                    s.settings["niche"] = g["niche"]
                    s.settings["genders"] = ["women", "men"] if ("women" in sp and "men" in sp.replace("women", "")) else ["unisex"]
                    s.save_settings()
            else:
                gate.suggest(s, log, avoid=[x.strip() for x in a.avoid.split(",") if x.strip()])
        elif a.cmd == "doctor":
            s = Store(a.store)
            if not s.exists:
                s = Store.create("beta store", a.store)
            log.store = s
            do_doctor(s, log)
        elif a.cmd == "override":
            s = Store(a.store); log.store = s
            gate.override(s, log, a.reason)
        elif a.cmd == "demand":
            from groundwork.lib import demand as _dm, trends as _tr
            s = Store(a.store); log.store = s
            dm, tr = _dm.check(a.kw, [], s, a.refresh), _tr.check(a.kw, a.refresh)
            log(f"{a.kw}: {dm['text']} {dm.get('error', '')}\n{tr['text']} {tr.get('error', '')} | {len(tr.get('series') or [])} months")
        elif a.cmd == "ladder":
            s = Store(a.store); log.store = s
            do_ladder(s, log, a)
        elif a.cmd == "pack":
            # Beta page niche packs: the same general pipeline as Solo's pick, for every direction in the pack.
            s = Store(a.store)
            if not s.exists:
                s = Store.create(a.niche, a.store)
            log.store = s
            ok = []
            for d in json.loads(a.spec):
                given = {"typical_price": d.get("typical", 0), "top_price": d.get("top", 0), "category": d.get("category", "other"),
                         "advertisers": d.get("ads", 0), "cj_keywords": d.get("keywords") or [d["name"]], "products": d.get("products") or []}
                res = scout.run_supply(s, log, direction=d["name"], given=given).get(d["name"]) or {}
                log(f"{d['name']}: {res.get('text')}")
                if res.get("ok") is not False:
                    ok.append(d["name"])
            if ok:
                do_approve(s, log, ",".join(ok))
                stores.run(s, log)
            else:
                log("STOP: CJ cannot supply any direction in this pack")
        elif a.cmd == "new":
            s = Store.create(a.niche, a.slug)
            log.store = s
            log(f"created stores/{s.slug}/ (DRY RUN until golive)")
            chain(s, log, "scout", a.auto, a.listings, a.pick_brand)
        else:
            s = _store(a)
            log.store = s
            if a.cmd == "run":
                chain(s, log, a.start, a.auto, a.listings, a.pick_brand)
            elif a.cmd == "scout":
                scout.run(s, log, angle=a.angle, like=a.like)
            elif a.cmd == "supply":
                scout.run_supply(s, log, direction=a.direction)
            elif a.cmd == "stores":
                stores.recheck(s, log) if a.recheck else stores.run(s, log, url=a.url)
            elif a.cmd == "approve":
                do_approve(s, log, a.corners)
            elif a.cmd == "profile":
                profile.run(s, log)
            elif a.cmd == "catalog":
                catalog.run(s, log, a.limit)
            elif a.cmd == "brand":
                brand.run(s, log, a.pick, a.auto, a.direction)
            elif a.cmd == "setup":
                storesetup.run(s, log)
            elif a.cmd == "clone":
                clone.run(s, log)
            elif a.cmd == "list":
                do_list(s, log, a.key, a.next, a.images, a.lanes)
            elif a.cmd == "ads":
                ads.run(s, log)
            elif a.cmd == "status":
                status(s, log)
            elif a.cmd == "cj-shops":
                from groundwork.lib.cj import CJ
                found = []
                for x in CJ(s, log).shops():
                    row = {k: x.get(k) for k in ("id", "shopId", "name", "shopName", "type", "platform") if x.get(k)}
                    found.append(row)
                    log(json.dumps(row))
                s.save("cj_shops.json", {"at": now(), "shops": found})   # Beta step 4 shows these to pick from
            elif a.cmd == "set":
                for kv in a.pairs:
                    k, _, v = kv.partition("=")
                    if k in ("dry_run",):
                        raise Stop("use `golive` to turn off the dry run")
                    if k in ("shop_domain", "sku_prefix", "cj_shop_id") and not str(v).strip():
                        raise Stop(f"{k} cannot be empty")
                    if k == "sku_prefix" and listing.records(s, include_dry=False):
                        raise Stop("sku_prefix is fixed once real listings exist")
                    if k not in ("shop_domain", "public_domain", "sku_prefix", "cj_shop_id", "support_email", "business_city", "name", "niche"):
                        try:   # numbers, true/false and lists; ids and names always stay text
                            v = json.loads(v)
                        except json.JSONDecodeError:
                            pass
                    s.settings[k] = v
                    log(f"{k} = {v!r}")
                s.save_settings()
            elif a.cmd == "golive":
                if a.confirm != s.slug:
                    raise Stop(f"--confirm must be the store slug '{s.slug}'")
                miss = [k for k in ("shop_domain", "sku_prefix", "cj_shop_id") if not s.get(k)]
                if miss:
                    raise Stop(f"set these first: {miss}")
                s.settings["dry_run"] = False
                s.save_settings()
                log(f"{s.slug}: dry run OFF. Real Shopify/CJ writes from the next run.")
    except Stop as e:
        log(f"STOP: {e}")
        rc = 1
    finally:
        p = log.save()
        if p and os.environ.get("GITHUB_STEP_SUMMARY"):
            Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text("```\n" + "\n".join(log.lines[-200:]) + "\n```\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())

"""Clone: Coziva's live theme onto the new store, rebranded. Jason's rule for a fast second store: copy what Coziva already
settled instead of designing again. Policies, pages, collections and menu come from `gw.py setup`; this stage does the theme.

  1 pull   Coziva's LIVE theme with the Shopify CLI, read-only (COZIVA_THEME_TOKEN: a Theme Access password for Coziva,
           a repo secret). Coziva's own product and hero photos are skipped (they are Coziva's, and huge).
  2 swap   - the name: "Coziva"/"COZIVA" as a word becomes the new brand (CSS class names like coziva-hero stay: invisible)
           - the domain coziva.co becomes the new domain
           - colours: Coziva's known palette hex values become the brand kit's palette
           - fonts: Coziva's Montserrat / Phudu become the brand kit's body / heading fonts
           - words: every short text setting in the templates (headings, buttons, captions) is rewritten by Claude for the
             new niche, following groundwork/rules (no-slop voice, conversion rules); code rejects slop wording
           - links: Coziva collection handles become the new store's collections; Coziva's own images are cleared
  3 push   to the new store as an UNPUBLISHED theme named "<Brand> (Groundwork)". Never published here: Jason looks at the
           preview and publishes it (one click), or tells Claude to.
Dry run (the default) does 1-2 and writes the result to stores/<slug>/work/theme for review, without pushing.
Writes stores/<slug>/clone.json.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from ..lib import ai, rules
from ..lib.core import Log, Stop, Store, now
from ..lib.shopify import Shopify

COZIVA_STORE = ""
IGNORE = ["AGENTS.md", "assets/*.png", "assets/*.jpg", "assets/*.jpeg", "assets/*.webp", "assets/*.gif", "assets/*.mp4"]
# Coziva's colours (live theme + Ororo build) -> brand-kit role
PALETTE_ROLES = {"#140800": "text", "#1c1917": "text", "#f55a00": "accent", "#c2410c": "accent", "#faf7f2": "background",
                 "#f8f8f4": "background", "#ededed": "card", "#f1ebe2": "card"}
FONT_ROLES = {"Phudu": "heading", "Montserrat": "body"}
TEXT_KEYS = re.compile(r"(heading|title|subheading|text|caption|label|button|content|description|eyebrow|cta|tagline)", re.I)
BRAND_RE = re.compile(r"(?<![\w-])(Coziva|COZIVA|coziva)(?![\w-])")


def cli(args: list[str], token: str, cwd: Path | None = None, timeout: int = 900) -> tuple[int, str]:
    """Shopify CLI with the password in the environment (never on the command line, never printed)."""
    env = {**os.environ, "SHOPIFY_CLI_THEME_TOKEN": token, "SHOPIFY_CLI_NO_ANALYTICS": "1", "CI": "1", "NODE_USE_ENV_PROXY": "1"}
    exe = shutil.which("shopify")
    if not exe:
        raise Stop("Shopify CLI not installed (the workflow installs it for `clone`; locally: npm i -g @shopify/cli)")
    p = subprocess.run([exe] + args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout + p.stderr).replace(token, "***") if token else p.stdout + p.stderr
    return p.returncode, out[-4000:]


def _split_json(raw: str) -> tuple[str, object]:
    """Shopify template JSON may start with a /* comment */ header; keep it and parse the rest."""
    m = re.match(r"\s*/\*.*?\*/\s*", raw, re.S)
    head = m.group(0) if m else ""
    return head, json.loads(raw[len(head):])


def _walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}/{i}")
    else:
        yield path, node


def _set(node, path: str, value) -> None:
    keys = [k for k in path.split("/") if k != ""]
    for k in keys[:-1]:
        node = node[int(k)] if isinstance(node, list) else node[k]
    last = keys[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def swap_text(text: str, brand: str, domain: str, kit: dict) -> tuple[str, int]:
    n = 0
    def name(m):
        w = m.group(1)
        return brand.upper() if w.isupper() else brand.lower() if w.islower() else brand
    text, k = BRAND_RE.subn(name, text); n += k
    if domain:
        text, k = re.subn(r"coziva\.co\b", domain, text); n += k
    pal, fonts = kit.get("palette") or {}, kit.get("fonts") or {}
    card = pal.get("card") or pal.get("background")
    for hexv, role in PALETTE_ROLES.items():
        new = card if role == "card" else pal.get(role) or (pal.get("primary") if role == "text" else None)
        if new:
            text, k = re.subn(re.escape(hexv), new, text, flags=re.I); n += k
    for old, role in FONT_ROLES.items():
        new = fonts.get(role)
        if new and new != old:
            text, k = re.subn(rf"family={old}\b", "family=" + new.replace(" ", "+"), text); n += k
            text, k = re.subn(rf"\b{old}\b", new, text); n += k
    return text, n


# Ripper: the reference store's homepage section types -> the proven custom sections they become.
RIP_MAP = {"hero": "coziva-hero", "quick-links": "coziva-quick-links", "product-carousel": "coziva-hot-picks", "category-tiles": "coziva-category-tiles",
           "product-grid": "coziva-feature-products", "trust-bar": "coziva-stats", "stats": "coziva-stats", "feature-cards": "coziva-explore",
           "editorial": "coziva-explore"}
FAKE_PROOF = {"reviews", "press-logos", "testimonials", "as-seen-on"}


def apply_layout(index_path: Path, layout: dict, log: Log) -> dict:
    """Rebuild templates/index.json in the reference store's section order with our proven sections. Fake proof (reviews,
    press logos) is dropped; header, footer, announcement and newsletter live in the section groups."""
    head, data = _split_json(index_path.read_text())
    by_type: dict[str, list[str]] = {}
    for k in data.get("order") or []:
        by_type.setdefault((data["sections"].get(k) or {}).get("type", ""), []).append(k)
    used: dict[str, int] = {}
    order, sections, dropped, unknown = [], {}, [], []
    for x in layout.get("home") or []:
        t = str(x.get("type") or "")
        if t in FAKE_PROOF:
            dropped.append(t)
            continue
        st = RIP_MAP.get(t)
        if not st or st not in by_type:
            if t not in ("announcement", "header", "footer", "newsletter"):
                unknown.append(t)
            continue
        n = used.get(st, 0)
        src = by_type[st][min(n, len(by_type[st]) - 1)]
        key = src if n == 0 else f"{src}_{n + 1}"
        sections[key] = json.loads(json.dumps(data["sections"][src]))
        sections[key].pop("disabled", None)
        order.append(key)
        used[st] = n + 1
    if not order:
        log("rip: no mappable sections; keeping the proven homepage order")
        return {"order": data.get("order"), "dropped": dropped, "unknown": unknown}
    data["sections"], data["order"] = sections, order
    index_path.write_text(head + json.dumps(data, indent=2, ensure_ascii=False))
    log(f"rip: homepage rebuilt in their order: {' > '.join(order)}" + (f"; dropped fake proof: {', '.join(dropped)}" if dropped else ""))
    return {"order": order, "dropped": dropped, "unknown": unknown}


REWRITE = """You rewrite the visible words of a Shopify theme for a new store. The theme came from a heated-apparel store
(Coziva); the new store is "{brand}", selling {niche} in the US. Brand voice: {voice}. Tagline: {tagline}.
Collections on the new store (handle: title): {collections}.
{voice_rules}
Input: JSON object of id -> current text (headings, buttons, captions, short paragraphs; may hold simple HTML).
Rewrite each for the new store and niche, same length and same HTML tags, keeping anything that is not about heated
apparel (e.g. "Free shipping", "Shop now") as it is. Remove claims you cannot know (review counts, awards, years).
Answer ONLY JSON: {{"texts": {{"<id>": "<new text>", ...}}, "collections": {{"<old handle>": "<new handle or empty>", ...}}}}
where "collections" maps every old Coziva collection handle listed below to the closest new handle ("" = none).
Old collection handles: {old_handles}"""


def run(store: Store, log: Log) -> dict:
    from ..lib import engine
    if engine.rules() or engine.solo_max():
        raise Stop("your copy builds the homepage in Shopify's theme editor from your reference store's section list (step 5); the ripper needs Coziva's theme")
    brand = store.load("brand.json") or {}
    if not brand.get("name"):
        raise Stop("pick the brand first (gw.py brand --pick NAME)")
    profile = store.load("profile.json") or {}
    setup = store.load("store_setup.json") or {}
    kit = brand.get("kit") or {}
    work = store.path("work", "theme")
    log(f"# Clone Coziva's theme -> {brand['name']} ({store.get('shop_domain') or 'no shop yet'}) {'DRY RUN' if store.dry_run else 'LIVE'}")

    # 1 pull Coziva's live theme (read-only)
    token = (os.environ.get("COZIVA_THEME_TOKEN") or os.environ.get("SHOPIFY_CLI_THEME_TOKEN") or "").strip()
    if not token:
        raise Stop("COZIVA_THEME_TOKEN is not set: add Coziva's Theme Access password as a repo secret (Shopify admin > Theme Access)")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    args = ["theme", "pull", "--store", COZIVA_STORE, "--live", "--path", str(work)]
    for g in IGNORE:
        args += ["--ignore", g]
    code, out = cli(args, token)
    files = [p for p in work.rglob("*") if p.is_file()]
    if code != 0 or not files:
        raise Stop(f"could not pull Coziva's theme (exit {code}): {out[-600:]}")
    log(f"pulled Coziva's live theme: {len(files)} files (Coziva photos skipped)")

    # 1b rip: the reference store's homepage layout, rebuilt with our sections
    rip = store.load("rip.json") or {}
    rip_res = apply_layout(work / "templates" / "index.json", rip.get("layout") or {}, log) if rip.get("layout") and (work / "templates" / "index.json").exists() else None

    # 2a name, domain, colours, fonts
    swaps = 0
    for p in files:
        if p.suffix not in (".liquid", ".json", ".css", ".js", ".svg"):
            continue
        t = p.read_text(errors="replace")
        t2, n = swap_text(t, brand["name"], brand.get("domain") or "", kit)
        if n:
            p.write_text(t2)
            swaps += n
    log(f"swapped name/domain/colours/fonts: {swaps} places")

    # 2b words + collection links + Coziva images in templates and section groups
    docs, texts, handles = {}, {}, set()
    for p in sorted(list((work / "templates").glob("*.json")) + list((work / "sections").glob("*.json"))):
        try:
            head, data = _split_json(p.read_text())
        except json.JSONDecodeError:
            continue
        docs[p] = (head, data)
        for path, v in _walk(data):
            if not isinstance(v, str) or not v.strip():
                continue
            key = path.rsplit("/", 1)[-1]
            if v.startswith("shopify://shop_images/") or v.startswith("shopify://files/"):
                _set(data, path, "")          # Coziva's own image: not on the new store
                continue
            m = re.match(r"shopify://collections/([\w-]+)", v)
            if m:
                handles.add(m.group(1))
            elif key == "collection" and re.fullmatch(r"[\w-]+", v):
                handles.add(v)
            elif TEXT_KEYS.search(key) and re.search(r"[A-Za-z]{3}", v) and len(v) <= 600 and "{{" not in v:
                texts[f"{p.name}|{path}"] = v
    new_cols = {c.get("handle") or "": c.get("title") or "" for c in (setup.get("collections_made") or [])} or \
               {re.sub(r"[^a-z0-9]+", "-", c["title"].lower()).strip("-"): c["title"] for c in profile.get("collections") or []}
    mapping, new_texts = {}, {}
    if texts or handles:
        ids = list(texts)
        short = {str(i): texts[k] for i, k in enumerate(ids)}
        ans = ai.ask_json("clone_rewrite", REWRITE.format(brand=brand["name"], niche=profile.get("niche") or store.get("niche"), voice=profile.get("voice", ""),
                                                         tagline=kit.get("tagline", ""), collections=json.dumps(new_cols), voice_rules=rules.COPY_VOICE,
                                                         old_handles=", ".join(sorted(handles)) or "none"),
                          json.dumps(short), max_tokens=12000,
                          check=lambda a: (["texts missing"] if not isinstance((a or {}).get("texts"), dict) else [])
                          + ([f"AI-slop wording, rewrite without: {h}"] if (h := rules.slop_hits(json.dumps((a or {}).get("texts") or {}))) else []))
        new_texts = {ids[int(i)]: v for i, v in (ans.get("texts") or {}).items() if str(i).isdigit() and int(i) < len(ids)}
        mapping = {k: v for k, v in (ans.get("collections") or {}).items() if k in handles}
    changed = 0
    for p, (head, data) in docs.items():
        for path, v in list(_walk(data)):
            if not isinstance(v, str):
                continue
            k = f"{p.name}|{path}"
            if k in new_texts and new_texts[k] != v:
                _set(data, path, new_texts[k]); changed += 1
                continue
            m = re.match(r"shopify://collections/([\w-]+)", v)
            if m and m.group(1) in mapping:
                _set(data, path, f"shopify://collections/{mapping[m.group(1)]}" if mapping[m.group(1)] else ""); changed += 1
            elif path.endswith("/collection") and v in mapping:
                _set(data, path, mapping[v]); changed += 1
        p.write_text(head + json.dumps(data, indent=2, ensure_ascii=False))
    leftover = sum(len(BRAND_RE.findall(p.read_text(errors="replace"))) for p in work.rglob("*") if p.is_file() and p.suffix in (".liquid", ".json"))
    log(f"rewrote {changed} texts/links for {profile.get('niche') or 'the niche'}; collections {mapping or 'none to map'}; 'Coziva' left: {leftover}")

    out = {"at": now(), "files": len(files), "swaps": swaps, "texts_rewritten": changed, "collections": mapping, "coziva_left": leftover,
           "theme_name": f"{brand['name']} (Groundwork)", "theme_id": None, "pushed": False, "rip": rip_res}

    # 3 push, unpublished
    if store.dry_run:
        log("DRY RUN: not pushed. The rebranded theme is in stores/<slug>/work/theme (also in the run's artifact) for review.")
    else:
        sh = Shopify(store, log)
        if sh.domain.lower().strip() in (COZIVA_STORE, "coziva.co", "www.coziva.co"):
            raise Stop("refusing: this store's shop_domain is Coziva; the clone only ever pushes to the NEW store")
        code, res = cli(["theme", "push", "--store", sh.domain, "--path", str(work), "--unpublished", "--theme", out["theme_name"], "--json"], sh.token())
        m = re.search(r'"id"\s*:\s*(\d+)', res)
        if code != 0:
            raise Stop(f"theme push failed (exit {code}): {res[-600:]} (the store's pipeline app needs the write_themes scope)")
        out.update(pushed=True, theme_id=int(m.group(1)) if m else None)
        log(f"pushed UNPUBLISHED theme '{out['theme_name']}' (id {out['theme_id']}). Preview it, then publish it in Shopify > Themes.")
    store.save("clone.json", out)
    return out

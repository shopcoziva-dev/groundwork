"""The 5 listing photos for any product type, from the niche profile's recipe + the product's fact sheet.

Shots (gallery order, alt text keys match): 01-studio, 02-hero, 03-product-alone, 04-detail, 05-feature.
  people types : 01 model in studio | 02 candid lifestyle with the model | 03 product alone | 04 macro of the real detail | 05 feature
  product types: 01 main studio shot | 02 product in its real place / in use | 03 product alone (second view) | 04 detail | 05 feature
04 needs a detail photo + subject in the fact sheet; 05 needs a feature in the profile (kind != none). Missing = skipped.
Cost mode (Coziva default "cheap"): 01/02 on the pro image model with a vision check and one redo; 03/04/05 on the flash
model, unchecked. A shot still flagged after its redo is accepted and the listing gets the tag check-photos.
Generator: Gemini (env GEMINI_API_KEY), same models and calls as scripts/gen_image.py.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import urllib.request
from pathlib import Path

from ..lib.core import GW, REPO, Log, Stop, Store, today
from ..lib import rules as gwrules

PRO_MODEL = "gemini-3-pro-image-preview"
FLASH_MODEL = "gemini-2.5-flash-image"
VISION_MODELS = ["gemini-3-flash-preview", "gemini-flash-latest", "gemini-3-pro-preview", "gemini-2.5-flash"]
STYLE_STUDIO = REPO / "references/style/studio-pose-lighting-ref.png"
STYLE_ALONE = REPO / "references/style/shot5-product-alone-ref.png"
ROSTER = {"women": ["Naomi", "Hannah", "Mei"], "men": ["Mateo", "Eli", "Luke"]}
SHOTS = ["01-studio", "02-hero", "03-product-alone", "04-detail", "05-feature"]
COST = {PRO_MODEL: 0.134, FLASH_MODEL: 0.039}

VET = """You are a strict quality reviewer for an online store's product photos. Grade the FIRST image. Further images are
the references (person and/or real product) it must match. Fail ONLY for real defects a shopper would notice:
wrong product (shape, colour, parts differ from the reference), wrong count of products/hands/feet/fingers, parts merged,
duplicated or passing through each other, a different person than the reference, text/logos/watermarks added, obvious AI
artefacts (melted details, plastic skin), split frame or seam, product cropped when it should be whole, and every listed
check below. Answer ONLY JSON: {"pass": true|false, "issues": ["short specific issue", ...]}"""


def _t(name: str) -> str:
    return (GW / "prompts" / f"{name}.md").read_text().strip()


def roster_for(store: Store, gender: str, n: int) -> str | None:
    r = store.get("roster") or ROSTER
    pool = r.get(gender) or (r.get("women", []) + r.get("men", []) if gender == "unisex" else [])
    return pool[n % len(pool)] if pool else None


def portrait(name: str) -> Path:
    return REPO / "references/models" / f"{name.lower()}.png"


def build_jobs(store: Store, profile: dict, facts: dict, sku: str, n: int, work: Path, plan_only: bool = False) -> list[dict]:
    """Jobs for one listing. facts = fact sheet (lib: listing.facts). work = folder with product.jpg / detail.jpg."""
    t = profile["types"][facts["type"]]
    people = bool(t.get("people"))
    rules = " ".join((profile.get("photo_rules") or []) + (t.get("rules") or []))
    if not t.get("corded") and "cordless" in rules.lower():
        rules += " Show the product CORDLESS: no charging cable or plug visible."
    clause = f"{facts['units']} {facts['scale']} " + " ".join(f"{x}." for x in facts.get("facts") or [])
    desc = facts["product_desc"]
    prod = work / "product.jpg"
    feature = t.get("feature") or {"kind": "none"}
    effect = feature.get("effect") if feature.get("kind") == "effect" else ""
    effect_hero = (f"VISIBLE EFFECT (real, subtle, only on the product itself): {effect}" if effect else "No glow, halo, rim light or coloured tint of any kind.")
    jobs: list[dict] = []
    cheap = (store.get("image_mode") or "cheap") == "cheap"

    def J(key, prompt, refs, checks=(), people_shot=False):
        j = {"key": key, "out": str(work / f"{sku.lower()}-{key}.png"), "prompt": prompt, "refs": [str(r) for r in refs if r],
             "checks": list(checks), "aspect_ratio": "3:4"}
        if people_shot or not cheap or key in ("01-studio", "02-hero"):
            j.update(model=PRO_MODEL, image_size="2K", vet=True, attempts=2)
        else:
            j.update(model=FLASH_MODEL, vet=False, attempts=1)
        jobs.append(j)

    base_checks = [f"Exactly this and no more: {facts['units']}", f"Product colour is {facts['color']} and matches the product reference",
                   "No parts fused, merged, duplicated or passing through each other"] + list(gwrules.IMAGE_CHECKS) + [f"Rule: {r}" for r in t.get("rules") or []]
    if people:
        model = facts.get("model")
        if not model:
            raise Stop(f"type {facts['type']} uses a model but the fact sheet has none")
        pr = portrait(model)
        if not pr.exists():
            raise Stop(f"no portrait for model {model} at {pr}")
        poses = t.get("poses") or ["standing relaxed"]
        face = bool(t.get("face", True))
        gaze = ("Looking straight into the camera." if n % 2 == 0 else "Looking slightly away from the camera, not at the lens.") if face else "No face is in the frame."
        expr = t.get("expression") or facts.get("expression") or "calm neutral to mildly happy"
        J("01-studio", _t("studio_person").format(product_desc=desc, fact_clause=clause, outfit=facts["outfit"], framing=t["framing"], pose=poses[n % len(poses)],
                                                    gaze=gaze, expression=expr, rules=rules or "none"),
          [STYLE_STUDIO, pr, prod], base_checks + [f"Framing: {t['framing']}", f"The person is the same person as the portrait reference"], True)
        hero = facts.get("hero") or {}
        J("02-hero", _t("hero_person").format(scene=hero.get("scene") or t["hero_scene"], outfit=facts["outfit"], moment=hero.get("moment") or t.get("hero_moment") or poses[0],
                                                fact_clause=clause, framing=t["framing"], expression=expr, effect_clause=effect_hero, rules=rules or "none"),
          [pr, prod], base_checks + ["The person does not look into the camera"], True)
    else:
        J("01-studio", _t("studio_product").format(product_desc=desc, fact_clause=clause, presentation=t.get("studio") or "a three-quarter front view at eye level",
                                                     rules=rules or "none"), [prod], base_checks)
        hero = facts.get("hero") or {}
        J("02-hero", _t("hero_product").format(scene=hero.get("scene") or t["hero_scene"], moment=hero.get("moment") or t.get("hero_moment") or "in place, ready to use",
                                                 fact_clause=clause, effect_clause=effect_hero, rules=rules or "none"), [prod], base_checks)
    J("03-product-alone", _t("alone").format(product_desc=desc, presentation=t["alone"]), [prod, STYLE_ALONE],
      [f"Product colour is {facts['color']}", "Product only, no person, no text"])
    if facts.get("detail_subject") and (plan_only or (work / "detail.jpg").exists()):
        J("04-detail", _t("detail").format(detail_subject=facts["detail_subject"]), [work / "detail.jpg"], ["Close-up of the real feature, nothing invented"])
    if feature.get("kind") in ("effect", "function", "in_use", "scale"):
        J("05-feature", _t("feature").format(product_desc=desc, fact_clause=clause, describe=feature["describe"],
                                              effect_clause=(f"EFFECT: {effect}" if effect else ""), rules=rules or "none"),
          [work / f"{sku.lower()}-03-product-alone.png", prod], [f"Shows: {feature['describe']}"])
    return jobs


# ---------------- references ----------------
def fetch_refs(facts: dict, work: Path) -> None:
    """product.jpg (cleanest supplier photo) and detail.jpg (zoom crop of the chosen feature)."""
    from PIL import Image
    work.mkdir(parents=True, exist_ok=True)

    def get(url: str) -> bytes:
        return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read()

    Image.open(io.BytesIO(get(facts["refs"]["product"]))).convert("RGB").save(work / "product.jpg", quality=95)
    d = facts["refs"].get("detail")
    box = facts.get("detail_box")
    if d and box and len(box) == 4:
        im = Image.open(io.BytesIO(get(d))).convert("RGB")
        w, h = im.size
        x0, y0, x1, y1 = [float(v) for v in box]
        cx, cy = (x0 + x1) / 2 * w, (y0 + y1) / 2 * h
        bw, bh = max((x1 - x0) * w, 1), max((y1 - y0) * h, 1)
        if bw / bh > 3 / 4:
            bh = bw * 4 / 3
        else:
            bw = bh * 3 / 4
        bw, bh = min(bw * 1.15, w), min(bh * 1.15, h)
        left, top = max(0, min(w - bw, cx - bw / 2)), max(0, min(h - bh, cy - bh / 2))
        crop = im.crop((int(left), int(top), int(left + bw), int(top + bh)))
        if min(crop.size) < 1000:
            k = 1000 / min(crop.size)
            crop = crop.resize((int(crop.size[0] * k), int(crop.size[1] * k)), Image.LANCZOS)
        crop.save(work / "detail.jpg", quality=95)


# ---------------- generation ----------------
def _client():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise Stop("GEMINI_API_KEY is not set")
    from google import genai
    return genai.Client(api_key=key)


def _parts(prompt: str, refs: list[str]):
    from google.genai import types
    parts = [types.Part.from_text(text=prompt)]
    for r in refs:
        p = Path(r)
        if p.exists():
            parts.append(types.Part.from_bytes(data=p.read_bytes(), mime_type=mimetypes.guess_type(p.name)[0] or "image/png"))
    return parts


def _generate(client, job: dict, prompt: str) -> bytes | None:
    from google.genai import types
    ic = {"aspect_ratio": job.get("aspect_ratio", "3:4")}
    if job.get("image_size"):
        ic["image_size"] = job["image_size"]
    cfg = types.GenerateContentConfig(image_config=types.ImageConfig(**ic))
    model = job["model"]
    try:
        r = client.models.generate_content(model=model, contents=[types.Content(role="user", parts=_parts(prompt, job["refs"]))], config=cfg)
    except Exception as e:  # noqa: BLE001
        if model != FLASH_MODEL and ("NOT_FOUND" in str(e) or "404" in str(e)):
            job["model"] = FLASH_MODEL
            r = client.models.generate_content(model=FLASH_MODEL, contents=[types.Content(role="user", parts=_parts(prompt, job["refs"]))],
                                               config=types.GenerateContentConfig(image_config=types.ImageConfig(aspect_ratio=ic["aspect_ratio"])))
        else:
            raise
    for c in r.candidates or []:
        for part in c.content.parts:
            if part.inline_data:
                return part.inline_data.data
    return None


def _vet(client, img: bytes, job: dict) -> dict:
    from google.genai import types
    text = VET + ("\nChecks for this shot:\n- " + "\n- ".join(job["checks"]) if job["checks"] else "") + f"\n\nPrompt used:\n{job['prompt'][:1500]}"
    parts = [types.Part.from_text(text=text), types.Part.from_bytes(data=img, mime_type="image/png")] + _parts("", job["refs"])[1:]
    last = None
    for m in VISION_MODELS:
        try:
            r = client.models.generate_content(model=m, contents=[types.Content(role="user", parts=parts)])
            t = (r.text or "{}").strip().strip("`")
            t = t[4:] if t.lower().startswith("json") else t
            v = json.loads(t[t.find("{"): t.rfind("}") + 1])
            return {"pass": bool(v.get("pass", True)), "issues": v.get("issues") or []}
        except Exception as e:  # noqa: BLE001
            last = e
    return {"pass": True, "issues": [f"vision check unavailable: {str(last)[:80]}"]}   # never block on a dead checker


def _crop(data: bytes, ratio: float) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    if abs(w / h - ratio) > 0.004:
        if w / h > ratio:
            nw = int(h * ratio)
            im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:
            nh = int(w / ratio)
            im = im.crop((0, 0, w, nh))   # keep the top (heads) when trimming height
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def spend(store: Store) -> dict:
    s = store.load("spend.json") or {}
    if s.get("date") != today():
        s = {"date": today(), "images": 0, "usd": 0.0}
    return s


def run_jobs(store: Store, jobs: list[dict], log: Log) -> list[dict]:
    """Generate every job (in order: 03 before 05). Returns jobs with status done | flagged | failed."""
    s = spend(store)
    cap = int(store.get("images_per_day") or 200)
    need = sum(j["attempts"] for j in jobs)
    if s["images"] + need > cap:
        raise Stop(f"daily image cap: {s['images']} used + up to {need} needed > {cap}")
    client = _client()
    ratio = float(store.get("image_ratio") or 0.8)
    for idx, j in enumerate(jobs):
        if any(x["key"] in ("01-studio", "02-hero") and x["status"] == "failed" for x in jobs[:idx]):
            j["status"], j["issues"] = "skipped", ["studio or hero failed: not paying for the rest"]
            log(f"  {j['key']:17} skipped")
            continue
        if j["key"] == "05-feature" and not Path(jobs[0]["out"]).with_name(Path(j["out"]).name.replace("05-feature", "03-product-alone")).exists():
            j["status"], j["issues"] = "skipped", ["needs the product-alone photo first"]
            log(f"  {j['key']:17} skipped (no product-alone photo)")
            continue
        prompt = j["prompt"]
        j["status"], j["issues"] = "failed", []
        for attempt in range(j["attempts"]):
            img = None
            for tryn in range(3):   # 429 / safety block / network: wait and try again before giving up on this shot
                try:
                    img = _generate(client, j, prompt)
                    break
                except Exception as e:  # noqa: BLE001
                    j["issues"] = [f"generator error: {str(e)[:120]}"]
                    import time as _t
                    _t.sleep(10 * (tryn + 1))
            s["images"] += 1
            s["usd"] = round(s["usd"] + COST.get(j["model"], 0.1), 3)
            if not img:
                j["issues"] = ["generator returned no image"]
                continue
            img = _crop(img, ratio)
            Path(j["out"]).write_bytes(img)
            if not j.get("vet"):
                j["status"] = "done"
                break
            v = _vet(client, img, j)
            if v["pass"]:
                j["status"], j["issues"] = "done", v["issues"]
                break
            j["status"], j["issues"] = "flagged", v["issues"]
            prompt = j["prompt"] + " FIX THESE ISSUES FROM THE LAST TRY: " + "; ".join(v["issues"])
        log(f"  {j['key']:17} {j['status']:8} {j['model']:28} {('; '.join(j['issues']))[:120]}")
        store.save("spend.json", s)
    return jobs

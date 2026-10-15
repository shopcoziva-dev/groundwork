"""Groundwork core: where a store's files live, how settings load, and the run log.

Everything for one store lives in stores/<slug>/ (small JSON files that get committed):
  store.json      settings: shop domain, sku prefix, CJ shop id, genders, caps, dry-run flag
  scout.json      Scout verdict per corner (stage 1)
  profile.json    the niche profile Claude wrote: product types, photo recipes, rules, voice (stage 2)
  shelf.json      products picked for the catalog, priced (stage 3)
  brand.json      name, tagline, colours, fonts, domain (stage 4)
  store_setup.json what store setup wrote to Shopify (stage 5)
  market/<key>.json  collected ad prices per product
  listings/<SKU>.json  one record per listing (draft -> live), with every gate result
  ads.json        PMax campaign settings + checklist (stage 7)
  runs/<date>-<what>.md  plain-text log of each run
Secrets never live here; they come from env only and are never printed.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GW = REPO / "groundwork"
STORES = REPO / "stores"

DEFAULT_STORE = {
    "name": "",                 # brand name once picked
    "niche": "",
    "slug": "",
    "shop_domain": "",          # xxxx.myshopify.com
    "public_domain": "",        # www.brand.com (storefront 200 check); falls back to shop_domain
    "sku_prefix": "",           # e.g. "GW" -> GW001; set once, never changed after the first listing
    "cj_shop_id": "",           # CJ's id for this Shopify store (cj shops list); needed to link products
    "genders": ["women", "men"],  # or ["unisex"] for products not sold by gender
    "catalog_target": 50,
    "images_per_day": 200,
    "image_mode": "cheap",      # cheap: people shots on the pro model + checked, product shots on flash (Coziva default)
    "channels": ["Online Store", "Google & YouTube"],
    "free_shipping_over": 70,
    "returns_days": 30,
    "ship_days": "7-15 business days",
    "discount_code_pct": 0.10,  # the standing public code the pricing floor assumes (0 = none)
    "compare_at": None,         # "was" price: Solo stores start False (real prices, opt-in); None = the beta's +$30
    "dry_run": True,            # safety: nothing is written to Shopify/CJ until Jason flips this to false
}


class Stop(Exception):
    """A gate failed. The message says why in plain words."""


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:48] or "store"


def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise Stop(f"{p} is not valid JSON: {e}")


def write_json(p: Path, data) -> Path:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{id(data)}.tmp")   # atomic: a parallel lane never reads a half-written file
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, p)
    return p


class Store:
    """One store's folder and settings."""

    def __init__(self, slug: str):
        self.slug = slugify(slug)
        self.dir = STORES / self.slug
        self.settings = {**DEFAULT_STORE, **(read_json(self.dir / "store.json", {}) or {})}
        self.settings["slug"] = self.slug

    # ---- files ----
    def path(self, *parts) -> Path:
        return self.dir.joinpath(*parts)

    def load(self, name: str, default=None):
        return read_json(self.path(name), default)

    def save(self, name: str, data) -> Path:
        return write_json(self.path(name), data)

    def save_settings(self) -> None:
        self.save("store.json", self.settings)

    @property
    def exists(self) -> bool:
        return (self.dir / "store.json").exists()

    @property
    def dry_run(self) -> bool:
        # env can force a dry run for any store; it can never force a live run
        return bool(self.settings.get("dry_run", True)) or os.environ.get("GW_DRY_RUN", "") == "1"

    def get(self, k, default=None):
        return self.settings.get(k, default)

    # ---- env per store: SHOPIFY_CLIENT_ID_<SLUG> wins over the plain name ----
    def secret(self, name: str) -> str:
        """This store's own login, never another store's: NAME_<SLUG> (local runs) or GW_NAME (set in the store's GitHub
        environment, see groundwork.yml). The plain NAME (Coziva's secret) is deliberately NOT used."""
        key = re.sub(r"[^A-Z0-9]", "_", self.slug.upper())
        names = [f"{name}_{key}"] + ([f"GW_{name}"] if os.environ.get("GITHUB_ACTIONS") == "true" else [])   # GW_ = this run's environment, CI only
        for k in names:
            v = os.environ.get(k, "").strip()
            if v:
                return v
        return ""

    @classmethod
    def create(cls, niche: str, slug: str | None = None) -> "Store":
        s = cls(slug or niche)
        if s.exists:
            raise Stop(f"store '{s.slug}' already exists (stores/{s.slug}); pick another slug")
        s.settings.update(niche=niche.strip(), slug=s.slug)
        s.save_settings()
        return s


class Log:
    """Prints and keeps lines; save() writes stores/<slug>/runs/<date>-<what>.md."""

    def __init__(self, store: Store | None, what: str):
        self.store, self.what, self.lines = store, what, []

    def __call__(self, s: str = "") -> None:
        print(s, flush=True)
        self.lines.append(str(s))

    def save(self) -> Path | None:
        if not self.store:
            return None
        p = self.store.path("runs", f"{_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d-%H%M%S')}-{slugify(self.what)}.md")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(self.lines) + "\n")
        return p


def die(msg: str, code: int = 1):
    print(f"STOP: {msg}", file=sys.stderr)
    sys.exit(code)

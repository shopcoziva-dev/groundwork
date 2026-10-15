"""CJdropshipping API for Groundwork (same calls the Coziva pipeline proved: listV2, product/query, freight, stock, link).

Env: CJ_API_KEY (never printed). CJ allows about one access-token request per 5 minutes, so the token is cached in
cjtoken.txt (git-ignored) and shared by every call in a run, exactly like Coziva.
Writes (saveProduct / saveVariantBatch / connection) only happen on live runs.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path

from .core import Log, Stop, Store
from .shopify import _http

BASE = "https://developers.cjdropshipping.com/api2.0/v1"
TOKEN_FILE = Path(os.environ.get("CJ_TOKEN_FILE", "cjtoken.txt"))
_NON_US = re.compile(r"(?<![A-Za-z])(EU|UK|AU)(?![A-Za-z])|-(EU|UK|AU)\b|\b(EU|UK|AU) plug", re.I)


def num(x):
    try:
        return float(str(x).split("--")[0].split("-")[0].strip())
    except Exception:  # noqa: BLE001
        return None


_PACE = threading.Lock()
_LAST = [0.0]
_TOK = threading.Lock()


def _pace(gap: float = 1.1) -> None:
    """CJ allows about 1 call a second for the whole account: one shared clock for every lane/thread."""
    with _PACE:
        wait = _LAST[0] + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST[0] = time.monotonic()


class CJ:
    def __init__(self, store: Store, log: Log):
        self.store, self.log = store, log
        self._tok = ""

    def call(self, method: str, path: str, params: dict | None = None, body: dict | None = None, auth: bool = True) -> dict:
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(4):
            _pace()   # CJ rate limit: about 1 call a second, shared across lanes
            code, d = _http(method, url, {"CJ-Access-Token": self.token()} if auth else {}, body)
            if code == 429 or (isinstance(d, dict) and d.get("code") in (1600200, 1600201)):   # too many requests
                time.sleep(10 * (attempt + 1))
                continue
            return d if isinstance(d, dict) else {}
        return {}

    def token(self) -> str:
        if self._tok:
            return self._tok
        with _TOK:   # one login for all lanes
            return self._token_locked()

    def _token_locked(self) -> str:
        if TOKEN_FILE.exists() and TOKEN_FILE.read_text().strip():
            self._tok = TOKEN_FILE.read_text().strip()
            return self._tok
        key = os.environ.get("CJ_API_KEY", "").strip()
        if not key:
            raise Stop("CJ_API_KEY is not set")
        for attempt in range(4):
            code, d = _http("POST", BASE + "/authentication/getAccessToken", {}, {"apiKey": key})
            tok = ((d or {}).get("data") or {}).get("accessToken")
            if tok:
                self._tok = tok
                TOKEN_FILE.write_text(tok)
                return tok
            time.sleep(15 * (attempt + 1))
        raise Stop("CJ login failed (no access token)")

    # ---- catalog ----
    def search(self, keyword: str, size: int = 40, min_price: float = 3, max_price: float = 60) -> list[dict]:
        r = self.call("GET", "/product/listV2", {"keyWord": keyword, "page": 1, "size": size, "orderBy": 1, "sort": "desc",
                                                  "startSellPrice": min_price, "endSellPrice": max_price, "features": "enable_category"})
        rows = [p for blk in (r.get("data") or {}).get("content", []) for p in blk.get("productList", [])]
        out = []
        for p in rows:
            out.append({"pid": str(p.get("id")), "name": str(p.get("nameEn") or "")[:160], "stores": int(p.get("listedNum") or 0),
                        "price": num(p.get("nowPrice") or p.get("sellPrice")), "image": p.get("bigImage") or "", "found_kw": keyword})
        out.sort(key=lambda x: -x["stores"])   # listedNum = how many stores already sell it (proof it sells)
        return out

    def product(self, pid: str) -> dict:
        det = self.call("GET", "/product/query", {"pid": pid}).get("data") or {}
        if not det:
            raise Stop(f"CJ returned no product for {pid}")
        imgs: list[str] = []
        raw = det.get("productImage") or det.get("productImageSet") or ""
        if isinstance(raw, str) and raw.strip().startswith("["):
            try:
                imgs = json.loads(raw)
            except Exception:  # noqa: BLE001
                imgs = []
        elif isinstance(raw, list):
            imgs = raw
        elif isinstance(raw, str) and raw:
            imgs = [raw]
        if det.get("bigImage") and det["bigImage"] not in imgs:
            imgs.insert(0, det["bigImage"])
        variants = []
        for v in det.get("variants") or []:
            name = v.get("variantNameEn") or v.get("variantKey") or ""
            if _NON_US.search(f"{name} {v.get('variantKey') or ''}"):
                continue   # US store: drop variants made for other markets (EU/UK/AU plugs)
            variants.append({"vid": v.get("vid"), "name": name, "key": v.get("variantKey") or "", "cost": num(v.get("variantSellPrice")),
                             "image": v.get("variantImage"), "sku": v.get("variantSku")})
            if v.get("variantImage") and v["variantImage"] not in imgs:
                imgs.append(v["variantImage"])
        desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(det.get("description") or ""))).strip()[:3000]
        return {"pid": det.get("pid") or pid, "name": det.get("productNameEn") or det.get("nameEn") or "", "desc": desc,
                "category": det.get("categoryName"), "images": imgs[:12], "variants": variants, "spu": det.get("productSku"),
                "weight": det.get("productWeight")}

    def freight(self, vids: list[str], max_days: int = 12) -> dict | None:
        """Cheapest US method for these variants within max_days (13-14 days only if it saves $5+). CN first, then US warehouse."""
        best = None
        for origin in ("CN", "US"):
            per = []
            for vid in list(dict.fromkeys([vids[0], vids[len(vids) // 2], vids[-1]]))[:3]:
                f = self.call("POST", "/logistic/freightCalculate", body={"startCountryCode": origin, "endCountryCode": "US", "products": [{"quantity": 1, "vid": vid}]})
                opts = {}
                for o in (f.get("data") if isinstance(f.get("data"), list) else []):
                    pr = num(o.get("logisticPrice"))
                    ds = [int(x) for x in re.findall(r"\d+", str(o.get("logisticAging") or ""))]
                    if pr is not None:
                        opts[o.get("logisticName")] = (pr, max(ds) if ds else 99)
                per.append(opts)
            common = set.intersection(*[set(o) for o in per]) if per and all(per) else set()
            rows = sorted((max(o[n][0] for o in per), max(o[n][1] for o in per), n) for n in common)
            ok = [r for r in rows if r[1] <= max_days]
            slow = [r for r in rows if max_days < r[1] <= 14]
            pick = ok[0] if ok else None
            if ok and slow and ok[0][0] - slow[0][0] >= 5:
                pick = slow[0]
            if pick:
                return {"price": round(pick[0], 2), "days": pick[1], "method": pick[2], "origin": origin}
            if rows and not best:
                best = {"price": round(rows[0][0], 2), "days": rows[0][1], "method": rows[0][2], "origin": origin}
        return best

    def stock(self, vids: list[str]) -> int | None:
        tot, seen = 0, False
        for vid in vids[:12]:
            r = self.call("GET", "/product/stock/queryByVid", {"vid": vid})
            for w in (r.get("data") if isinstance(r.get("data"), list) else []):
                n = w.get("totalInventoryNum") if w.get("totalInventoryNum") is not None else w.get("storageNum")
                if n is not None:
                    tot += int(n)
                    seen = True
        return tot if seen else None

    def shops(self) -> list[dict]:
        """CJ's list of connected stores (to find cj_shop_id for a new Shopify store)."""
        d = self.call("GET", "/shop/getShops").get("data")
        return d if isinstance(d, list) else ((d or {}).get("list") or [])

    # ---- link a Shopify product to its CJ product (live runs only) ----
    def link(self, shop_product_id: str, title: str, image: str, pairs: list[dict], cj_pid: str, logistics: str, price: float, origin: str = "CN") -> dict:
        """pairs: [{cj_vid, shop_vid, sku, name}]. Returns {verified, missing, detail}."""
        shop = self.store.get("cj_shop_id")
        if not shop:
            raise Stop("cj_shop_id missing in store.json (run `gw.py cj-shops --store <slug>` to find it)")
        img = image or "https://example.com/x.jpg"
        r1 = self.call("POST", "/store/product/saveProduct", body={"id": shop_product_id, "shopId": shop, "title": title, "image": img, "description": title,
                                                                    "priceMin": price, "priceMax": price, "priceCurrency": "USD"})
        r2 = self.call("POST", "/store/product/saveVariantBatch", body={"shopId": shop, "variants": [
            {"id": p["shop_vid"], "productId": shop_product_id, "title": p["name"], "sku": p["sku"], "image": img, "shopPrice": price, "shopPriceCurrency": "USD"} for p in pairs]})
        bad = [x for x in (r2.get("data") or []) if not x.get("saveSuccess")]
        if not r1.get("result") or bad:
            return {"verified": False, "missing": len(pairs), "detail": f"saveProduct={r1.get('message')} variants failed={len(bad)}"}
        area = 1 if origin == "CN" else self.store.get(f"cj_area_{origin.lower()}")
        if not area:
            raise Stop(f"this product ships from CJ's {origin} warehouse; set cj_area_{origin.lower()} in store.json (the CJ area id) before linking it")
        conn = {"shopId": shop, "defaultArea": area, "logistics": logistics, "cjProductId": cj_pid, "platformProductId": shop_product_id,
                "sourceCountryCode": origin, "targetCountryCode": "US",
                "variantList": [{"cjVariantId": p["cj_vid"], "platformVariantId": p["shop_vid"]} for p in pairs]}
        r3 = self.call("POST", "/product/conn/connection", body=conn)
        want = {(str(p["cj_vid"]), str(p["shop_vid"])) for p in pairs}
        got = self.linked_pairs(shop_product_id)
        missing = want - got
        return {"verified": bool(r3.get("result")) and not missing, "missing": len(missing), "detail": f"{r3.get('message')}; {len(want) - len(missing)}/{len(want)} pairs read back"}

    def linked_pairs(self, shop_product_id: str) -> set:
        """(cjVariantId, platformVariantId) pairs CJ holds for this product (stage1 read-back)."""
        for attempt in range(4):   # CJ sometimes answers 0 pairs under load: retry before failing
            rows = (self.call("GET", "/product/conn/connection", {"shopId": self.store.get("cj_shop_id"), "platformProductId": shop_product_id,
                                                                   "pageNum": 1, "pageSize": 100}).get("data") or {}).get("list", [])
            if rows:
                return {(str(x.get("cjVariantId")), str(x.get("platformVariantId"))) for x in rows}
            time.sleep(6 * (attempt + 1))
        return set()

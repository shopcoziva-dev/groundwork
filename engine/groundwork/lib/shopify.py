"""Shopify Admin GraphQL for any Groundwork store.

Login, per store (env, never printed):
  SHOPIFY_CLIENT_ID_<SLUG> + SHOPIFY_CLIENT_SECRET_<SLUG>  (Dev Dashboard app, client-credentials grant, same as Coziva)
  or SHOPIFY_ADMIN_TOKEN_<SLUG>                            (custom app token, shpat_...)
The plain names (no _<SLUG>) work too, for a single store.

Reads always run. Writes go through write(): in a dry run they are logged with their variables and NOT sent.
"""
from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .core import Log, Stop, Store

API_VERSION = "2026-07"


def _http(method: str, url: str, headers: dict, body=None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return e.code, {"message": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return -1, {"message": type(e).__name__}


class Shopify:
    def __init__(self, store: Store, log: Log):
        self.store, self.log = store, log
        self.domain = store.get("shop_domain") or ""   # never a fallback: a store without its own domain reads nothing
        self._token = ""
        self.writes: list[dict] = []   # what a dry run would have sent (also kept on live runs)

    # ---- login ----
    @property
    def configured(self) -> bool:
        return bool(self.domain and (self.store.secret("SHOPIFY_ADMIN_TOKEN") or (self.store.secret("SHOPIFY_CLIENT_ID") and self.store.secret("SHOPIFY_CLIENT_SECRET"))))

    def token(self) -> str:
        if self._token:
            return self._token
        if not self.domain:
            raise Stop("no Shopify store set: put shop_domain in stores/<slug>/store.json")
        static = self.store.secret("SHOPIFY_ADMIN_TOKEN")
        if static:
            self._token = static
            return static
        cid, sec = self.store.secret("SHOPIFY_CLIENT_ID"), self.store.secret("SHOPIFY_CLIENT_SECRET")
        if not (cid and sec):
            raise Stop(f"no Shopify login for {self.store.slug}: set GW_SHOPIFY_CLIENT_ID + GW_SHOPIFY_CLIENT_SECRET in the store's GitHub environment "
                       f"(or SHOPIFY_CLIENT_ID_<SLUG> / SHOPIFY_ADMIN_TOKEN_<SLUG> locally)")
        code, d = _http("POST", f"https://{self.domain}/admin/oauth/access_token", {},
                        {"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"})
        tok = d.get("access_token")
        if not tok:
            raise Stop(f"Shopify login failed: http={code} {d.get('error_description') or d.get('error') or d.get('message')}")
        self.log(f"Shopify login OK for {self.domain} (token not printed); scopes: {d.get('scope')}")
        self._token = tok
        return tok

    # ---- GraphQL ----
    def gql(self, query: str, variables: dict | None = None, safe_to_repeat: bool = True) -> dict:
        """Any failure (network, 5xx, GraphQL error, no data) raises Stop: a failed call must never look like an empty answer.
        Throttled calls are always retried (Shopify did not run them). Network errors / 5xx are retried only when
        safe_to_repeat (reads): a write that timed out may already have happened, and repeating it could duplicate it."""
        for attempt in range(5):
            code, d = _http("POST", f"https://{self.domain}/admin/api/{API_VERSION}/graphql.json",
                            {"X-Shopify-Access-Token": self.token()}, {"query": query, "variables": variables or {}})
            throttled = code == 429 or any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in (d.get("errors") or []) if isinstance(e, dict))
            if throttled or (safe_to_repeat and code in (-1, 500, 502, 503, 504)):
                time.sleep(2 * (attempt + 1))
                continue
            break
        if d.get("errors"):
            raise Stop(f"Shopify GraphQL error (http {code}): {json.dumps(d['errors'])[:400]}")
        if code != 200 or "data" not in d or d["data"] is None:
            raise Stop(f"Shopify call failed (http {code}): {json.dumps(d)[:300]}")
        return d["data"]

    def read(self, query: str, variables: dict | None = None) -> dict:
        if not self.configured:
            if self.store.dry_run:
                self.log("  (dry run, no Shopify login: read skipped)")
                return {}
            self.token()   # raises the clear "no login" Stop
        return self.gql(query, variables)

    def write(self, what: str, query: str, variables: dict | None = None, result_key: str | None = None) -> dict | None:
        """A mutation. Dry run: logged, not sent, returns None. Live: sent; userErrors -> Stop."""
        self.writes.append({"what": what, "variables": variables or {}})
        if self.store.dry_run:
            self.log(f"  DRY RUN would {what}")
            return None
        d = self.gql(query, variables, safe_to_repeat=False)
        if result_key:
            res = d.get(result_key) or {}
            errs = res.get("userErrors") or res.get("mediaUserErrors") or []
            if errs:
                raise Stop(f"Shopify refused to {what}: {json.dumps(errs)[:400]}")
            return res
        return d

    # ---- helpers used by several stages ----
    def publications(self) -> list[tuple[str, str]]:
        nodes = (self.read("query{ publications(first:30){ nodes{ id name } } }").get("publications") or {}).get("nodes") or []
        return [(n["id"], n["name"]) for n in nodes]

    def staged_upload(self, path: Path, filename: str) -> str:
        """Upload one local image; returns the resourceUrl to attach. Live runs only."""
        data = Path(path).read_bytes()
        ctype = mimetypes.guess_type(Path(path).name)[0] or "image/png"
        st = self.gql("mutation($in:[StagedUploadInput!]!){ stagedUploadsCreate(input:$in){ stagedTargets{ url resourceUrl parameters{ name value } } userErrors{ field message } } }",
                      {"in": [{"resource": "IMAGE", "filename": filename, "mimeType": ctype, "httpMethod": "POST", "fileSize": str(len(data))}]})["stagedUploadsCreate"]
        if st["userErrors"] or not st["stagedTargets"]:
            raise Stop(f"staged upload {filename}: {st['userErrors']}")
        t = st["stagedTargets"][0]
        b = uuid.uuid4().hex
        body = b""
        for p in t["parameters"]:
            body += f'--{b}\r\nContent-Disposition: form-data; name="{p["name"]}"\r\n\r\n{p["value"]}\r\n'.encode()
        body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
                 + data + f"\r\n--{b}--\r\n".encode())
        urllib.request.urlopen(urllib.request.Request(t["url"], data=body, method="POST",
                                                      headers={"Content-Type": f"multipart/form-data; boundary={b}"}), timeout=120).read()
        return t["resourceUrl"]

    def storefront_ok(self, handle: str, tries: int = 6) -> tuple[str, int]:
        host = self.store.get("public_domain") or self.domain
        url = f"https://{host}/products/{handle}"
        code = -1
        for _ in range(tries):
            try:
                code = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).status
            except urllib.error.HTTPError as e:
                code = e.code
            except Exception:  # noqa: BLE001
                code = -1
            if code == 200:
                break
            time.sleep(4)
        return url, code

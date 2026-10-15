"""Google Ads API, read-only (REST). Used for Keyword Planner numbers (lib/demand) and daily ROAS (lib/ladder).
Nothing here creates, changes or spends anything.

Secrets (GitHub repo secrets, never printed): GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET,
GOOGLE_ADS_REFRESH_TOKEN, GOOGLE_ADS_CUSTOMER_ID (the account Keyword Planner runs in, digits only),
GOOGLE_ADS_LOGIN_CUSTOMER_ID (the manager account, optional), GOOGLE_ADS_API_VERSION (optional, default below).
"""
from __future__ import annotations

import os
import urllib.parse

from .shopify import _http

API_VERSION = "v21"
_KEYS = ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN")
_token = {"v": ""}


def _env(k: str) -> str:
    return os.environ.get(k, "").strip()


def ready(customer: str = "") -> bool:
    return all(_env(k) for k in _KEYS) and bool(customer or _env("GOOGLE_ADS_CUSTOMER_ID"))


def _cid(x: str) -> str:
    return "".join(c for c in str(x) if c.isdigit())


def _access() -> str:
    if _token["v"]:
        return _token["v"]
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": _env("GOOGLE_ADS_CLIENT_ID"),
                                   "client_secret": _env("GOOGLE_ADS_CLIENT_SECRET"), "refresh_token": _env("GOOGLE_ADS_REFRESH_TOKEN")}).encode()
    import json
    import urllib.request
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        _token["v"] = json.loads(r.read().decode())["access_token"]
    return _token["v"]


def call(path: str, body: dict, customer: str = "") -> dict:
    """POST customers/{cid}:{path}. Raises RuntimeError with Google's message (no secrets in it) on failure."""
    cid = _cid(customer or _env("GOOGLE_ADS_CUSTOMER_ID"))
    h = {"Authorization": f"Bearer {_access()}", "developer-token": _env("GOOGLE_ADS_DEVELOPER_TOKEN")}
    if _env("GOOGLE_ADS_LOGIN_CUSTOMER_ID"):
        h["login-customer-id"] = _cid(_env("GOOGLE_ADS_LOGIN_CUSTOMER_ID"))
    ver = _env("GOOGLE_ADS_API_VERSION") or API_VERSION
    code, js = _http("POST", f"https://googleads.googleapis.com/{ver}/customers/{cid}{path}", h, body, timeout=60)
    if code != 200:
        err = js[0] if isinstance(js, list) and js else js
        msg = ((err or {}).get("error") or {}).get("message") or (err or {}).get("message") or f"HTTP {code}"
        raise RuntimeError(f"Google Ads API: {str(msg)[:200]}")
    return js

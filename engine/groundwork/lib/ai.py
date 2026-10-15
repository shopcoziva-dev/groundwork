"""Claude calls for Groundwork: plain JSON answers, answers with web search, and answers about photos.

Same API call the Coziva pipeline already uses (urllib, no SDK), plus:
  - web search (Anthropic server tool) for Scout, market prices and brand checks
  - one automatic retry with the reason when an answer fails its checks
  - a mock mode for offline tests: GW_AI_MOCK=<folder> answers task "x" from <folder>/x.json

Env: ANTHROPIC_API_KEY (never printed). GW_MODEL picks the model (default claude-sonnet-5-5, the pipeline's model).
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .core import Stop
from . import engine

MODEL = os.environ.get("GW_MODEL", "claude-sonnet-5-5")
API = "https://api.anthropic.com/v1/messages"
_MOCK_N: dict[str, int] = {}
USAGE = {"input_tokens": 0, "output_tokens": 0, "web_searches": 0, "calls": 0}


def _post(body: dict, timeout: int = 300) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise Stop("ANTHROPIC_API_KEY is not set")
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    last = ""
    for attempt in range(4):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except urllib.error.HTTPError as e:
            last = f"{e.code}: {e.read().decode()[:300]}"
            if e.code in (429, 500, 502, 503, 529):
                time.sleep(8 * (attempt + 1))
                continue
            raise Stop(f"Anthropic API error {last}")
        except (urllib.error.URLError, TimeoutError) as e:
            last = repr(e)[:200]
            time.sleep(5 * (attempt + 1))
    raise Stop(f"Anthropic API unreachable after retries ({last})")


def _count(r: dict) -> None:
    u = r.get("usage") or {}
    USAGE["calls"] += 1
    USAGE["input_tokens"] += int(u.get("input_tokens") or 0)
    USAGE["output_tokens"] += int(u.get("output_tokens") or 0)
    USAGE["web_searches"] += int(((u.get("server_tool_use") or {}).get("web_search_requests")) or 0)


def ask(system: str, content: list | str, *, web: int = 0, max_tokens: int = 4000, model: str | None = None) -> str:
    """One Claude answer as text. web=N lets Claude run up to N web searches first."""
    if engine.cli_mode():   # Beta: the user's own Claude subscription through Claude Code
        USAGE["calls"] += 1
        return engine.cli_ask(system, content, web=web)
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    messages = [{"role": "user", "content": content}]
    body = {"model": model or MODEL, "max_tokens": max_tokens, "system": system, "messages": messages}
    if web:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": int(web)}]
    text_parts: list[str] = []
    for _ in range(4):   # a long search can pause the turn; send it back to continue
        r = _post(body)
        _count(r)
        if r.get("stop_reason") == "max_tokens" and body["max_tokens"] < 16000:   # cut off: ask again with room to finish
            body["max_tokens"] = min(16000, body["max_tokens"] * 2)
            continue
        blocks = r.get("content") or []
        text_parts += [b.get("text", "") for b in blocks if b.get("type") == "text"]
        if r.get("stop_reason") != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": blocks}]
        body["messages"] = messages
    return "".join(text_parts)


def parse_json(t: str):
    """First JSON object or array in the reply (tolerates ```json fences and prose around it)."""
    t = t.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i = t.find(opener)
        j = t.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise Stop(f"no JSON in the model reply: {t[:200]!r}")


def ask_json(task: str, system: str, content: list | str, *, check: Callable[[object], list[str]] | None = None,
             web: int = 0, max_tokens: int = 4000, retries: int = 1, model: str | None = None):
    """Claude answer as JSON. `check(answer) -> [problems]`; any problem = one retry with the reasons, then Stop.
    `task` names the call (used by mock mode and logs)."""
    engine.take(task)   # Beta (GW_ENGINE=rules): only the three Beta AI jobs, capped per run; no-op for Solo
    mock = os.environ.get("GW_AI_MOCK", "").strip()
    if mock:
        f = Path(mock) / f"{task}.json"
        if not f.exists():
            raise Stop(f"mock answer missing: {f}")
        ans = json.loads(f.read_text())
        if isinstance(ans, dict) and "_seq" in ans:   # a different answer per call, in order (last one repeats)
            n = _MOCK_N.get(task, 0)
            _MOCK_N[task] = n + 1
            ans = ans["_seq"][min(n, len(ans["_seq"]) - 1)]
        errs = check(ans) if check else []
        if errs:
            raise Stop(f"{task}: mock answer fails its checks: {'; '.join(errs)}")
        return ans
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    sys_full = system + "\nAnswer with ONLY the JSON, no prose before or after it."
    errs: list[str] = []
    for attempt in range(retries + 1):
        msg = content if attempt == 0 else content + [{"type": "text", "text": "Your previous answer was rejected: " + "; ".join(errs)
                                                       + ". Fix every point and answer again with the complete JSON."}]
        try:
            ans = parse_json(ask(sys_full, msg, web=web, max_tokens=max_tokens, model=model))
        except Stop as e:
            errs = [str(e)]
            continue
        try:
            errs = check(ans) if check else []
        except Exception as e:  # noqa: BLE001 - a malformed answer (e.g. a list) is a rejected answer, not a crash
            errs = [f"answer has the wrong shape ({type(e).__name__})"]
        if not errs:
            return ans
    raise Stop(f"{task}: answer rejected twice: {'; '.join(errs)}")


def image_block(url_or_path: str) -> dict:
    """An image for Claude from a URL or a local file (sniffs the real type; CJ serves some .jpg as WebP)."""
    s = str(url_or_path)
    if s.startswith("http"):
        data = urllib.request.urlopen(urllib.request.Request(s, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read()
    else:
        data = Path(s).read_bytes()
    if data[:4] == b"\x89PNG":
        mt = "image/png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mt = "image/webp"
    elif data[:3] == b"GIF":
        mt = "image/gif"
    else:
        mt = "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(data).decode()}}


def photos(urls: list[str], limit: int = 8, label: str = "Photo") -> list[dict]:
    """Numbered photos for a prompt; a photo that will not download is skipped (noted in the text)."""
    out: list[dict] = []
    for i, u in enumerate(urls[:limit]):
        try:
            out += [{"type": "text", "text": f"{label} {i}:"}, image_block(u)]
        except Exception as e:  # noqa: BLE001 - a dead supplier photo must not stop the run
            out.append({"type": "text", "text": f"{label} {i}: (could not download: {type(e).__name__})"})
    return out


def usage_line() -> str:
    u = USAGE
    # rough cost at Sonnet-class list prices ($3 / $15 per M tokens, $10 per 1k searches); a guide, not a bill
    est = u["input_tokens"] * 3e-6 + u["output_tokens"] * 15e-6 + u["web_searches"] * 0.01
    if engine.rules():
        return f"Claude (your plan): {u['calls']} calls {engine.usage() or ''}"
    return f"Claude: {u['calls']} calls, {u['input_tokens']} in / {u['output_tokens']} out tokens, {u['web_searches']} web searches (~${est:.2f})"

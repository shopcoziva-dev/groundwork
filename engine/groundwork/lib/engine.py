"""Groundwork Beta engine mode (GW_ENGINE=rules).

Beta runs the same pipeline as Solo, on the user's own GitHub and accounts, with two differences:
  1. Every decision is made by code (lib/rulebook) except three jobs where code is clearly worse:
       market_web     current ad prices for a product type (web search)
       gate_classify  "is this supplier product really one of the store's types, or an accessory/part?"
       copy           the listing copy for one product
  2. Those three use the USER'S Claude, capped per run so a run can never burn through their plan:
       - ANTHROPIC_API_KEY set           -> the normal API (pay per use)
       - else CLAUDE_CODE_OAUTH_TOKEN set -> Claude Code on their Pro/Max subscription (`claude -p`)
     When Claude is missing or a cap is reached, classify and copy fall back to the rulebook; market prices stop with a
     plain reason (they cannot be guessed).
Solo customer copy (GW_ENGINE=max): the full Solo pipeline (every AI decision, no rulebook, no per-task caps) on the
customer's own Claude Max plan through Claude Code. Images go to Claude Code as files it reads. A whole-run ceiling
(GW_MAX_CALLS, default 400) stops a runaway run.
Jason's own Solo is untouched: without GW_ENGINE every call behaves exactly as before.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from .core import Stop

AI_TASKS = ("market_web", "gate_classify", "copy")
CAP = {"market_web": 9, "gate_classify": 4, "copy": 12}   # Claude calls per run, per task
_used: dict[str, int] = {}


class NoAI(Stop):
    """This task may not use Claude in this run (rules mode, not connected, or over its cap)."""


def rules() -> bool:
    return os.environ.get("GW_ENGINE", "").strip().lower() == "rules"


def solo_max() -> bool:
    return os.environ.get("GW_ENGINE", "").strip().lower() == "max"


def claude_ready() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip() or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip())


def cap(task: str) -> int:
    try:
        return int(os.environ.get(f"GW_CAP_{task.upper()}", CAP.get(task, 0)))
    except ValueError:
        return CAP.get(task, 0)


def allowed(task: str) -> bool:
    """May this task ask Claude now? Always True outside rules mode."""
    if not rules():
        return True
    return task in AI_TASKS and claude_ready() and _used.get(task, 0) < cap(task)


def take(task: str) -> None:
    """Count one Claude call for this task; raises NoAI when it is not allowed."""
    if solo_max():
        _used[task] = _used.get(task, 0) + 1
        ceiling = int(os.environ.get("GW_MAX_CALLS", "400") or 400)
        if sum(_used.values()) > ceiling:
            raise Stop(f"this run reached its ceiling of {ceiling} Claude calls; run the step again to continue")
        return
    if not rules():
        return
    if not allowed(task):
        why = "not one of the Beta AI jobs" if task not in AI_TASKS else "Claude is not connected" if not claude_ready() else f"cap of {cap(task)} reached for this run"
        raise NoAI(f"{task}: {why}")
    _used[task] = _used.get(task, 0) + 1


def usage() -> dict:
    return dict(_used)


def cli_mode() -> bool:
    return (rules() or solo_max()) and not os.environ.get("ANTHROPIC_API_KEY", "").strip() and bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip())


def _image_files(content: list, folder: str) -> list[str]:
    """Writes the image blocks to files Claude Code can Read; returns their paths in order."""
    import base64
    paths = []
    ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
    for c in content:
        if isinstance(c, dict) and c.get("type") == "image" and (c.get("source") or {}).get("type") == "base64":
            src = c["source"]
            path = os.path.join(folder, f"image-{len(paths) + 1}{ext.get(src.get('media_type'), '.jpg')}")
            with open(path, "wb") as f:
                f.write(base64.b64decode(src["data"]))
            paths.append(path)
    return paths


def cli_ask(system: str, content: list | str, web: int = 0, timeout: int = 420, model: str | None = None) -> str:
    """One answer from Claude Code on the user's subscription. Images are handed over as files it reads."""
    import tempfile
    exe = shutil.which("claude")
    if not exe:
        raise Stop("Claude Code is not installed on the runner (the workflow installs it when CLAUDE_CODE_OAUTH_TOKEN is set)")
    folder = tempfile.mkdtemp(prefix="gw-ask-")
    parts, images = [], []
    if isinstance(content, str):
        parts = [content]
    else:
        images = _image_files(content, folder)
        n = 0
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "image" and n < len(images):
                parts.append(f"[image: read the file {images[n]}]")
                n += 1
    text = "\n\n".join(parts)
    if images:
        text = "Look at every image file named below with the Read tool before answering.\n\n" + text
    prompt = f"{system}\n\n{text}"
    tools = (["WebSearch"] if web else []) + (["Read"] if images else [])
    args = [exe, "-p", prompt, "--output-format", "json", "--max-turns", str(2 + (6 if web else 0) + len(images))]
    if tools:
        args += ["--allowedTools", ",".join(tools)]
    if model and solo_max():
        args += ["--model", model]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env={**os.environ}, cwd=folder)
    except subprocess.TimeoutExpired:
        raise Stop("Claude Code took too long")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["no output"]
        if "limit" in msg[0].lower():
            raise NoAI("your Claude plan hit its usage limit; try again later")
        raise Stop(f"Claude Code failed: {msg[0][:200]}")
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.stdout
    if out.get("is_error"):
        res = str(out.get("result"))
        if "limit" in res.lower():
            raise NoAI("your Claude plan hit its usage limit; try again later")
        raise Stop(f"Claude Code: {res[:200]}")
    return str(out.get("result") or "")

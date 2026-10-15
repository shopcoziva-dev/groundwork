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
Solo is untouched: without GW_ENGINE=rules every call behaves exactly as before.
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
    if not rules():
        return
    if not allowed(task):
        why = "not one of the Beta AI jobs" if task not in AI_TASKS else "Claude is not connected" if not claude_ready() else f"cap of {cap(task)} reached for this run"
        raise NoAI(f"{task}: {why}")
    _used[task] = _used.get(task, 0) + 1


def usage() -> dict:
    return dict(_used)


def cli_mode() -> bool:
    return rules() and not os.environ.get("ANTHROPIC_API_KEY", "").strip() and bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip())


def cli_ask(system: str, content: list | str, web: int = 0, timeout: int = 420) -> str:
    """One answer from Claude Code on the user's subscription. Text only (images are left out)."""
    exe = shutil.which("claude")
    if not exe:
        raise Stop("Claude Code is not installed on the runner (the workflow installs it when CLAUDE_CODE_OAUTH_TOKEN is set)")
    if isinstance(content, str):
        text = content
    else:
        text = "\n\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    prompt = f"{system}\n\n{text}"
    args = [exe, "-p", prompt, "--output-format", "json", "--max-turns", "8" if web else "2"]
    if web:
        args += ["--allowedTools", "WebSearch"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env={**os.environ})
    except subprocess.TimeoutExpired:
        raise Stop("Claude Code took too long")
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
        raise Stop(f"Claude Code: {str(out.get('result'))[:200]}")
    return str(out.get("result") or "")

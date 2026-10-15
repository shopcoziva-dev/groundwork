"""Store rules every stage shares: conversion rules (groundwork/rules/store-conversion.md) and no-slop design rules
(groundwork/rules/no-slop-design.md). Prompts get short excerpts; the banned-phrase check is code, so copy that uses
AI-slop wording is rejected and rewritten instead of published."""
from __future__ import annotations

import re
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "rules"

BANNED = [
    "elevate", "unlock", "discover", "unleash", "embark", "journey", "seamless", "effortless", "game-changer", "game changer",
    "revolutionary", "cutting-edge", "cutting edge", "next-level", "next level", "must-have", "must have", "premium quality",
    "high-quality", "high quality", "unparalleled", "unmatched", "world-class", "world class", "curated", "meticulously crafted",
    "designed with you in mind", "look no further", "in today's fast-paced world", "fast-paced world", "say goodbye to", "redefine",
    "transform your", "perfect for any occasion", "a testament to", "delve", "nestled",
]
_BANNED_RE = re.compile(r"\b(" + "|".join(re.escape(b) for b in sorted(BANNED, key=len, reverse=True)) + r")\w*", re.I)
_PATTERNS = [
    (re.compile(r"\btake your [\w\s]{1,30}? to the next level", re.I), "take your X to the next level"),
    (re.compile(r"\bwhether you(?:'|’)re [\w\s,]{1,40}? or ", re.I), "whether you're X or Y"),
    (re.compile(r"[\U0001F300-\U0001FAFF☀-➿]"), "emoji"),
]

COPY_VOICE = """VOICE (no AI slop): plain, specific statements a shopper can check; a number or fact instead of an adjective
("Three heat zones, up to 8 hours on low", not "unparalleled warmth"). Never use: """ + ", ".join(BANNED) + """, "take your X
to the next level", "whether you're X or Y". No invented claims, review counts, certifications or delivery times; no
three-adjective lists; no exclamation marks; no emoji. Lead with what it is, who it is for and the key spec."""


def slop_hits(text: str) -> list[str]:
    """Banned words/phrases and emoji found in text (HTML allowed). Empty list = clean."""
    t = re.sub(r"<[^>]+>", " ", str(text or ""))
    hits = sorted({m.group(1).lower() for m in _BANNED_RE.finditer(t)})
    hits += [name for rx, name in _PATTERNS if rx.search(t)]
    if t.count("!") > 1:
        hits.append("exclamation marks")
    return hits


def text(name: str) -> str:
    """The full rules file ('store-conversion' or 'no-slop-design')."""
    p = DIR / f"{name}.md"
    return p.read_text() if p.exists() else ""


IMAGE_CHECKS = [
    "No garbled or invented text on labels, tags, packaging or background",
    "No plastic sheen, over-smoothed skin or HDR halos; light and shadows physically plausible",
    "Scene is specific and believable, not a generic luxury loft or stock-photo setting",
]

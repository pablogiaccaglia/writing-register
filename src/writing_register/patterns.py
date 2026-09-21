"""The humanizer skill in a form small enough to travel everywhere.

wr has two sets of rules. The humanizer skill says what machine writing looks
like, and a voice says how one person wants to read. A rewrite has always
carried both in one call, with the voice winning where they disagree. Steering
carried only the voice, which left a user with no voice steered by nothing,
and left everyone writing without ever being told the patterns.

The skill is 28,728 characters, too much for every session and every subagent,
so what travels is this card, built from the skill's own numbered headings. It
cannot drift from the skill, because it is read from it, and a test fails when
the skill gains or renames a pattern. The full text stays one tool call away,
as the `writing-register:humanizer` skill, and `wr humanize` still sends it
whole.
"""
from __future__ import annotations

import re
from pathlib import Path

from .resources import SKILL

_HEADING = re.compile(r"(?m)^### (\d+)\. (.+)$")
_HEAD = ("Write prose that reads as a person wrote it. These are the patterns that make text "
         "read as machine-written, by number, from the humanizer skill:")
_TAIL = ("A pattern marked weak in the skill counts only with company in the same passage. Leave "
         "quotations, code, commands, paths and pre-2022 text alone. The skill "
         "`writing-register:humanizer` has each pattern in full, with examples.")


def patterns() -> list[tuple[str, str]]:
    """The skill's numbered patterns, as they are written in it."""
    try:
        text = SKILL.read_text(encoding="utf-8")
    except OSError:
        return []
    return [(number, name.strip()) for number, name in _HEADING.findall(text)]


def card() -> str:
    """The patterns in about 1,200 characters, for every session and subagent."""
    found = patterns()
    if not found:
        return ""
    listed = "; ".join(f"{number}. {name}" for number, name in found)
    return f"{_HEAD} {listed}. {_TAIL}"

"""The voice as a Claude Code output style.

Claude Code's own documentation names the output style as the way to change
how the model writes every turn: the style's text goes into the instructions
Claude Code gives Claude, it is sent with every request, and it survives
compaction, while `keep-coding-instructions: true` leaves the built-in
software-engineering instructions alone. That is a stronger place for a voice
than the session-start hook, whose text lands in the conversation.

A plugin cannot ship this file, because the voice belongs to the person, not
to the plugin. `wr style` writes it from whichever voice is active, and
`--enable` selects it in the user's settings.

What the style does not cover is traffic between agents: a subagent's report
to whoever called it, a message to another session, a prompt written for a
tool. A model reads those, and reads them better dense and plain, so the
voice is scoped to what a person reads (2026-09-16).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import ConfigError, load_config
from .voice import read_core
from .patterns import card

NAME = "writing-register"
SCOPE = """This style governs what a person reads: replies in this conversation, documentation and
READMEs, reports, meeting and work cards, code comments and docstrings, commit messages and pull
request descriptions, and messages written to a person.

It does not govern what another model reads. A report back to an agent that called you, a message
to another session or agent, a prompt written for a tool and any other agent-to-agent traffic stays
plain, dense and literal, because that text is read by a model and not by a person. Do not spend
words on register there.

Nothing here changes how you work: which tools you use, how you scope a change, how you verify it,
or what you say while doing it."""


class StyleError(Exception):
    """There is no voice to build a style from, or it cannot be written."""


def claude_dir() -> Path:
    """Claude Code's configuration directory."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def build() -> str:
    """The style file's text, from the active voice."""
    try:
        cfg = load_config()
    except ConfigError as e:
        raise StyleError(str(e)) from e
    core = ""
    if cfg.voice is not None:
        try:
            core = read_core(cfg.voice).strip()
        except (ConfigError, OSError) as e:
            raise StyleError(f"could not read {cfg.voice}: {e}") from e
    patterns_card = card()
    if not core and not patterns_card:
        raise StyleError("there is neither a voice nor a readable humanizer skill to "
                         "build a style from")
    who = f"in the {cfg.voice.stem} voice" if core else "so it reads as a person wrote it"
    precedence = ("\n\nFollow the voice above the patterns below: where they disagree, the voice "
                  "wins.") if core else ""
    body = "\n\n".join(p for p in (SCOPE + precedence, core, patterns_card) if p)
    return (f"---\nname: {NAME}\n"
            f"description: Write for people {who}, and keep agent traffic plain\n"
            "keep-coding-instructions: true\n---\n\n"
            f"{body}\n")


def install(enable: bool = False) -> Path:
    """Write the style where Claude Code reads it, and optionally select it."""
    text = build()
    target = claude_dir() / "output-styles" / f"{NAME}.md"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError as e:
        raise StyleError(f"could not write {target}: {e}") from e
    if enable:
        _select(NAME)
    return target


def _select(name: str) -> None:
    """Set `outputStyle` in the user's settings, leaving everything else."""
    path = claude_dir() / "settings.json"
    data = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError) as e:
            raise StyleError(f"could not read {path}: {e}") from e
    data["outputStyle"] = name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        raise StyleError(f"could not write {path}: {e}") from e


def active() -> bool:
    """Whether this style is the one Claude Code is set to use.

    The session-start hook asks, so that the voice does not travel twice: once
    in the system prompt and once in the conversation."""
    path = claude_dir() / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("outputStyle") == NAME

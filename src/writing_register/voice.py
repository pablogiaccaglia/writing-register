"""A voice, read from one file or from a directory of rule files.

A voice is a specification of how one person wants text written for them. It
began as a single markdown file, split by a marker into the core the model
receives and the evidence the people who maintain it keep. As it grew, rules
of different kinds overlapped, and an edit could put a rule into the core with
no evidence behind it (2026-09-21). So a voice can also be a directory:

    voice.toml      format, name, the order of the rule files, a budget
    rules/          what the model receives, in that order
    evidence/       dated quotes, before-and-after pairs, measurements
    decisions.md    where requests pulled in different directions
    about.md        how the voice was built
    maintaining.md  how to change it

A rule is one line that starts with a marker such as `{#register.no-dashes}`,
optionally `{#register.range refines=register.no-dashes}`. The markers let
evidence and decisions point at a rule, and they are removed from what the
model receives, where they would only cost characters and read as a database.

Every consumer receives one string, the core, exactly as before. The runtime
reads the manifest and the rule files and nothing else; every check on the
rest lives in `wr voice check`, so a malformed evidence file can never leave a
session without its voice.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, voice_core

MANIFEST = "voice.toml"
FORMAT = 1
_KEYS = {"format", "name", "budget", "evidence", "order"}
_EVIDENCE = {"optional", "required"}
MARKER = re.compile(r"\{#[a-z0-9][a-z0-9.-]*(?:\s+refines=[a-z0-9.,-]+)?\}\s?")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s")


class VoiceError(ConfigError):
    """A voice directory that cannot be read as a voice."""


@dataclass(frozen=True)
class Manifest:
    root: Path
    name: str
    order: tuple[str, ...]
    budget: int | None = None
    evidence: str = "optional"


def load(root) -> Manifest:
    """Read and validate a voice directory's manifest."""
    root = Path(root)
    path = root / MANIFEST
    if not path.is_file():
        raise VoiceError(f"{root} is a folder without {MANIFEST}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise VoiceError(f"could not read {path}: {e}") from e
    unknown = sorted(set(data) - _KEYS)
    if unknown:
        # A typo such as `oder` must not quietly empty the voice.
        raise VoiceError(f"{path} has unknown settings: {', '.join(unknown)}. "
                         f"The settings are: {', '.join(sorted(_KEYS))}")
    if data.get("format") != FORMAT:
        raise VoiceError(f"{path}: format must be {FORMAT}; this version of wr reads "
                         f"only that layout")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise VoiceError(f"{path}: name must be a non-empty string")
    order = data.get("order")
    if not isinstance(order, list) or not all(isinstance(o, str) for o in order):
        raise VoiceError(f"{path}: order must be a list of rule files, such as "
                         f"[\"reader.md\", \"register.md\"]")
    budget = data.get("budget")
    if budget is not None and (not isinstance(budget, int) or isinstance(budget, bool)
                               or budget <= 0):
        raise VoiceError(f"{path}: budget must be a positive number of characters")
    evidence = data.get("evidence", "optional")
    if evidence not in _EVIDENCE:
        raise VoiceError(f"{path}: evidence must be \"optional\" or \"required\"")
    return Manifest(root=root, name=name.strip(), order=tuple(order), budget=budget,
                    evidence=evidence)


def _split_title(text: str) -> tuple[str | None, list[str]]:
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip(), lines[1:]
    return None, lines


def _blocks(lines: list[str]) -> list[list[str]]:
    """The body as blocks, each a list of rendered runs: a paragraph is one run
    of lines joined by a space, and each list item is a run of its own."""
    blocks, block, prose = [], [], []

    def close_prose():
        if prose:
            block.append(" ".join(prose))
            prose.clear()

    for raw in lines + [""]:
        line = MARKER.sub("", raw).rstrip()
        if not line.strip():
            close_prose()
            if block:
                blocks.append(block)
                block = []
            continue
        if _LIST_ITEM.match(line):
            close_prose()
            block.append(line)
        else:
            prose.append(line.strip())
    return blocks


def _render(manifest: Manifest, rel: str) -> str:
    path = manifest.root / "rules" / rel
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise VoiceError(f"{manifest.root / MANIFEST} lists rules/{rel}, which does "
                         f"not exist") from e
    except (OSError, UnicodeDecodeError) as e:
        raise VoiceError(f"could not read {path}: {e}") from e
    title, body = _split_title(text)
    blocks = _blocks(body)
    rendered = ["\n".join(b) for b in blocks]
    if "/" in rel:
        # A file inside a folder belongs to the rule file of the same name, and
        # renders as a labelled paragraph under that file's heading.
        if title:
            label = f"**{title if title[-1] in '.!?:' else title + '.'}**"
            if not rendered:
                rendered = [label]
            elif _LIST_ITEM.match(blocks[0][0]):
                rendered[0] = f"{label}\n{rendered[0]}"
            else:
                rendered[0] = f"{label} {rendered[0]}"
        return "\n\n".join(rendered)
    if title:
        rendered.insert(0, f"## {title}")
    return "\n\n".join(rendered)


def assemble_core(manifest: Manifest) -> str:
    """The core: the title, then each rule file in the manifest's order."""
    parts = [f"# Voice: {manifest.name}"]
    for rel in manifest.order:
        chunk = _render(manifest, rel)
        if chunk:
            parts.append(chunk)
    return "\n\n".join(parts) + "\n"


def read_core(path) -> str:
    """What the model receives from a voice, file or directory."""
    path = Path(path)
    if path.is_dir():
        return assemble_core(load(path))
    return voice_core(path.read_text(encoding="utf-8"))


def read_full(path) -> str:
    """The whole voice: the core, the marker, then what people keep, which is
    the generated view without its notice. `wr humanize --full-voice` sends it."""
    path = Path(path)
    if not path.is_dir():
        return path.read_text(encoding="utf-8")
    from .voice_tools import render_view
    return render_view(path).partition("\n")[2]

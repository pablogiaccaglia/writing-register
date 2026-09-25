"""The user's configuration: which voice, if any, the rewrite is written in.

No voice is the default, so a teammate who installs writing-register gets the
humanizer skill alone. A person who wants a voice names it in their own
configuration file, which lives on their machine and is never committed to a
repository (2026-09-14).

    # ~/.config/writing-register/config.toml
    voice = "technical-colleague"

A voice file can be long. The model gets only its core, the text above the
line `<!-- wr:end-of-core -->`; what follows is evidence and examples kept for
the people who maintain the voice.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .resources import VOICES_DIR

REPO = Path(__file__).resolve().parents[2]
CONFIG_ENV = "WR_CONFIG"
CORE_MARKER = "<!-- wr:end-of-core -->"
KNOWN_KEYS = {"voice", "auto", "metrics"}
# What wr may rewrite by itself through the plugin's hooks (2026-09-15).
AUTO_VALUES = {"markdown", "commit", "pr", "notion", "mail", "discord"}


class ConfigError(Exception):
    """The configuration names something that does not exist or cannot be read."""


@dataclass(frozen=True)
class Config:
    path: Path
    voice: Path | None
    auto: frozenset = frozenset()
    # Whether the hooks keep a line about each rewrite (see metrics.py).
    metrics: bool = True


def config_path() -> Path:
    if os.environ.get(CONFIG_ENV):
        return Path(os.environ[CONFIG_ENV]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "writing-register" / "config.toml"


VOICE_MANIFEST = "voice.toml"


def _voice_names() -> list[str]:
    if not VOICES_DIR.is_dir():
        return []
    names = {p.stem for p in VOICES_DIR.glob("*.md")}
    names |= {p.name for p in VOICES_DIR.iterdir() if (p / VOICE_MANIFEST).is_file()}
    return sorted(names)


def resolve_voice(value: str, base: Path) -> Path | None:
    """A voice name, a path to a voice file or voice directory, or none.

    A value holding a slash or ending in .md is a path, read relative to `base`;
    it may be a file, or a directory holding a voice.toml. Anything else is the
    name of a voice in this clone's voice/ folder, where a directory of that
    name wins over a file of that name, because the file is the read-only view
    `wr voice build` generates from the directory (2026-09-21)."""
    value = value.strip()
    if value.lower() in ("", "none"):
        return None
    if "/" in value or value.endswith(".md"):
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        path = Path(os.path.normpath(path))
        if path.is_dir():
            if not (path / VOICE_MANIFEST).is_file():
                raise ConfigError(f"the voice {path} is a folder without {VOICE_MANIFEST}")
            return path
        if not path.is_file():
            raise ConfigError(f"the voice file {path} does not exist")
        return path
    folder = VOICES_DIR / value
    if (folder / VOICE_MANIFEST).is_file():
        return folder
    path = VOICES_DIR / f"{value}.md"
    if not path.is_file():
        known = ", ".join(_voice_names()) or "none"
        raise ConfigError(f"there is no voice named {value!r}; the voices in "
                          f"{VOICES_DIR} are: {known}")
    return path


def load_config() -> Config:
    path = config_path()
    if not path.is_file():
        return Config(path=path, voice=None)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"could not read {path}: {e}") from e
    unknown = sorted(set(data) - KNOWN_KEYS)
    if unknown:
        # A typo such as `vocie` must not quietly mean "no voice".
        raise ConfigError(f"{path} has unknown settings: {', '.join(unknown)}. "
                          f"The settings are: {', '.join(sorted(KNOWN_KEYS))}")
    meter = data.get("metrics", True)
    if not isinstance(meter, bool):
        raise ConfigError(f"{path}: metrics must be true or false")
    value = data.get("voice", "")
    if not isinstance(value, str):
        raise ConfigError(f"{path}: voice must be a string, a name or a path")
    auto = data.get("auto", [])
    if not isinstance(auto, list) or not all(isinstance(a, str) for a in auto):
        raise ConfigError(f"{path}: auto must be a list such as "
                          f"[\"markdown\", \"commit\", \"pr\"]")
    bad = sorted(set(auto) - AUTO_VALUES)
    if bad:
        raise ConfigError(f"{path}: auto has unknown values: {', '.join(bad)}. "
                          f"Allowed: {', '.join(sorted(AUTO_VALUES))}")
    return Config(path=path, voice=resolve_voice(value, path.parent),
                  auto=frozenset(auto), metrics=meter)


GENERATED = "<!-- wr:generated"


def voice_core(text: str) -> str:
    """The part of a voice file the model gets: everything above the marker.

    A file `wr voice build` generated from a voice directory opens with a line
    saying so, which the model does not need (2026-09-21)."""
    if text.startswith(GENERATED):
        text = text.partition("\n")[2]
    head, marker, _ = text.partition(CORE_MARKER)
    if not marker:
        return text
    return head.rstrip() + "\n"

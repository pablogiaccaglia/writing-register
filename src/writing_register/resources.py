"""Where wr finds the humanizer skill and the voices it ships.

In a clone, both live at the repository root: `vendor/humanizer/` and
`voice/`. An installed package carries its own copy under `_data/`, which the
build copies in (setup.py), so `wr` also works after `uv tool install`, pipx
or pip, away from any clone. The clone wins when both exist, so an editable
install always reads the files a developer is editing.
"""
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent
_CLONE = _PACKAGE.parents[1]


def _root() -> Path:
    if (_CLONE / "vendor" / "humanizer" / "SKILL.md").is_file():
        return _CLONE
    return _PACKAGE / "_data"


ROOT = _root()
SKILL = ROOT / "vendor" / "humanizer" / "SKILL.md"
VOICES_DIR = ROOT / "voice"

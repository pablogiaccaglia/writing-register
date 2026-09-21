"""Every voice this repository ships, checked as a whole (2026-09-21).

This is the one test file that loads the real voices. The behaviour tests use
small fixture voices, so rewording a rule here never breaks a hook test; what
this file guarantees is that each shipped voice directory passes its own
checks, that its generated view is current and carries the same core, that
the core fits its budget, and that the core reaches a session, a subagent and
the output style unchanged.
"""
import pytest

from writing_register import config, hooks, style
from writing_register import voice as v
from writing_register import voice_tools as vt

# The public voices, and on the owner's machine the private ones kept beside them.
_FOLDERS = [config.VOICES_DIR, config.REPO / "private" / "voice"]
VOICES = sorted(p for d in _FOLDERS if d.is_dir() for p in d.iterdir() if (p / v.MANIFEST).is_file())


@pytest.mark.parametrize("root", VOICES, ids=[p.name for p in VOICES])
def test_the_voice_passes_its_checks(root):
    errors = [str(f) for f in vt.check(root) if f.level == "error"]
    assert errors == []


@pytest.mark.parametrize("root", VOICES, ids=[p.name for p in VOICES])
def test_the_view_is_current_and_carries_the_same_core(root):
    assert vt.is_fresh(root), f"run `wr voice build {root}`"
    assert config.voice_core(vt.view_path(root).read_text()) == v.read_core(root)


@pytest.mark.parametrize("root", VOICES, ids=[p.name for p in VOICES])
def test_the_core_fits_its_budget(root):
    manifest = v.load(root)
    if manifest.budget:
        assert len(v.read_core(root)) <= manifest.budget


@pytest.mark.parametrize("root", VOICES, ids=[p.name for p in VOICES])
def test_the_core_reaches_the_session_the_subagent_and_the_style(root, tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'voice = "{root}"\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    core = v.read_core(root)
    session = hooks.session_start({"session_id": "s"})["hookSpecificOutput"]["additionalContext"]
    subagent = hooks.subagent_start({"session_id": "s"})["hookSpecificOutput"]["additionalContext"]
    assert core.strip() in session and core.strip() in subagent and core.strip() in style.build()

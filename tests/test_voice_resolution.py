"""A voice named or pointed at by the configuration may be a directory
(2026-09-21). The four places that read a voice all receive the same
core string, so a directory voice reaches every session, subagent, output
style and rewrite exactly as a single file does."""
import io
import json

import pytest

from writing_register import config as c
from writing_register import hooks, style
from writing_register.cli import main


def _dir_voice(parent, name="dirvoice"):
    root = parent / name
    (root / "rules").mkdir(parents=True)
    (root / "rules" / "rule.md").write_text("# Register\n\n{#rule.dashes} No em dashes in directory voices.\n")
    (root / "voice.toml").write_text('format = 1\nname = "Directory"\norder = ["rule.md"]\n')
    return root


def _config(tmp_path, monkeypatch, value):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'voice = "{value}"\nauto = ["markdown"]\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    return cfg


def test_a_path_to_a_voice_directory_resolves(tmp_path):
    root = _dir_voice(tmp_path)
    assert c.resolve_voice(str(root), tmp_path) == root


def test_a_folder_without_a_manifest_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(c.ConfigError, match="voice.toml"):
        c.resolve_voice(str(tmp_path / "empty"), tmp_path)


def test_a_name_finds_a_directory_before_a_file_of_the_same_name(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "VOICES_DIR", tmp_path)
    root = _dir_voice(tmp_path, "twin")
    (tmp_path / "twin.md").write_text("# Voice: generated view\n")
    assert c.resolve_voice("twin", tmp_path) == root


def test_an_unknown_name_lists_directories_and_files(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "VOICES_DIR", tmp_path)
    _dir_voice(tmp_path, "alpha")
    (tmp_path / "beta.md").write_text("# Voice: Beta\n")
    with pytest.raises(c.ConfigError) as e:
        c.resolve_voice("gamma", tmp_path)
    assert "alpha" in str(e.value) and "beta" in str(e.value)


def test_a_directory_voice_reaches_the_session_the_subagent_and_the_style(tmp_path, monkeypatch):
    root = _dir_voice(tmp_path)
    _config(tmp_path, monkeypatch, root)
    session = hooks.session_start({"session_id": "s"})["hookSpecificOutput"]["additionalContext"]
    sub = hooks.subagent_start({"session_id": "s"})["hookSpecificOutput"]["additionalContext"]
    built = style.build()
    for text in (session, sub, built):
        assert "No em dashes in directory voices." in text
        assert "{#" not in text


def test_a_broken_manifest_leaves_the_session_working_and_says_so(tmp_path, monkeypatch):
    root = _dir_voice(tmp_path)
    (root / "voice.toml").write_text('format = 1\nname = "D"\norder = ["gone.md"]\n')
    _config(tmp_path, monkeypatch, root)
    reply = hooks.session_start({"session_id": "s"})
    assert "gone.md" in reply.get("systemMessage", ""), reply
    assert "No em dashes in directory voices." not in json.dumps(reply)


def test_the_command_prints_a_directory_voice_s_core(tmp_path, monkeypatch):
    root = _dir_voice(tmp_path)
    _config(tmp_path, monkeypatch, root)
    out = io.StringIO()
    assert main(["voice", "--core"], out=out) == 0
    assert out.getvalue().startswith("# Voice: Directory\n")
    assert "{#" not in out.getvalue()

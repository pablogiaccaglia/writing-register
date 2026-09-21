"""`wr style`: the voice as a Claude Code output style.

Claude Code's documented way to change how the model writes every turn is an
output style: its text goes into the system prompt, is sent with every
request, and survives compaction, while `keep-coding-instructions: true`
leaves the software-engineering instructions in place. A style cannot ship a
particular person's voice, because a plugin's files are the same for everyone,
so the command writes one from whichever voice is active.

The style covers what a person reads. A subagent's report to the agent that
called it, and any other message between agents, stays plain: that traffic is
read by a model, and the voice is for people.
"""
import json
from pathlib import Path

from writing_register import style

TESTER = str(Path(__file__).parent / "fixtures" / "voices" / "tester.md")


def _config(tmp_path, monkeypatch, voice=TESTER, auto='["markdown", "commit", "pr"]'):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'voice = "{voice}"\nauto = {auto}\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))


def test_the_style_carries_the_voice_and_keeps_the_coding_instructions(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    text = style.build()
    head, body = text.split("---\n", 2)[1], text.split("---\n", 2)[2]
    assert "keep-coding-instructions: true" in head
    assert "name:" in head and "description:" in head
    assert "# Voice: Tester" in body
    assert "read by a model" in body or "between agents" in body


def test_without_a_voice_the_style_still_carries_the_patterns(tmp_path, monkeypatch):
    """Both halves travel in every modality: a user with no voice still gets the
    machine-writing patterns, which is the whole of what wr knows about them."""
    _config(tmp_path, monkeypatch, voice="none")
    text = style.build()
    assert "Not X but Y" in text and "# Voice" not in text


def test_install_writes_the_file_where_claude_code_reads_it(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    path = style.install()
    assert path == tmp_path / "claude" / "output-styles" / f"{style.NAME}.md"
    assert "# Voice: Tester" in path.read_text()


def test_enabling_sets_the_setting_and_keeps_the_rest_of_the_file(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    home = tmp_path / "claude"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"model": "claude-opus-5",
                                                    "enabledPlugins": {"x": True}}, indent=2))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    style.install(enable=True)
    saved = json.loads((home / "settings.json").read_text())
    assert saved["outputStyle"] == style.NAME
    assert saved["model"] == "claude-opus-5" and saved["enabledPlugins"] == {"x": True}


def test_the_session_hook_does_not_repeat_the_voice_the_style_already_carries(tmp_path, monkeypatch):
    from writing_register.hooks import session_start
    _config(tmp_path, monkeypatch)
    home = tmp_path / "claude"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    (home / "settings.json").write_text(json.dumps({"outputStyle": style.NAME}))
    reply = session_start({"session_id": "s"})
    text = (reply or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "# Voice: Tester" not in text, "the style already carries it"
    assert "rewrites these automatically" in text, "the note about the rewrites still belongs here"

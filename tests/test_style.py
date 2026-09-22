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


# The plugin's own style, for users with no voice (2026-09-21).

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_plugin_ships_the_plain_style_exactly_as_built():
    shipped = (REPO_ROOT / "output-styles" / f"{style.PLAIN}.md").read_text()
    assert shipped == style.plain(), "run: .venv/bin/python -c 'from writing_register import style; print(style.plain(), end=\"\")' > output-styles/human-prose.md"


def test_the_plain_style_carries_the_patterns_and_never_a_voice(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    text = style.plain()
    assert f"name: {style.PLAIN}" in text and "keep-coding-instructions: true" in text
    assert "Not X but Y" in text and "# Voice:" not in text
    assert "force-for-plugin" not in text, "the plugin never overrides a style the user chose"


def _selected_in_project(tmp_path, monkeypatch, name, file="settings.local.json"):
    _config(tmp_path, monkeypatch)
    home = tmp_path / "claude"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / file).write_text(json.dumps({"outputStyle": name}))
    return project


def _session_text(project):
    from writing_register.hooks import session_start
    reply = session_start({"session_id": "s", "cwd": str(project)})
    return (reply or {}).get("hookSpecificOutput", {}).get("additionalContext", "")


def test_a_style_chosen_in_config_is_read_from_the_project_settings(tmp_path, monkeypatch):
    """/config saves the choice in the project's .claude/settings.local.json."""
    project = _selected_in_project(tmp_path, monkeypatch, style.NAME)
    text = _session_text(project)
    assert "# Voice: Tester" not in text and "Not X but Y" not in text


def test_with_the_plugin_style_the_hook_sends_the_voice_but_not_the_patterns_again(tmp_path, monkeypatch):
    for chosen in (f"writing-register:{style.PLAIN}",):
        case = tmp_path / chosen.replace(":", "_")
        case.mkdir()
        project = _selected_in_project(case, monkeypatch, chosen)
        text = _session_text(project)
        assert "# Voice: Tester" in text, chosen
        assert "Not X but Y" not in text, f"{chosen}: the style already carries the patterns"


def test_the_local_project_setting_wins_over_the_user_setting(tmp_path, monkeypatch):
    project = _selected_in_project(tmp_path, monkeypatch, "Explanatory")
    (tmp_path / "claude" / "settings.json").write_text(json.dumps({"outputStyle": style.NAME}))
    assert style.selected(project) == "Explanatory"
    assert "# Voice: Tester" in _session_text(project)


def test_the_project_folder_claude_code_reports_wins_over_the_hook_cwd(tmp_path, monkeypatch):
    project = _selected_in_project(tmp_path, monkeypatch, f"writing-register:{style.PLAIN}")
    sub = project / "sub"
    sub.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    assert style.selected(sub) == f"writing-register:{style.PLAIN}"


def test_a_user_style_that_happens_to_be_called_human_prose_is_not_the_plugin_s(tmp_path, monkeypatch):
    project = _selected_in_project(tmp_path, monkeypatch, style.PLAIN)
    assert not style.plain_active(project)


def test_with_the_plugin_style_the_intro_does_not_point_at_missing_patterns(tmp_path, monkeypatch):
    project = _selected_in_project(tmp_path, monkeypatch, f"writing-register:{style.PLAIN}")
    text = _session_text(project)
    assert "# Voice: Tester" in text and "patterns after it" not in text

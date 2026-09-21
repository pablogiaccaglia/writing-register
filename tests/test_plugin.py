"""The plugin: one manifest at the repository root shipping two skills, the
vendored humanizer and the small writing-register skill that points at it."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / ".claude-plugin" / "plugin.json"
SKILL = ROOT / "plugin" / "skills" / "writing-register" / "SKILL.md"


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{path.name}: frontmatter must be line 1"
    return yaml.safe_load(text.split("---", 2)[1])


def test_the_manifest_ships_both_skills_and_each_exists():
    m = json.loads(MANIFEST.read_text())
    assert m["name"] == "writing-register" and m["description"] and m["version"]
    assert set(m["skills"]) == {"./vendor/humanizer", "./plugin/skills/writing-register"}
    for rel in m["skills"]:
        assert (ROOT / rel / "SKILL.md").is_file(), rel


def test_the_marketplace_offers_the_plugin_from_this_repository():
    m = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert [p["name"] for p in m["plugins"]] == ["writing-register"]
    assert m["plugins"][0]["source"] == "./"


def test_the_skill_is_scoped_to_markdown_and_small():
    """Skill content stays in context for the session, so every line is a
    recurring cost."""
    meta = frontmatter(SKILL)
    assert meta["paths"] == "**/*.md"
    assert len(SKILL.read_text(encoding="utf-8").split()) < 500


def test_the_skill_description_fits_the_listing_budget():
    meta = frontmatter(SKILL)
    assert len(meta.get("description", "") + meta.get("when_to_use", "")) <= 1536


def test_the_skill_carries_no_personal_voice():
    """Everyone who installs the plugin gets this skill, so it must not carry
    the maintainer's voice; it asks `wr voice --core` for whatever voice the
    user turned on (2026-09-14: other people must not get one person's voice by
    default)."""
    text = SKILL.read_text(encoding="utf-8")
    assert "# Voice:" not in text
    assert "wr voice --core" in text
    for rule in ("Explain before you name", "Join sentences by reason"):
        assert rule not in text, rule


HOOKS = ROOT / "hooks" / "hooks.json"


def test_the_plugin_hooks_call_wr_for_each_event():
    """Since 2026-09-15 wr runs on commit messages, PR descriptions and
    markdown files. The hooks file only calls `wr hook`; what happens, and
    whether anything happens at all, is decided in Python from the user's
    configuration."""
    h = json.loads(HOOKS.read_text())["hooks"]
    commands = {event: [x["command"] for group in groups for x in group["hooks"]]
                for event, groups in h.items()}
    expected = {"SessionStart": {"session-start"}, "SubagentStart": {"subagent-start"},
                "PreToolUse": {"pre-bash", "pre-edit"}, "PostToolUse": {"post-edit"},
                "Stop": {"stop"}, "UserPromptSubmit": {"prompt"}}
    assert set(commands) == set(expected)
    for event, names in expected.items():
        called = {n for n in names for c in commands[event] if f"hook {n}" in c}
        assert called == names, event
        assert all(any(f"hook {n}" in c for n in names) for c in commands[event]), event


def test_one_bash_hook_filters_in_the_shell_before_starting_wr(tmp_path):
    """Audit follow-up, 2026-09-15: the `if: Bash(git commit:*)` filter is a
    prefix rule, so `git -C path commit` never reached wr, and a commit and a
    PR in one command matched two handlers. One handler now passes the payload
    to wr only when it mentions a commit or `gh pr`, without starting Python
    for any other Bash call."""
    import subprocess
    groups = json.loads(HOOKS.read_text())["hooks"]["PreToolUse"]
    bash = [g for g in groups if g["matcher"] == "Bash"]
    assert len(bash) == 1
    handlers = bash[0]["hooks"]
    assert len(handlers) == 1 and "if" not in handlers[0]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    stub = bin_dir / "wr"
    stub.write_text(f'#!/bin/sh\ncat > /dev/null\necho "$@" >> "{log}"\n', encoding="utf-8")
    stub.chmod(0o755)

    def fire(command):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        subprocess.run(["sh", "-c", handlers[0]["command"]], input=payload, text=True,
                       env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}, check=True)
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return calls

    assert fire('git add -A && git commit -m "x"') == ["hook pre-bash"]
    assert fire('git -C ../repo commit -m "x"') == ["hook pre-bash"]
    assert fire('gh pr create --title T --body "x"') == ["hook pre-bash"]
    assert fire("ls -la && git status") == []


def test_the_end_of_turn_rewrite_runs_in_the_background():
    """A document rewrite takes minutes; Claude must not wait for it."""
    stop = json.loads(HOOKS.read_text())["hooks"]["Stop"][0]["hooks"][0]
    assert stop["async"] is True


def test_the_hooks_do_nothing_when_wr_is_not_installed(tmp_path):
    """Installing the plugin without running install.sh must not break Claude."""
    import subprocess
    h = json.loads(HOOKS.read_text())["hooks"]
    for groups in h.values():
        for handler in (x for g in groups for x in g["hooks"]):
            r = subprocess.run(["sh", "-c", handler["command"]], input="{}", text=True,
                               capture_output=True, env={"PATH": "/usr/bin:/bin",
                                                         "HOME": str(tmp_path)})
            assert r.returncode == 0 and r.stdout == "", handler["command"]


def test_the_end_of_turn_rewrite_has_time_for_several_documents():
    """A long document can take several minutes; the audit found no timeout."""
    stop = json.loads(HOOKS.read_text())["hooks"]["Stop"][0]["hooks"][0]
    assert stop["timeout"] >= 1800


def test_the_skills_never_tell_an_agent_to_install_anything():
    """Snyk, through skills.sh, 2026-09-21: an install command with a GitHub URL
    in the skill read as "an unverifiable external dependency" (W012). Installing
    is for a person reading the README, never an instruction to an agent."""
    import re
    install = r"git\+|uv tool install|pipx? install|npx |curl "
    for skill in (SKILL, ROOT / "vendor" / "humanizer" / "SKILL.md"):
        assert not re.search(install, skill.read_text(encoding="utf-8")), skill
    # The vendored skill cites its sources with links; ours needs none.
    assert not re.search(r"https?://", SKILL.read_text(encoding="utf-8"))

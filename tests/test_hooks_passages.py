"""The end-of-turn hook rewrites only the prose Claude added in the turn.

2026-09-15: the markdown hook rewrote the whole file after every turn,
so paragraphs Claude never touched drifted and a word Claude changed on request
could be changed back. A hook before Write and Edit now saves the file's text
before Claude's first edit, and the end-of-turn hook rewrites only the passages
that are new since then."""
import io
import re
import json
import subprocess
from pathlib import Path

from writing_register import hooks
from writing_register.spawn import Answer

BASE = """# Capture

The logger service writes one log per station into the out folder.

The alerter reads the log the next morning and builds the daily card.
"""
FILLER = ("It's worth noting that the logger service is a pivotal and robust foundation "
          "that seamlessly prepares every card for the team.")
CURRENT = BASE.replace("# Capture\n\n", f"# Capture\n\n{FILLER}\n\n")
CLEAN = "The logger service prepares every card for the team."
ROOT = Path(__file__).resolve().parent.parent


class _Bodies(tuple):
    """Passage bodies; the reply is built with the markers found in the prompt."""


def _reply(*texts):
    return _Bodies(texts)


def _render(reply, prompt):
    if not isinstance(reply, _Bodies):
        return reply
    token = re.search(r"\[\[(wr-[0-9a-f]+) passage 1\]\]", prompt).group(1)
    return "\n".join(f"[[{token} passage {i}]]\n{t}\n[[/{token} passage {i}]]"
                     for i, t in enumerate(reply, 1))


class Spawn:
    def __init__(self, reply="", during=None):
        self.reply, self.during, self.calls, self.prompts = reply, during, 0, []

    def run(self, prompt, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        if self.during and self.calls == 1:
            self.during()
        return Answer(stdout=_render(self.reply, prompt), command=("claude",), duration_seconds=0.1)


def _setup(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\nauto = ["markdown"]\n', encoding="utf-8")
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setattr(hooks, "_SETTLE_SECONDS", 0.0)


def _repo(tmp_path, text=BASE, commit=False):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("*.refused.md\n", encoding="utf-8")
    doc = repo / "docs" / "CAPTURE.md"
    if text is not None:
        doc.write_text(text, encoding="utf-8")
    if commit:
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start"],
                       cwd=repo, check=True)
    return repo, doc


def _run(event, payload, spawn=None):
    out = io.StringIO()
    assert hooks.run(event, io.StringIO(json.dumps(payload)), out, spawn=spawn) == 0
    return json.loads(out.getvalue()) if out.getvalue().strip() else None


def _claude_edits(doc, text, tool="Edit"):
    """What Claude Code does around an edit: the hook before, the write, the hook after."""
    payload = {"session_id": "s1", "tool_name": tool, "tool_input": {"file_path": str(doc)}}
    _run("pre-edit", payload)
    doc.write_text(text, encoding="utf-8")
    _run("post-edit", payload)


def _stop(spawn):
    return _run("stop", {"session_id": "s1", "stop_hook_active": False}, spawn)


def _notes():
    out = _run("prompt", {"session_id": "s1", "prompt": "next"})
    return out["hookSpecificOutput"]["additionalContext"] if out else ""


def test_only_the_paragraph_claude_added_is_rewritten(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    _claude_edits(doc, CURRENT)
    s = Spawn(_reply(CLEAN))
    _stop(s)
    assert s.calls == 1 and re.search(r"\[\[wr-[0-9a-f]+ passage 1\]\]\n" + re.escape(FILLER), s.prompts[0])
    assert doc.read_text(encoding="utf-8") == CURRENT.replace(FILLER, CLEAN)
    assert "1 passage" in _notes()


def test_a_one_word_edit_is_left_alone_without_a_call_or_a_note(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    edited = BASE.replace("next morning", "following morning")
    _claude_edits(doc, edited)
    s = Spawn(_reply(CLEAN))
    _stop(s)
    assert s.calls == 0 and doc.read_text(encoding="utf-8") == edited
    assert _notes() == ""


def test_a_later_edit_in_the_same_turn_does_not_hide_the_first_edits_prose(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    _claude_edits(doc, CURRENT)
    _claude_edits(doc, CURRENT.replace("next morning", "following morning"))
    s = Spawn(_reply(CLEAN))
    _stop(s)
    assert s.calls == 1 and FILLER in s.prompts[0]


def test_the_next_turn_starts_from_the_rewritten_text(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    _claude_edits(doc, CURRENT)
    _stop(Spawn(_reply(CLEAN)))
    _notes()
    rewritten = doc.read_text(encoding="utf-8")
    _claude_edits(doc, rewritten.replace("next morning", "following morning"))
    s = Spawn(_reply("unused"))
    _stop(s)
    assert s.calls == 0 and "following morning" in doc.read_text(encoding="utf-8")


def test_a_new_file_is_all_new_prose(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path, text=None)
    _claude_edits(doc, CURRENT, tool="Write")
    s = Spawn(_reply("unused one", "unused two", "unused three"))
    _stop(s)
    assert s.calls == 1 and FILLER in s.prompts[0] and "The alerter reads the log" in s.prompts[0]


def test_a_conflict_keeps_claudes_recorded_text_so_the_next_turn_covers_both(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    _claude_edits(doc, CURRENT)
    second = CURRENT + "\nA paragraph Claude added while the rewrite was running, with enough words.\n"
    _stop(Spawn(_reply(CLEAN), during=lambda: _claude_edits(doc, second)))
    assert doc.read_text(encoding="utf-8") == second
    s = Spawn(_reply("one", "two"))
    _stop(s)
    assert s.calls == 1 and FILLER in s.prompts[0] and "while the rewrite was running" in s.prompts[0]


def test_the_plugin_saves_the_baseline_before_markdown_edits_only(tmp_path):
    groups = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    edit = [g for g in groups if g["matcher"] == "Write|Edit|MultiEdit"]
    assert len(edit) == 1 and len(edit[0]["hooks"]) == 1
    handler = edit[0]["hooks"][0]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    stub = bin_dir / "wr"
    stub.write_text(f'#!/bin/sh\ncat > /dev/null\necho "$@" >> "{log}"\n', encoding="utf-8")
    stub.chmod(0o755)

    def fire(path):
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": path}})
        subprocess.run(["sh", "-c", handler["command"]], input=payload, text=True,
                       env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}, check=True)
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return calls

    assert fire("/repo/docs/SETUP.md") == ["hook pre-edit"]
    assert fire("/repo/src/tool.py") == []

"""What counts as new prose over several turns.

Review 2, 2026-09-15: the saved text was the file as it stood when the last
turn ended, so a paragraph the user typed in their editor before Claude's next
edit counted as new and was rewritten. The same happened after a denied Write
and in a repository that was skipped until `*.refused.md` was ignored. And a
reply that merely broke the format made the hook give up on those passages for
good."""
import io
import json
import re
import subprocess

from writing_register import hooks
from writing_register.spawn import Answer

BASE = """# Capture

The logger service writes one log per station into the out folder.

The alerter reads the log the next morning and builds the daily card.
"""
CLAUDE_A = ("It's worth noting that the logger service is a pivotal and robust foundation "
            "that seamlessly prepares every card for the team.")
CLAUDE_B = ("It is also worth noting that the alerter serves as a testament to careful design "
            "and reads every log with great care.")
USER = "I added this paragraph myself in my editor, and nobody should rewrite it for me."


class Spawn:
    def __init__(self, bodies=None, raw=None):
        self.bodies, self.raw, self.calls, self.prompts = bodies, raw, 0, []

    def run(self, prompt, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        if self.raw is not None:
            out = self.raw
        else:
            token = re.search(r"\[\[(wr-[0-9a-f]+) passage 1\]\]", prompt).group(1)
            out = "\n".join(f"[[{token} passage {i}]]\n{b}\n[[/{token} passage {i}]]"
                            for i, b in enumerate(self.bodies, 1))
        return Answer(stdout=out, command=("claude",), duration_seconds=0.1)


def _setup(tmp_path, monkeypatch, ignore=True):
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\nauto = ["markdown"]\n', encoding="utf-8")
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setattr(hooks, "_SETTLE_SECONDS", 0.0)
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if ignore:
        (repo / ".gitignore").write_text("*.refused.md\n", encoding="utf-8")
    doc = repo / "docs" / "CAPTURE.md"
    doc.write_text(BASE, encoding="utf-8")
    return repo, doc


def _run(event, body, spawn=None):
    out = io.StringIO()
    assert hooks.run(event, io.StringIO(json.dumps(body)), out, spawn=spawn) == 0
    return json.loads(out.getvalue()) if out.getvalue().strip() else None


def _payload(doc, tool="Edit"):
    return {"session_id": "s1", "tool_name": tool, "tool_input": {"file_path": str(doc)}}


def _claude_edits(doc, change, tool="Edit"):
    _run("pre-edit", _payload(doc, tool))
    doc.write_text(change(doc.read_text(encoding="utf-8")), encoding="utf-8")
    _run("post-edit", _payload(doc, tool))


def _stop(spawn):
    _run("stop", {"session_id": "s1"}, spawn)
    _run("prompt", {"session_id": "s1", "prompt": "next"})


def add_after_heading(text):
    return lambda doc: doc.replace("# Capture\n\n", f"# Capture\n\n{text}\n\n")


def append(text):
    return lambda doc: doc.rstrip("\n") + f"\n\n{text}\n"


def tweak(doc):
    return doc.replace("next morning", "following morning")


def test_a_paragraph_typed_outside_claude_between_turns_is_not_sent(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    _claude_edits(doc, add_after_heading(CLAUDE_A))
    _stop(Spawn(["The logger service prepares every card for the team."]))
    doc.write_text(append(USER)(doc.read_text(encoding="utf-8")), encoding="utf-8")
    _claude_edits(doc, tweak)
    s = Spawn(["unused"])
    _stop(s)
    assert s.calls == 0 and USER in doc.read_text(encoding="utf-8")


def test_only_claudes_paragraph_is_sent_when_the_user_also_added_one(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    doc.write_text(append(USER)(BASE), encoding="utf-8")
    _claude_edits(doc, add_after_heading(CLAUDE_A))
    s = Spawn(["The logger service prepares every card for the team."])
    _stop(s)
    assert s.calls == 1 and CLAUDE_A in s.prompts[0]
    assert not re.search(r"\[\[wr-[0-9a-f]+ passage \d+\]\]\n" + re.escape(USER), s.prompts[0])
    assert USER in doc.read_text(encoding="utf-8")


def test_a_user_edit_between_two_claude_edits_in_one_turn_is_not_sent(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    _claude_edits(doc, add_after_heading(CLAUDE_A))
    doc.write_text(append(USER)(doc.read_text(encoding="utf-8")), encoding="utf-8")
    _claude_edits(doc, lambda d: d.replace("builds the daily card.", f"builds the daily card.\n\n{CLAUDE_B}"))
    s = Spawn(["The logger service prepares every card.", "The alerter reads every log carefully."])
    _stop(s)
    assert s.calls == 1
    marked = re.findall(r"\[\[wr-[0-9a-f]+ passage \d+\]\]\n(.*?)\n\[\[/", s.prompts[0], re.S)
    assert marked == [CLAUDE_A, CLAUDE_B]


def test_a_file_created_by_hand_after_a_denied_write_is_not_new(tmp_path, monkeypatch):
    repo, _ = _setup(tmp_path, monkeypatch)
    new = repo / "docs" / "NEW.md"
    _run("pre-edit", _payload(new, "Write"))
    new.write_text(BASE.replace("# Capture", "# New"), encoding="utf-8")
    _claude_edits(new, tweak)
    s = Spawn(["unused"])
    _stop(s)
    assert s.calls == 0


def test_a_skipped_repository_starts_fresh_once_it_ignores_refused_files(tmp_path, monkeypatch):
    repo, doc = _setup(tmp_path, monkeypatch, ignore=False)
    _claude_edits(doc, add_after_heading(CLAUDE_A))
    _stop(Spawn(["unused"]))
    (repo / ".gitignore").write_text("*.refused.md\n", encoding="utf-8")
    _claude_edits(doc, lambda d: d.replace("builds the daily card.", f"builds the daily card.\n\n{CLAUDE_B}"))
    s = Spawn(["The alerter reads every log carefully."])
    _stop(s)
    assert s.calls == 1
    assert re.findall(r"\[\[wr-[0-9a-f]+ passage \d+\]\]\n(.*?)\n\[\[/", s.prompts[0], re.S) == [CLAUDE_B]


def test_a_reply_that_broke_the_format_is_tried_again_next_turn(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    _claude_edits(doc, add_after_heading(CLAUDE_A))
    _stop(Spawn(raw="Sorry, here is the text without markers."))
    _claude_edits(doc, tweak)
    s = Spawn(["The logger service prepares every card for the team."])
    _stop(s)
    assert s.calls == 1 and CLAUDE_A in s.prompts[0]
    assert "The logger service prepares every card for the team." in doc.read_text(encoding="utf-8")


def test_a_content_refusal_is_not_tried_again(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    _claude_edits(doc, add_after_heading(CLAUDE_A))
    _stop(Spawn(["The logger service prepares 12 cards for the team."]))
    _claude_edits(doc, tweak)
    s = Spawn(["unused"])
    _stop(s)
    assert s.calls == 0


def test_pre_edit_is_quiet_on_a_markdown_file_that_is_not_utf8(tmp_path, monkeypatch):
    repo, _ = _setup(tmp_path, monkeypatch)
    latin = repo / "docs" / "LATIN.md"
    latin.write_bytes(b"# Nota\n\nPerch\xe9 no.\n")
    assert _run("pre-edit", _payload(latin)) is None

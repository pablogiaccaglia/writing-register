"""Only text Claude wrote is rewritten.

Review 3, 2026-09-15: guessing Claude's text by comparing snapshots of the file
failed when turns overlapped (the background rewrite of one turn saved a stale
snapshot over the next turn's edit, so Claude's new prose was taken for the
user's and never rewritten) and when someone typed after Claude's last edit in
a turn (their text was sent). The edit hooks now record the sentences and list
items each of Claude's edits added, and the end-of-turn rewrite sends only those."""
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
A = ("It's worth noting that the logger service is a pivotal and robust foundation "
     "that seamlessly prepares every card for the team.")
B = ("It is also worth noting that the alerter serves as a testament to careful design "
     "and reads every log with great care.")
USER = "I typed this sentence myself right after Claude finished, and nobody should rewrite it."


class Spawn:
    def __init__(self, bodies=None, raw=None, during=None):
        self.bodies, self.raw, self.during, self.calls, self.prompts = bodies, raw, during, 0, []

    def run(self, prompt, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        if self.during and self.calls == 1:
            self.during()
        if self.raw is not None:
            return Answer(stdout=self.raw, command=("claude",), duration_seconds=0.1)
        token = re.search(r"\[\[(wr-[0-9a-f]+) passage 1\]\]", prompt).group(1)
        count = len(re.findall(rf"\[\[{token} passage \d+\]\]", prompt))
        bodies = (self.bodies * count)[:count]
        out = "\n".join(f"[[{token} passage {i}]]\n{b}\n[[/{token} passage {i}]]"
                        for i, b in enumerate(bodies, 1))
        return Answer(stdout=out, command=("claude",), duration_seconds=0.1)


def _setup(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\nauto = ["markdown"]\n', encoding="utf-8")
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setattr(hooks, "_SETTLE_SECONDS", 0.0)
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("*.refused.md\n", encoding="utf-8")
    doc = repo / "docs" / "CAPTURE.md"
    doc.write_text(BASE, encoding="utf-8")
    return repo, doc


def _run(event, body, spawn=None):
    out = io.StringIO()
    assert hooks.run(event, io.StringIO(json.dumps(body)), out, spawn=spawn) == 0
    return json.loads(out.getvalue()) if out.getvalue().strip() else None


def _claude_edits(doc, change, tool="Edit"):
    payload = {"session_id": "s1", "tool_name": tool, "tool_input": {"file_path": str(doc)}}
    _run("pre-edit", payload)
    doc.write_text(change(doc.read_text(encoding="utf-8") if doc.exists() else ""), encoding="utf-8")
    _run("post-edit", payload)


def _stop(spawn):
    _run("stop", {"session_id": "s1"}, spawn)


def _marked(prompt):
    return re.findall(r"\[\[wr-[0-9a-f]+ passage \d+\]\]\n(.*?)\n\[\[/", prompt, re.S)


def add_after_heading(text):
    return lambda doc: doc.replace("# Capture\n\n", f"# Capture\n\n{text}\n\n")


def add_at_end(text):
    return lambda doc: doc.rstrip("\n") + f"\n\n{text}\n"


def tweak(doc):
    return doc.replace("next morning", "following morning")


def test_text_typed_right_after_claudes_edit_is_not_sent(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    _claude_edits(doc, add_after_heading(A))
    doc.write_text(add_at_end(USER)(doc.read_text(encoding="utf-8")), encoding="utf-8")
    s = Spawn(["The logger service prepares every card for the team."])
    _stop(s)
    assert s.calls == 1 and _marked(s.prompts[0]) == [A]
    assert USER in doc.read_text(encoding="utf-8")


def test_an_overlapping_refusal_keeps_the_next_turns_text(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    _claude_edits(doc, add_after_heading(A))
    turn_two = lambda: _claude_edits(doc, add_at_end(B))
    _stop(Spawn(["The logger service prepares 12 cards for the team."], during=turn_two))
    s = Spawn(["The alerter reads every log carefully."])
    _stop(s)
    assert s.calls == 1 and _marked(s.prompts[0]) == [B]


def test_an_overlapping_retry_keeps_both_turns_text(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    _claude_edits(doc, add_after_heading(A))
    turn_two = lambda: _claude_edits(doc, add_at_end(B))
    _stop(Spawn(raw="no markers at all", during=turn_two))
    s = Spawn(["The logger service prepares every card.", "The alerter reads every log carefully."])
    _stop(s)
    assert s.calls == 1 and _marked(s.prompts[0]) == [A, B]


def test_claude_rewording_its_own_new_sentence_keeps_it_claudes(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    _claude_edits(doc, add_after_heading(A))
    reworded = A.replace("every card", "each card")
    _claude_edits(doc, lambda d: d.replace(A, reworded))
    s = Spawn(["The logger service prepares each card for the team."])
    _stop(s)
    assert s.calls == 1 and _marked(s.prompts[0]) == [reworded]


def test_a_whole_file_write_sends_only_what_claude_added(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    _claude_edits(doc, add_at_end(A), tool="Write")
    s = Spawn(["The logger service prepares every card for the team."])
    _stop(s)
    assert s.calls == 1 and _marked(s.prompts[0]) == [A]


def test_a_paragraph_claude_only_moves_is_not_sent(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    first = "The logger service writes one log per station into the out folder."
    second = "The alerter reads the log the next morning and builds the daily card."
    _claude_edits(doc, lambda d: d.replace(first, "@@").replace(second, first).replace("@@", second))
    s = Spawn(["unused"])
    _stop(s)
    assert s.calls == 0


def test_retries_stop_after_three_attempts(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    _claude_edits(doc, add_after_heading(A))
    calls = 0
    for turn in range(5):
        s = Spawn(raw="no markers at all")
        _stop(s)
        calls += s.calls
        _claude_edits(doc, tweak if turn % 2 == 0 else (lambda d: d.replace("following morning", "next morning")))
    assert calls == 3


def test_a_file_that_disappears_loses_its_records(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    _claude_edits(doc, add_after_heading(A))
    doc.unlink()
    _stop(Spawn(["unused"]))
    doc.write_text(add_after_heading(A)(BASE), encoding="utf-8")
    _claude_edits(doc, tweak)
    s = Spawn(["unused"])
    _stop(s)
    assert s.calls == 0


def test_nothing_is_rewritten_for_a_file_claude_never_edited_through_the_hooks(tmp_path, monkeypatch):
    _, doc = _setup(tmp_path, monkeypatch)
    doc.write_text(add_after_heading(A)(BASE), encoding="utf-8")
    _run("post-edit", {"session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": str(doc)}})
    s = Spawn(["unused"])
    _stop(s)
    assert s.calls == 0

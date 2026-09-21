"""The end-of-turn rewrite changes prose only, and tells Claude what it changed.

2026-09-15. The automatic rewrite runs on sentences Claude wrote minutes
earlier with the repository open. It used to be
told to add background from the files the document names, which a model that
has not seen the repository gets wrong. It now changes prose only, and the
next-turn note gives Claude the old and new wording of every sentence it
changed, so Claude, who has the context, checks them. The checker that verifies
against the code runs in `wr humanize` by hand, where minutes are affordable."""
import io
import json
import re
import subprocess

from writing_register import hooks
from writing_register.passages import build_passage_prompt, humanize_passages
from writing_register.spawn import Answer

BASE = """# Capture

The logger service writes one log per station into the out folder, as [the setup guide](SETUP.md) explains.
"""
FILLER = ("It's worth noting that the logger service is a pivotal and robust foundation "
          "that seamlessly prepares every card for the team.")
CLEAN = "The logger service prepares every card for the team."


class Replying:
    def __init__(self, bodies):
        self.bodies, self.prompts = bodies, []

    def run(self, prompt, **kw):
        self.prompts.append(prompt)
        token = re.search(r"\[\[(wr-[0-9a-f]+) passage 1\]\]", prompt).group(1)
        count = len(re.findall(rf"\[\[{token} passage \d+\]\]", prompt))
        bodies = (self.bodies * count)[:count]
        reply = "\n".join(f"[[{token} passage {i}]]\n{b}\n[[/{token} passage {i}]]" for i, b in enumerate(bodies, 1))
        return Answer(stdout=reply, command=("claude",), duration_seconds=0.1)


def _repo(tmp_path):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("*.refused.md\n")
    (root / "docs" / "SETUP.md").write_text("# Setup\n\nThe box keeps 7 days of spool in SPOOL_DIR.\n")
    doc = root / "docs" / "CAPTURE.md"
    doc.write_text(BASE)
    return root, doc


def test_the_passage_prompt_changes_prose_only():
    prompt = build_passage_prompt("[[wr passage 1]]\nx\n[[/wr passage 1]]", 1, "SKILL", token="wr")
    instructions = prompt.split("# The humanizer skill")[0]
    assert "Change prose only" in instructions
    assert "did not build" not in instructions and "background" not in instructions.lower()


def test_the_automatic_rewrite_sends_no_sources_even_when_the_document_links_files(tmp_path):
    root, doc = _repo(tmp_path)
    doc.write_text(BASE + f"\n{FILLER}\n")
    s = Replying([CLEAN])
    r = humanize_passages(doc, BASE, spawn=s)
    assert r.written, r.refused
    assert "# Source:" not in s.prompts[0] and "7 days of spool" not in s.prompts[0]


def test_the_result_records_each_changed_sentence(tmp_path):
    root, doc = _repo(tmp_path)
    doc.write_text(BASE + f"\n{FILLER}\n")
    r = humanize_passages(doc, BASE, spawn=Replying([CLEAN]))
    pairs = [(c.old, c.new) for c in r.changes if c.kind in ("changed", "added", "removed")]
    assert pairs == [(FILLER, CLEAN)]


def _setup_hooks(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\nauto = ["markdown"]\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setattr(hooks, "_SETTLE_SECONDS", 0.0)


def _event(name, body, spawn=None):
    out = io.StringIO()
    assert hooks.run(name, io.StringIO(json.dumps(body)), out, spawn=spawn) == 0
    return json.loads(out.getvalue()) if out.getvalue().strip() else None


def _claude_writes(doc, text):
    payload = {"session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": str(doc)}}
    _event("pre-edit", payload)
    doc.write_text(text)
    _event("post-edit", payload)


def _note():
    out = _event("prompt", {"session_id": "s1", "prompt": "next"})
    return out["hookSpecificOutput"]["additionalContext"] if out else ""


def test_the_next_turn_note_lists_old_and_new_wording(tmp_path, monkeypatch):
    _setup_hooks(tmp_path, monkeypatch)
    root, doc = _repo(tmp_path)
    _claude_writes(doc, BASE + f"\n{FILLER}\n")
    _event("stop", {"session_id": "s1"}, Replying([CLEAN]))
    note = _note()
    assert "1 passage" in note
    assert f'"{FILLER}" -> "{CLEAN}"' in note
    assert "still says what you meant" in note


def test_the_note_lists_at_most_twenty_pairs(tmp_path, monkeypatch):
    _setup_hooks(tmp_path, monkeypatch)
    root, doc = _repo(tmp_path)
    paragraphs = [f"It's worth noting that paragraph number {i} seamlessly explains a pivotal and robust capture step for the team."
                  for i in range(25)]
    _claude_writes(doc, BASE + "".join(f"\n{p}\n" for p in paragraphs))
    _event("stop", {"session_id": "s1"}, Replying(["This step prepares a card for the team."]))
    note = _note()
    assert note.count('" -> "') == 20 and "and 5 more" in note


def test_session_start_says_the_automatic_rewrite_is_prose_only(tmp_path, monkeypatch):
    _setup_hooks(tmp_path, monkeypatch)
    out = _event("session-start", {"session_id": "s1"})
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "prose only" in context and "each passage it changed" in context

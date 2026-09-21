"""Markdown files Claude writes are rewritten at the end of the turn.

Since 2026-09-15 wr runs on the markdown files Claude produces, in any git
repository. A rewrite takes minutes, so the edit hook only records the file, a
background hook at the end of the turn does the rewriting, and the next prompt
tells Claude what changed so it reads the file again before editing it."""
import io
import json
import re
import subprocess

from writing_register import hooks
from writing_register.spawn import Answer

DOC = "# Setup\n\nIt is not a recorder, but a note taker.\n"
CLEAN = "# Setup\n\nIt takes notes.\n"


def _render(reply, prompt):
    """A whole-document reply in the passage format the prompt asks for: every
    marked passage becomes the reply's first paragraph after its heading."""
    found = re.search(r"\[\[(wr-[0-9a-f]+) passage 1\]\]", prompt)
    if not found:
        return reply
    token = found.group(1)
    count = len(re.findall(rf"\[\[{token} passage \d+\]\]", prompt))
    paragraphs = [p for p in reply.split("\n\n") if p.strip() and not p.lstrip().startswith("#")]
    body = paragraphs[0].strip() if paragraphs else reply.strip()
    return "\n".join(f"[[{token} passage {i}]]\n{body}\n[[/{token} passage {i}]]"
                     for i in range(1, count + 1))


class Spawn:
    def __init__(self, reply=CLEAN):
        self.reply, self.calls = reply, 0

    def run(self, prompt, **kw):
        self.calls += 1
        return Answer(stdout=_render(self.reply, prompt), command=("claude",), duration_seconds=0.1)


def _setup(tmp_path, monkeypatch, auto='["markdown"]'):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'voice = "none"\nauto = {auto}\n', encoding="utf-8")
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setattr(hooks, "_SETTLE_SECONDS", 0.0)


def _repo(tmp_path, ignore="*.refused.md\n"):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if ignore is not None:
        (repo / ".gitignore").write_text(ignore, encoding="utf-8")
    doc = repo / "docs" / "SETUP.md"
    doc.write_text(DOC, encoding="utf-8")
    return repo, doc


def _event(name, payload, spawn=None):
    out = io.StringIO()
    code = hooks.run(name, io.StringIO(json.dumps(payload)), out, spawn=spawn)
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else None)


def _edit(path, session="s1", tool="Edit"):
    """What Claude Code does when Claude writes the file: the hook before the
    write, the write, and the hook after it. The file's content at this point
    counts as what Claude wrote (review 3: only Claude's recorded text is
    rewritten)."""
    payload = {"session_id": session, "tool_name": tool, "tool_input": {"file_path": str(path)}}
    content = path.read_bytes() if path.exists() else b""
    if path.exists():
        path.unlink()
    _event("pre-edit", payload)
    path.write_bytes(content)
    return _event("post-edit", payload)


def _stop(spawn, session="s1"):
    return _event("stop", {"session_id": session, "stop_hook_active": False}, spawn)


def _prompt(session="s1"):
    return _event("prompt", {"session_id": session, "prompt": "next"})


def test_an_edited_markdown_file_is_rewritten_at_the_end_of_the_turn(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    assert _edit(doc) == (0, None)
    assert doc.read_text(encoding="utf-8") == DOC
    s = Spawn()
    assert _stop(s) == (0, None)
    assert s.calls == 1 and doc.read_text(encoding="utf-8") == CLEAN


def test_the_next_prompt_tells_claude_what_was_rewritten_once(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    _edit(doc)
    _stop(Spawn())
    code, out = _prompt()
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert code == 0 and "SETUP.md" in ctx and "read" in ctx.lower()
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert _prompt() == (0, None)


def test_a_file_edited_several_times_in_a_turn_is_rewritten_once(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    for tool in ("Write", "Edit", "Edit"):
        _edit(doc, tool=tool)
    s = Spawn()
    _stop(s)
    assert s.calls == 1


def test_a_turn_with_no_markdown_edits_makes_no_model_call(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    repo, _ = _repo(tmp_path)
    code_file = repo / "tool.py"
    code_file.write_text("x = 1\n", encoding="utf-8")
    _edit(code_file)
    s = Spawn()
    assert _stop(s) == (0, None) and s.calls == 0


def test_nothing_is_recorded_unless_auto_lists_markdown(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, auto='["commit"]')
    _, doc = _repo(tmp_path)
    _edit(doc)
    s = Spawn()
    _stop(s)
    assert s.calls == 0 and doc.read_text(encoding="utf-8") == DOC


def test_a_repository_that_does_not_ignore_refused_files_is_skipped_and_claude_is_told(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path, ignore=None)
    _edit(doc)
    s = Spawn()
    _stop(s)
    assert s.calls == 0 and doc.read_text(encoding="utf-8") == DOC
    _, out = _prompt()
    assert "*.refused.md" in out["hookSpecificOutput"]["additionalContext"]


def test_ignored_vendored_changelog_and_outside_files_are_skipped(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    repo, _ = _repo(tmp_path, ignore="*.refused.md\nbuild/\n")
    (repo / "build").mkdir()
    (repo / "vendor" / "lib").mkdir(parents=True)
    outside = tmp_path / "loose.md"
    targets = [repo / "build" / "OUT.md", repo / "vendor" / "lib" / "README.md",
               repo / "CHANGELOG.md", outside, repo / "docs" / "SETUP.refused.md"]
    for t in targets:
        t.write_text(DOC, encoding="utf-8")
        _edit(t)
    s = Spawn()
    _stop(s)
    assert s.calls == 0
    assert all(t.read_text(encoding="utf-8") == DOC for t in targets)


def test_a_refused_rewrite_is_reported_with_where_it_was_kept(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    _edit(doc)
    _stop(Spawn("# Setup\n\nIt takes 12 notes.\n"))
    assert doc.read_text(encoding="utf-8") == DOC
    _, out = _prompt()
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "SETUP.md" in ctx and "SETUP.refused.md" in ctx


def test_sessions_do_not_share_their_queues(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    _edit(doc, session="a")
    s = Spawn()
    _stop(s, session="b")
    assert s.calls == 0
    _stop(s, session="a")
    assert s.calls == 1


# Audit 2026-09-15: a file that could not be read made the end-of-turn hook
# stop, and every file queued after it was dropped without a word.

def test_a_file_that_cannot_be_read_is_reported_and_the_rest_are_still_rewritten(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    repo, doc = _repo(tmp_path)
    latin = repo / "docs" / "LATIN.md"
    latin.write_bytes(b"# Nota\n\nPerch\xe9 no.\n")
    _edit(latin)
    _edit(doc)
    assert _stop(Spawn()) == (0, None)
    assert doc.read_text(encoding="utf-8") == CLEAN
    ctx = _prompt()[1]["hookSpecificOutput"]["additionalContext"]
    assert "LATIN.md" in ctx and "SETUP.md" in ctx


def test_each_file_is_reported_as_soon_as_it_is_handled(tmp_path, monkeypatch):
    """If the end-of-turn hook is stopped halfway, the files already handled
    still reach Claude."""
    _setup(tmp_path, monkeypatch)
    repo, doc = _repo(tmp_path)
    other = repo / "docs" / "OTHER.md"
    other.write_text(DOC, encoding="utf-8")
    _edit(doc)
    _edit(other)
    seen = []

    class Watching(Spawn):
        def run(self, prompt, **kw):
            done = list((tmp_path / "state").glob("*/done"))
            seen.append(done[0].read_text(encoding="utf-8") if done else "")
            return super().run(prompt, **kw)

    _stop(Watching())
    assert seen[0] == "" and "SETUP.md" in seen[1]


# Audit 2026-09-15: a golden file under tests/fixtures was rewritten, which
# would break a byte-exact test, and a name starting with a dash was read by
# git as an option.

import pytest


@pytest.mark.parametrize("rel", ["tests/fixtures/expected.md", "test/golden.md", "testdata/in.md",
                                 "fixtures/a.md", ".claude/commands/review.md",
                                 "node_modules/pkg/README.md"])
def test_markdown_in_test_fixture_and_claude_folders_is_not_rewritten(tmp_path, monkeypatch, rel):
    _setup(tmp_path, monkeypatch)
    repo, _ = _repo(tmp_path)
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(DOC, encoding="utf-8")
    _edit(f)
    s = Spawn()
    _stop(s)
    assert s.calls == 0 and f.read_text(encoding="utf-8") == DOC


def test_a_file_name_starting_with_a_dash_is_checked_correctly(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    repo, _ = _repo(tmp_path)
    f = repo / "-notes.md"
    f.write_text(DOC, encoding="utf-8")
    _edit(f)
    s = Spawn()
    _stop(s)
    assert s.calls == 1 and f.read_text(encoding="utf-8") == CLEAN


# Audit 2026-09-15: the session id became a folder name after only replacing
# unusual characters, so `..` wrote outside the state folder, and every call
# without a session id shared one queue.

def test_a_session_id_cannot_leave_the_state_folder(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    for session in ("..", ".", "../escape"):
        _edit(doc, session=session)
    state = tmp_path / "state"
    written = [p for p in tmp_path.rglob("queue")]
    assert written and all(state in p.parents for p in written)


def test_an_edit_without_a_session_id_is_not_queued(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    _event("post-edit", {"tool_name": "Edit", "tool_input": {"file_path": str(doc)}})
    assert not list(tmp_path.rglob("queue"))


# Audit follow-up, 2026-09-15: the queue and notes files were renamed by one
# hook while another could still be appending to them, and a rewrite refused
# because Claude had edited the file again left a stale `.refused.md` and a
# confusing note, although the newer version was about to be rewritten anyway.

import threading


def test_the_end_of_turn_hook_waits_for_an_edit_being_recorded(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    repo, doc = _repo(tmp_path)
    other = repo / "docs" / "OTHER.md"
    other.write_text(DOC, encoding="utf-8")
    _edit(doc)
    d = hooks._state_dir("s1")
    s = Spawn()
    with hooks._locked(d):
        worker = threading.Thread(target=_stop, args=(s,))
        worker.start()
        worker.join(0.3)
        assert worker.is_alive(), "the end-of-turn hook did not wait for the lock"
        with (d / "queue").open("a", encoding="utf-8") as f:
            f.write(str(other) + "\n")
    worker.join(10)
    # The line appended while the lock was held was taken, not lost; OTHER.md
    # was never edited by Claude through the hooks, so nothing is sent for it.
    assert not worker.is_alive() and s.calls == 1
    assert not (d / "queue").exists() and other.read_text(encoding="utf-8") == DOC


def test_the_next_prompt_waits_for_a_note_being_written(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    _edit(doc)
    d = hooks._state_dir("s1")
    seen = {}
    with hooks._locked(d):
        worker = threading.Thread(target=lambda: seen.setdefault("out", _prompt()))
        worker.start()
        worker.join(0.3)
        assert worker.is_alive(), "the prompt hook did not wait for the lock"
        (d / "done").write_text("wr rewrote SETUP.md in 3s\n", encoding="utf-8")
    worker.join(10)
    assert "SETUP.md" in seen["out"][1]["hookSpecificOutput"]["additionalContext"]


class ReEditing(Spawn):
    """During the model call, Claude edits the document again, and the edit
    hook records it, as it would in the next turn."""

    def __init__(self, doc, record=True):
        super().__init__()
        self.doc, self.record = doc, record

    def run(self, prompt, **kw):
        if self.calls == 0:
            self.doc.write_text(DOC + "\nA newer paragraph.\n", encoding="utf-8")
            if self.record:
                _edit(self.doc)
        return super().run(prompt, **kw)


def test_a_file_claude_edits_again_during_its_rewrite_is_not_kept_as_refused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    _edit(doc)
    _stop(ReEditing(doc))
    assert "A newer paragraph." in doc.read_text(encoding="utf-8")
    assert not (doc.parent / "SETUP.refused.md").exists()
    ctx = _prompt()[1]["hookSpecificOutput"]["additionalContext"]
    assert "SETUP.md" in ctx and "edited it again" in ctx and "refused" not in ctx
    s = Spawn("# Setup\n\nIt takes notes.\n\nA newer paragraph.\n")
    _stop(s)
    assert s.calls == 1


def test_an_edit_made_outside_claude_during_the_rewrite_still_keeps_the_refused_copy(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _repo(tmp_path)
    _edit(doc)
    _stop(ReEditing(doc, record=False))
    assert "A newer paragraph." in doc.read_text(encoding="utf-8")
    assert (doc.parent / "SETUP.refused.md").exists()
    ctx = _prompt()[1]["hookSpecificOutput"]["additionalContext"]
    assert "SETUP.refused.md" in ctx

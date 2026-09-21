"""A rewrite keeps the file's line endings.

Review 2, 2026-09-15: the file was read in text mode, which turns CRLF into
LF, and written back with LF, so adding one paragraph to a CRLF file changed
every line in the git diff."""
import io
import json
import re
import subprocess

from writing_register import hooks
from writing_register.humanize import humanize
from writing_register.passages import humanize_passages
from writing_register.spawn import Answer

LF_BASE = "# Capture\n\nThe logger service writes one log per station.\n\nThe alerter builds the card.\n"
FILLER = ("It's worth noting that the logger service is a pivotal and robust foundation "
          "that seamlessly prepares every card for the team.")
CLEAN = "The logger service prepares every card for the team."


def crlf(text):
    return text.replace("\n", "\r\n")


class Spawn:
    def __init__(self, reply=None, bodies=None, during=None):
        self.reply, self.bodies, self.during = reply, bodies, during

    def run(self, prompt, **kw):
        if self.during:
            self.during()
        if self.bodies is not None:
            token = re.search(r"\[\[(wr-[0-9a-f]+) passage 1\]\]", prompt).group(1)
            out = "\n".join(f"[[{token} passage {i}]]\n{b}\n[[/{token} passage {i}]]"
                            for i, b in enumerate(self.bodies, 1))
        else:
            out = self.reply
        return Answer(stdout=out, command=("claude",), duration_seconds=0.1)


def _only_crlf(raw: bytes) -> bool:
    return raw.count(b"\n") == raw.count(b"\r\n") > 0


def test_a_whole_file_rewrite_keeps_crlf(tmp_path):
    p = tmp_path / "doc.md"
    doc = LF_BASE.replace("The alerter builds the card.", FILLER)
    p.write_bytes(crlf(doc).encode())
    r = humanize(p, spawn=Spawn(reply=LF_BASE.replace("The alerter builds the card.", CLEAN)))
    assert r.written, r.refused
    raw = p.read_bytes()
    assert _only_crlf(raw) and raw == crlf(LF_BASE.replace("The alerter builds the card.", CLEAN)).encode()


def test_a_passage_rewrite_keeps_crlf_and_every_other_byte(tmp_path):
    p = tmp_path / "doc.md"
    current = LF_BASE.replace("# Capture\n\n", f"# Capture\n\n{FILLER}\n\n")
    p.write_bytes(crlf(current).encode())
    r = humanize_passages(p, crlf(LF_BASE), spawn=Spawn(bodies=[CLEAN]))
    assert r.written, r.refused
    assert p.read_bytes() == crlf(current.replace(FILLER, CLEAN)).encode()


def test_an_edit_during_the_call_is_still_caught_on_a_crlf_file(tmp_path):
    p = tmp_path / "doc.md"
    current = LF_BASE.replace("# Capture\n\n", f"# Capture\n\n{FILLER}\n\n")
    p.write_bytes(crlf(current).encode())

    def edit():
        p.write_bytes(crlf(current + "\nAn edit made during the call.\n").encode())

    r = humanize_passages(p, crlf(LF_BASE), spawn=Spawn(bodies=[CLEAN], during=edit))
    assert not r.written and r.conflict
    assert b"An edit made during the call." in p.read_bytes()


def test_the_end_of_turn_hook_keeps_crlf(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\nauto = ["markdown"]\n', encoding="utf-8")
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("*.refused.md\n", encoding="utf-8")
    doc = repo / "docs" / "CAPTURE.md"
    doc.write_bytes(crlf(LF_BASE).encode())
    payload = {"session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": str(doc)}}

    def run(event, body, spawn=None):
        assert hooks.run(event, io.StringIO(json.dumps(body)), io.StringIO(), spawn=spawn) == 0

    run("pre-edit", payload)
    current = crlf(LF_BASE.replace("# Capture\n\n", f"# Capture\n\n{FILLER}\n\n"))
    doc.write_bytes(current.encode())
    run("post-edit", payload)
    run("stop", {"session_id": "s1"}, Spawn(bodies=[CLEAN]))
    assert doc.read_bytes() == current.replace(FILLER, CLEAN).encode()

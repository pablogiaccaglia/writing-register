"""The voice a hook delivers arrives whole, in parts (2026-09-21).

Claude Code shows the model at most about 10,000 characters of a hook's
additionalContext. Above that it saves the text to a file and shows a 2KB
preview: in the transcripts the largest text delivered whole was 9.7KB and the
smallest persisted one 9.9KB. The voice core is over 20,000 characters, so every
session-start and subagent delivery since 2026-09-15 reached the model as its
first 2KB only (227 persisted, none whole). The plugin now registers each of
these hooks several times, and each registration sends one part under the
limit, cut at section boundaries.
"""
import io
import json
from pathlib import Path

from writing_register import hooks

REPO = Path(__file__).resolve().parent.parent


def _big_voice(tmp_path, sections=12, words=200):
    body = "\n\n".join(
        f"## Section {i}\n\n" + " ".join(f"word{i}x{j}" for j in range(words)) + "." for i in range(sections))
    path = tmp_path / "big.md"
    path.write_text(f"# Voice: Big\n\nIntro sentence.\n\n{body}\n")
    return path


def _config(tmp_path, monkeypatch, voice, auto='["markdown"]'):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'voice = "{voice}"\nauto = {auto}\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))


def _context(reply):
    return (reply or {}).get("hookSpecificOutput", {}).get("additionalContext", "")


def test_parts_fit_the_limit_and_together_hold_all_the_text():
    text = "\n\n".join(f"## S{i}\n\n" + ("x " * 1500).strip() for i in range(10))
    parts = hooks.split_parts(text)
    assert len(parts) > 1
    assert all(len(p) <= hooks.PART_LIMIT for p in parts)
    for i in range(10):
        assert sum(p.count(f"## S{i}\n") for p in parts) == 1, "each section whole, once"


def test_a_section_longer_than_the_limit_is_cut_at_paragraphs():
    long = "## Long\n\n" + "\n\n".join(("y " * 900).strip() for _ in range(8))
    parts = hooks.split_parts(long)
    assert len(parts) > 1 and all(len(p) <= hooks.PART_LIMIT for p in parts)
    assert sum(p.count("y ") for p in parts) == long.count("y ")


def test_each_subagent_part_arrives_under_the_limit_and_names_itself(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, _big_voice(tmp_path))
    replies = [hooks.subagent_start({"session_id": "s"}, part=k) for k in range(1, hooks.PARTS + 1)]
    texts = [_context(r) for r in replies if r]
    assert len(texts) >= 2
    assert all(len(t) <= hooks.PART_LIMIT + 400 for t in texts), [len(t) for t in texts]
    assert all(f"part {k} of {len(texts)}" in t for k, t in enumerate(texts, 1))
    joined = "\n".join(texts)
    assert "# Voice: Big" in texts[0] and "voice wins" in texts[0]
    assert all(f"## Section {i}\n" in joined for i in range(12))
    assert "Not X but Y" in joined, "the patterns card travels too"


def test_without_a_part_the_hook_sends_everything_as_before(tmp_path, monkeypatch):
    """Plugin copies installed before this change call the hook without --part."""
    _config(tmp_path, monkeypatch, _big_voice(tmp_path))
    text = _context(hooks.subagent_start({"session_id": "s"}))
    assert all(f"## Section {i}\n" in text for i in range(12)) and "part 1 of" not in text


def test_a_short_voice_is_one_part_and_later_parts_send_nothing(tmp_path, monkeypatch):
    voice = tmp_path / "small.md"
    voice.write_text("# Voice: Small\n\n- No em dashes.\n")
    _config(tmp_path, monkeypatch, voice)
    assert "# Voice: Small" in _context(hooks.subagent_start({"session_id": "s"}, part=1))
    assert hooks.subagent_start({"session_id": "s"}, part=2) is None


def test_session_start_parts_carry_the_voice_and_the_rewrite_note(tmp_path, monkeypatch):
    home = tmp_path / "claude"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    _config(tmp_path, monkeypatch, _big_voice(tmp_path))
    texts = [_context(hooks.session_start({"session_id": "s"}, part=k)) for k in range(1, hooks.PARTS + 1)]
    texts = [t for t in texts if t]
    assert len(texts) >= 2 and all(len(t) <= hooks.PART_LIMIT + 400 for t in texts)
    joined = "\n".join(texts)
    assert "rewrites these automatically" in joined
    assert all(f"## Section {i}\n" in joined for i in range(12))


def test_too_much_text_says_where_the_rest_is(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, _big_voice(tmp_path, sections=60))
    last = _context(hooks.subagent_start({"session_id": "s"}, part=hooks.PARTS))
    assert "wr voice --core" in last and len(last) <= hooks.PART_LIMIT + 400


def test_the_command_takes_a_part(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, _big_voice(tmp_path))
    out = io.StringIO()
    assert hooks.run("subagent-start", io.StringIO('{"session_id": "s"}'), out, part=2) == 0
    assert "part 2 of" in json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]


def test_the_plugin_registers_every_part_for_both_events():
    registered = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
    for event, name in (("SessionStart", "session-start"), ("SubagentStart", "subagent-start")):
        commands = [h["command"] for group in registered[event] for h in group["hooks"]]
        for k in range(1, hooks.PARTS + 1):
            assert any(f"hook {name} --part {k}" in c for c in commands), (event, k)


def test_the_example_voice_fits_in_the_registered_parts(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, REPO / "voice" / "technical-colleague")
    texts = [_context(hooks.subagent_start({"session_id": "s"}, part=k)) for k in range(1, hooks.PARTS + 1)]
    joined = "\n".join(t for t in texts if t)
    assert "wr voice --core" not in joined, "the example voice no longer fits; register more parts"
    assert "## When rewriting an existing text" in joined


def test_a_long_note_about_rewritten_passages_fits_and_points_at_the_rest(tmp_path, monkeypatch):
    """The note listing each passage the markdown rewrite changed reached 13.9KB
    on 2026-09-21, and Claude saw a 2KB preview of it."""
    monkeypatch.setenv(hooks.STATE_ENV, str(tmp_path / "state"))
    d = hooks._state_dir("s", create=True)
    lines = [f'  "old passage {i} ' + "a" * 300 + f'" -> "new passage {i} ' + "b" * 300 + '"' for i in range(40)]
    (d / "done").write_text("wr rewrote 40 passages in /x/README.md:\n" + "\n".join(lines) + "\n")
    text = _context(hooks.prompt({"session_id": "s"}))
    assert len(text) <= hooks.PART_LIMIT + 400
    assert "old passage 0 " in text, "the note starts as it always did"
    full = [p for p in d.iterdir() if p.name.startswith("note-")]
    assert len(full) == 1 and str(full[0]) in text
    assert all(f"new passage {i} " in full[0].read_text() for i in range(40))


def test_a_short_note_is_sent_as_it_is(tmp_path, monkeypatch):
    monkeypatch.setenv(hooks.STATE_ENV, str(tmp_path / "state"))
    d = hooks._state_dir("s", create=True)
    (d / "done").write_text("wr kept /x/a.md as written\n")
    text = _context(hooks.prompt({"session_id": "s"}))
    assert text.endswith("wr kept /x/a.md as written\n")
    assert not [p for p in d.iterdir() if p.name.startswith("note-")]


# Audit of 2026-09-22.

def test_the_plugin_falls_back_when_wr_is_older_than_the_part_option(tmp_path):
    """The plugin updates from its marketplace, wr separately. An older wr
    rejects --part (argparse exits 2), which left sessions with no voice."""
    import os
    import stat
    import subprocess
    fake = tmp_path / "wr"
    fake.write_text('#!/bin/sh\ncase "$*" in *--part*) echo "wr: error: unrecognized arguments" >&2; exit 2;; esac\n'
                    'cat >/dev/null; echo \'{"old": "whole"}\'\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    registered = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    for event in ("SessionStart", "SubagentStart"):
        outs = []
        for h in registered[event][0]["hooks"]:
            r = subprocess.run(["sh", "-c", h["command"]], input='{"session_id": "s"}', capture_output=True,
                               text=True, env=env)
            assert r.returncode == 0, (event, h["command"], r.stderr)
            outs.append(r.stdout.strip())
        assert outs.count('{"old": "whole"}') == 1, (event, outs)
        assert all(o in ("", '{"old": "whole"}') for o in outs)


def test_a_configuration_error_is_reported_once_not_once_per_part(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('vocie = "x"\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    replies = [hooks.session_start({"session_id": "s"}, part=k) for k in range(1, hooks.PARTS + 1)]
    assert sum(1 for r in replies if r and r.get("systemMessage")) == 1


def test_a_failing_hook_reports_once_across_parts(monkeypatch):
    def boom(payload, spawn=None, part=None):
        raise RuntimeError("x")
    monkeypatch.setitem(hooks.HANDLERS, "session-start", boom)
    outs = []
    for k in range(1, hooks.PARTS + 1):
        out = io.StringIO()
        hooks.run("session-start", io.StringIO('{"session_id": "s"}'), out, part=k)
        outs.append(out.getvalue())
    assert sum(1 for o in outs if o) == 1


def test_one_part_needs_no_label(tmp_path, monkeypatch):
    voice = tmp_path / "small.md"
    voice.write_text("# Voice: Small\n\n- No em dashes.\n")
    _config(tmp_path, monkeypatch, voice)
    text = _context(hooks.subagent_start({"session_id": "s"}, part=1))
    assert "arrives in" not in text


def test_the_overflow_pointer_does_not_promise_the_patterns(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, _big_voice(tmp_path, sections=60))
    last = _context(hooks.subagent_start({"session_id": "s"}, part=hooks.PARTS))
    assert "wr voice --core" in last and "humanizer" in last


def test_a_note_that_cannot_be_saved_is_still_delivered(tmp_path, monkeypatch):
    monkeypatch.setenv(hooks.STATE_ENV, str(tmp_path / "state"))
    d = hooks._state_dir("s", create=True)
    (d / "done").write_text("wr rewrote 40 passages in /x/README.md:\n" + "\n".join("  " + "z" * 700 for _ in range(40)))
    real = hooks.Path.write_text
    def fail(self, *a, **k):
        if self.name.startswith("note-"):
            raise OSError("disk full")
        return real(self, *a, **k)
    monkeypatch.setattr(hooks.Path, "write_text", fail)
    text = _context(hooks.prompt({"session_id": "s"}))
    assert "wr rewrote 40 passages" in text and len(text) <= hooks.PART_LIMIT + 400

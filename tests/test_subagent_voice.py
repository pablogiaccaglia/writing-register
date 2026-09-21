"""Every subagent writes in the voice too (2026-09-16).

Measured over 5,654 transcripts: the session-start hook does not reach a
subagent, and it shows. After the voice began reaching every session, the main
conversation writes 0.40 em or en dashes per 1,000 words and a subagent 6.57,
against 1.58 in what the person types themselves; parenthetical glosses run 3.08
against 13.62. Subagents write reports, reviews and answers that reach people,
so they are given the same voice, on the event Claude Code fires when one
starts.
"""
import io
import json
from pathlib import Path

from writing_register.hooks import run, subagent_start

TESTER = str(Path(__file__).parent / "fixtures" / "voices" / "tester.md")


def _config(tmp_path, monkeypatch, voice=TESTER):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'voice = "{voice}"\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    return cfg


def test_a_subagent_is_given_the_voice(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    reply = subagent_start({"session_id": "s1", "agent_type": "general-purpose"})
    out = reply["hookSpecificOutput"]
    assert out["hookEventName"] == "SubagentStart"
    assert "# Voice: Tester" in out["additionalContext"]
    assert "no em dashes" in out["additionalContext"].lower() or "em dash" in out["additionalContext"]


def test_the_voice_covers_what_people_read_and_not_the_report_to_the_caller(tmp_path, monkeypatch):
    """2026-09-16: a report to the agent that called you is read by a
    model, and a model reads a dense, plain report better. The voice is for
    people, so it stops at the boundary between them."""
    _config(tmp_path, monkeypatch)
    text = subagent_start({"session_id": "s1"})["hookSpecificOutput"]["additionalContext"]
    head = text.split("\n\n")[0]
    assert "documents, code comments" in head and "replies in this chat" not in head
    assert "plain, dense and literal" in head, head


def test_without_a_voice_a_subagent_still_gets_the_patterns(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, voice="none")
    text = subagent_start({"session_id": "s1"})["hookSpecificOutput"]["additionalContext"]
    assert "Not X but Y" in text and "# Voice" not in text


def test_the_event_is_wired_to_the_handler(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    out = io.StringIO()
    assert run("subagent-start", io.StringIO(json.dumps({"session_id": "s1"})), out) == 0
    assert "Voice: Tester" in out.getvalue()


def test_the_plugin_registers_the_event():
    import pathlib
    spec = json.loads((pathlib.Path(__file__).resolve().parent.parent / "hooks" / "hooks.json").read_text())
    entries = spec["hooks"]["SubagentStart"]
    assert any("subagent-start" in h["command"] for e in entries for h in e["hooks"])

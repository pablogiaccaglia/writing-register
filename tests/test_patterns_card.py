"""Both halves travel in every modality (2026-09-16).

wr has two sets of rules: the humanizer skill, which says what machine writing
looks like, and a voice, which says how this person wants to read. A rewrite
has carried both since the beginning, in one call, with the voice winning
where they disagree. Steering carried only the voice, so a user with no voice
was steered by nothing at all, and nobody was ever told the patterns.

The skill is 28,728 characters, too much to put in every session and every
subagent, so what travels is a card generated from its own headings. It cannot
drift: a test fails when the skill gains or renames a pattern.
"""
from pathlib import Path
import re

from writing_register import config, hooks, style
from writing_register.patterns import SKILL, card

TESTER = str(Path(__file__).parent / "fixtures" / "voices" / "tester.md")


def _config(tmp_path, monkeypatch, voice=TESTER):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'voice = "{voice}"\nauto = ["markdown"]\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))


def test_the_card_names_every_pattern_the_skill_has():
    text = card()
    headings = re.findall(r"(?m)^### (\d+)\. (.+)$", SKILL.read_text(encoding="utf-8"))
    assert headings, "the vendored skill must have numbered patterns"
    for number, name in headings:
        assert f"{number}." in text, f"pattern {number} is missing from the card"
        assert name.split(",")[0][:18].lower() in text.lower(), name
    assert len(text) <= 1800, f"the card is {len(text)} characters"


def test_a_session_with_no_voice_is_still_told_the_patterns(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, voice="none")
    text = hooks.session_start({"session_id": "s"})["hookSpecificOutput"]["additionalContext"]
    assert "Not X but Y" in text or "not X but Y" in text.lower()
    assert "# Voice" not in text


def test_a_session_with_a_voice_is_told_both_and_which_wins(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    text = hooks.session_start({"session_id": "s"})["hookSpecificOutput"]["additionalContext"]
    assert "# Voice: Tester" in text and "Not X but Y" in text
    assert "voice wins" in text


def test_a_subagent_with_no_voice_is_still_told_the_patterns(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, voice="none")
    reply = hooks.subagent_start({"session_id": "s"})
    assert reply is not None, "a subagent with no voice still needs the patterns"
    assert "Not X but Y" in reply["hookSpecificOutput"]["additionalContext"]


def test_the_style_works_without_a_voice_and_carries_both_with_one(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, voice="none")
    plain = style.build()
    assert "Not X but Y" in plain and "# Voice" not in plain
    _config(tmp_path, monkeypatch)
    both = style.build()
    assert "Not X but Y" in both and "# Voice: Tester" in both and "voice wins" in both

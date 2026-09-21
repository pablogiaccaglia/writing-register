"""The end-of-turn note (review of 2026-09-15).

A rewrite with no changed passage printed a header promising a list followed
by nothing; quotes inside a passage were not escaped, so the old and new text
ran together; cutting at 400 characters could hide a change made further in;
and the session note said "sentence" where the note lists passages."""
from pathlib import Path
from types import SimpleNamespace

from writing_register import hooks


def _note(pairs, passages=1):
    return hooks._rewritten_note(Path("/repo/docs/X.md"), SimpleNamespace(passages=passages, seconds=3.2, pairs=pairs))


def test_a_rewrite_that_changed_only_spacing_says_so():
    note = _note([])
    assert "spacing" in note and "old -> new" not in note


def test_quotes_inside_a_passage_are_escaped():
    note = _note([('He said "stop" here and left.', 'He said "halt" here and left.')])
    assert '"He said \\"stop\\" here and left." -> "He said \\"halt\\" here and left."' in note


def test_a_long_passage_shows_the_part_that_changed():
    head = "word " * 120
    old, new = head + "the report writes findings.", head + "the push script writes findings."
    note = _note([(old, new)])
    assert "push script" in note and "the report writes" in note
    assert max(len(line) for line in note.splitlines()[1:]) < 1000

"""Rules the audit of 2026-09-16 found no test would notice breaking.

Each rule below survived a mutation of the code it lives in
while the whole suite stayed green: the two guards that refuse a revert which
would duplicate or scatter text, the string checks that run again on the text
after blocks go back, the three-line window a cited span must fall inside, and
the eight-character minimum that stops a citation resting on a fragment.
"""
import pytest

from writing_register.changes import RevertUnsafe, changes_between, revert_blocks
from writing_register.verify import _judge
from writing_register.changes import Change

from test_verdict_rules import _export


def _numbers(changes):
    return {c.number for c in changes if c.kind in ("added", "changed")}


def test_a_revert_refuses_when_the_rejected_paragraph_was_split_around_text_that_stayed():
    """The rewrite put the two halves of one original paragraph on either side
    of a paragraph it kept. Replacing a run that spans the kept text would move
    it; the guard refuses instead, and the whole rewrite is refused upstream."""
    old = "# T\n\nAlpha one here. Beta two here.\n\nA kept paragraph.\n"
    new = ("# T\n\nAlpha one rewritten.\n\nA kept paragraph.\n\nBeta two rewritten.\n")
    changes = [Change("changed", "Alpha one here.", "Alpha one rewritten.",
                      new_block=1, old_block=1, old_blocks=(1,), number=1),
               Change("changed", "Beta two here.", "Beta two rewritten.",
                      new_block=3, old_block=1, old_blocks=(1,), number=2)]
    with pytest.raises(RevertUnsafe):
        revert_blocks(new, old, changes, {1, 2})


def test_a_revert_refuses_when_putting_the_original_back_would_repeat_kept_text():
    """The original paragraph still stands elsewhere in the rewrite, so putting
    it back a second time would say the same thing twice."""
    old = "# T\n\nThe report writes the rows.\n\nAnother paragraph.\n"
    new = "# T\n\nThe push script writes the rows instead.\n\nThe report writes the rows.\n"
    changes = [Change("changed", "The report writes the rows.",
                      "The push script writes the rows instead.",
                      new_block=1, old_block=1, old_blocks=(1,), number=1)]
    with pytest.raises(RevertUnsafe):
        revert_blocks(new, old, changes, {1})


def _verdict(path, line, span, claim_type="mechanism"):
    return {"id": 1, "verdict": "TRUE", "claim_type": claim_type,
            "effect": {"path": path, "line": line, "span": span},
            "condition": None, "negative": [], "reason": "r", "reason_code": None,
            "delta": "", "original": [], "actor": None}


def test_a_cited_span_must_sit_within_three_lines_of_the_line_it_names(tmp_path):
    root = _export(tmp_path)
    long_file = root / "lib" / "long.py"
    long_file.write_text("\n".join(["# filler"] * 20 + ["KEEP_MONTHS = 13"] + ["# filler"] * 5))
    claim = Change("added", "", "The limit is thirteen months.", number=1)
    near = _judge(root, claim, _verdict("lib/long.py", 20, "KEEP_MONTHS = 13"))
    far = _judge(root, claim, _verdict("lib/long.py", 5, "KEEP_MONTHS = 13"))
    assert near.effective == "TRUE", near.reason
    assert far.effective == "UNVERIFIABLE", "a span ten lines from the line it cites decides nothing"


def test_a_citation_cannot_rest_on_a_fragment(tmp_path):
    root = _export(tmp_path)
    claim = Change("added", "", "The limit is thirteen months.", number=1)
    whole = _judge(root, claim, _verdict("lib/retention.py", 1, "KEEP_MONTHS = 13"))
    fragment = _judge(root, claim, _verdict("lib/retention.py", 1, "13"))
    assert whole.effective == "TRUE", whole.reason
    assert fragment.effective == "UNVERIFIABLE", "two characters are not evidence"


def test_the_string_checks_run_again_on_the_text_after_blocks_go_back(tmp_path, monkeypatch):
    """Putting a paragraph back can break what the first check passed: the
    original may hold a link or a piece of code the rewrite dropped. The audit
    replaced this second check with nothing and the suite stayed green."""
    from test_humanize_check import Model, _judge_database_false, _repo

    from writing_register import humanize as h
    root, doc = _repo(tmp_path)
    calls = []
    real = h._string_checks

    def once_more(*a, **kw):
        calls.append(1)
        return "a link was dropped" if len(calls) > 1 else real(*a, **kw)

    monkeypatch.setattr(h, "_string_checks", once_more)
    r = h.humanize(doc, spawn=Model(judge=_judge_database_false), root=root, write=False)
    assert len(calls) > 1, "the checks must run again after a revert"
    assert r.refused.startswith("after putting back"), r.refused


def test_the_checker_s_searches_have_a_budget_for_the_whole_reply(tmp_path):
    """Each search already had its own time limit, but a reply could carry any
    number of them, so a checker could keep wr busy for many minutes after its
    own call had finished (found in the audit)."""
    from writing_register.verify import _NEGATIVE_MAX, _negative_matches
    root = _export(tmp_path)
    many = [{"pattern": "zzz_never_appears", "dir": "."} for _ in range(_NEGATIVE_MAX + 1)]
    assert _negative_matches(root, many) is True, "too many searches rejects the verdict"
    assert _negative_matches(root, many[:2]) is False

"""`wr voice check`: the structure of a voice directory, checked (2026-09-21).

Each check exists because of a failure it prevents. The runtime reads only the
manifest and the rule files and stays lenient, so a slip in formatting never
leaves a session without its voice; this command is where the rest is caught.
Two of the failures happened for real: an edit to the single-file voice put
two rules in the core with their measurements pasted into the rule text and no
evidence recorded, and left a decision describing the old rule.
"""
import pytest

from writing_register import voice_tools as vt


def _good(tmp_path):
    root = tmp_path / "v"
    files = {
        "voice.toml": ('format = 1\nname = "T"\nbudget = 5000\nevidence = "required"\n'
                       'order = ["scope.md", "register.md", "kinds.md", "kinds/replies.md"]\n'),
        "rules/scope.md": "{#scope.what} This voice is for tests.\n",
        "rules/register.md": ("# Register\n\n"
                              "- {#register.no-dashes} No em dashes. "
                              "{#register.range refines=register.no-dashes} A range keeps its hyphen.\n"),
        "rules/kinds.md": "# By kind of text\n\n{#kinds.precedence} A kind's own rule wins.\n",
        "rules/kinds/replies.md": "# Replies\n\n{#replies.lead refines=kinds.precedence} Lead with the answer.\n",
        "evidence/register.md": "## Dashes\nRules: register.no-dashes, register.range\n\nA quote, 2026-09-21.\n",
        "evidence/scope.md": "## Scope\nRules: scope.what, kinds.precedence, replies.lead\n\nSeen on 2026-09-21.\n",
        "decisions.md": "## Dashes or not\nRules: register.no-dashes\nDecided: 2026-09-21\n\nNone.\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _messages(root, level=None, **kw):
    return [f.message for f in vt.check(root, **kw) if level is None or f.level == level]


def _edit(root, rel, old, new):
    p = root / rel
    text = p.read_text()
    assert old in text, (rel, old)
    p.write_text(text.replace(old, new))


def test_a_good_voice_has_nothing_to_report(tmp_path):
    assert vt.check(_good(tmp_path)) == []


def test_a_rule_file_missing_from_the_manifest_is_reported(tmp_path):
    """A rules file written but never listed would never reach the model."""
    root = _good(tmp_path)
    (root / "rules" / "math.md").write_text("# Maths\n\n{#math.symbols} Define every symbol.\n")
    assert any("math.md" in m and "order" in m for m in _messages(root, "error"))


def test_an_unknown_file_at_the_top_of_the_voice_is_reported(tmp_path):
    root = _good(tmp_path)
    (root / "notes.md").write_text("scratch\n")
    assert any("notes.md" in m for m in _messages(root, "error"))


def test_a_child_listed_before_its_parent_is_reported(tmp_path):
    root = _good(tmp_path)
    _edit(root, "voice.toml", '"kinds.md", "kinds/replies.md"', '"kinds/replies.md", "kinds.md"')
    assert any("kinds/replies.md" in m and "after" in m for m in _messages(root, "error"))


def test_a_line_that_is_not_a_rule_is_reported(tmp_path):
    root = _good(tmp_path)
    _edit(root, "rules/register.md", "# Register\n\n", "# Register\n\nA stray note without a marker.\n\n")
    assert any("register.md" in m and "marker" in m for m in _messages(root, "error"))


@pytest.mark.parametrize("line", ["<!-- a comment -->", "{#register.x} see [the guide](../guide.md)",
                                  "{#register.y} <!-- wr:end-of-core -->"])
def test_what_would_leak_into_the_prompt_or_break_the_view_is_reported(tmp_path, line):
    root = _good(tmp_path)
    _edit(root, "rules/register.md", "# Register\n\n", f"# Register\n\n{line}\n\n")
    assert _messages(root, "error")


def test_an_identifier_must_match_its_file_and_be_unique(tmp_path):
    root = _good(tmp_path)
    _edit(root, "rules/scope.md", "{#scope.what}", "{#register.no-dashes}")
    errors = _messages(root, "error")
    assert any("register.no-dashes" in m and "scope" in m for m in errors)
    assert any("register.no-dashes" in m and "twice" in m for m in errors)


def test_a_refinement_must_point_at_an_earlier_rule_that_exists(tmp_path):
    root = _good(tmp_path)
    _edit(root, "rules/register.md", "refines=register.no-dashes", "refines=register.nothing")
    assert any("register.nothing" in m for m in _messages(root, "error"))
    root = _good(tmp_path / "b")
    _edit(root, "rules/scope.md", "{#scope.what}", "{#scope.what refines=replies.lead}")
    assert any("replies.lead" in m and "earlier" in m for m in _messages(root, "error"))


def test_the_same_sentence_in_two_rules_is_reported(tmp_path):
    root = _good(tmp_path)
    _edit(root, "rules/kinds/replies.md", "Lead with the answer.", "No em dashes.")
    assert any("No em dashes" in m for m in _messages(root, "error"))


def test_a_core_over_its_budget_names_each_file_s_size(tmp_path):
    root = _good(tmp_path)
    _edit(root, "voice.toml", "budget = 5000", "budget = 50")
    over = [m for m in _messages(root, "error") if "budget" in m]
    assert over and "register.md" in over[0]


def test_evidence_must_point_at_rules_that_exist_and_carry_a_date(tmp_path):
    root = _good(tmp_path)
    _edit(root, "evidence/register.md", "Rules: register.no-dashes, register.range",
          "Rules: register.no-dashes, register.gone")
    _edit(root, "evidence/scope.md", "Seen on 2026-09-21.", "Seen recently.")
    errors = _messages(root, "error")
    assert any("register.gone" in m for m in errors)
    assert any("date" in m for m in errors)


def test_an_entry_without_a_rules_line_and_a_decision_without_a_date_are_reported(tmp_path):
    root = _good(tmp_path)
    _edit(root, "evidence/register.md", "Rules: register.no-dashes, register.range\n", "")
    _edit(root, "decisions.md", "Decided: 2026-09-21\n", "")
    errors = _messages(root, "error")
    assert any("Rules:" in m for m in errors)
    assert any("Decided:" in m for m in errors)


def test_with_evidence_required_every_rule_needs_evidence_or_a_decision(tmp_path):
    """The single-file voice's last edit added two rules with nothing behind them."""
    root = _good(tmp_path)
    _edit(root, "rules/register.md", "A range keeps its hyphen.",
          "A range keeps its hyphen. {#register.bold} Bold only a label.")
    assert any("register.bold" in m and "evidence" in m for m in _messages(root, "error"))


def test_a_retired_rule_may_keep_its_evidence_but_must_be_gone(tmp_path):
    root = _good(tmp_path)
    (root / "decisions.md").write_text(
        "## Dashes or not\nRules: register.no-dashes\nDecided: 2026-09-21\n\nNone.\n\n"
        "## Retire the range rule\nRules: none\nDecided: 2026-09-21\nRetires: register.range\n\nMerged.\n")
    assert any("register.range" in m and "retire" in m.lower() for m in _messages(root, "error"))
    _edit(root, "rules/register.md",
          " {#register.range refines=register.no-dashes} A range keeps its hyphen.", "")
    assert _messages(root, "error") == []


def test_a_measurement_pasted_into_a_rule_is_a_warning(tmp_path):
    """Evidence in the core travels in every session; it belongs beside the rule."""
    root = _good(tmp_path)
    _edit(root, "rules/scope.md", "This voice is for tests.",
          "This voice is for tests. Measured on 2026-09-16: 8.95 per 1,000 words.")
    warnings = _messages(root, "warning")
    assert any("scope.what" in m for m in warnings)


def test_a_decision_older_than_the_evidence_for_its_rule_is_a_warning(tmp_path):
    root = _good(tmp_path)
    _edit(root, "evidence/register.md", "A quote, 2026-09-21.", "A quote, 2026-09-21. A newer one, 2026-10-02.")
    assert any("Dashes or not" in m for m in _messages(root, "warning"))


def test_overlapping_rules_are_listed_only_on_request(tmp_path):
    root = _good(tmp_path)
    _edit(root, "rules/kinds/replies.md", "Lead with the answer.",
          "Lead with the answer and keep every em dash out of the reply.")
    _edit(root, "rules/register.md", "No em dashes.", "Keep every em dash out of the text.")
    assert not _messages(root, "advisory")
    assert _messages(root, "advisory", overlaps=True)


def test_the_command_prints_each_finding_and_exits_by_severity(tmp_path, monkeypatch):
    import io

    from writing_register.cli import main
    root = _good(tmp_path)
    out = io.StringIO()
    assert main(["voice", "check", str(root)], out=out) == 0
    _edit(root, "rules/scope.md", "{#scope.what}", "{#scope.what refines=nothing.here}")
    out = io.StringIO()
    assert main(["voice", "check", str(root)], out=out) == 1
    assert "rules/scope.md:1: error: scope.what refines nothing.here" in out.getvalue()


def test_the_command_checks_the_active_voice_when_given_none(tmp_path, monkeypatch):
    import io

    from writing_register.cli import main
    root = _good(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'voice = "{root}"\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    out = io.StringIO()
    assert main(["voice", "check"], out=out) == 0
    assert "no problems" in out.getvalue()


def test_a_month_and_a_year_in_words_count_as_a_date(tmp_path):
    """Evidence often reads "a message from July 2026"; that is a date."""
    root = _good(tmp_path)
    _edit(root, "evidence/scope.md", "Seen on 2026-09-21.", "His message from July 2026.")
    assert not any("date" in m for m in _messages(root, "error"))

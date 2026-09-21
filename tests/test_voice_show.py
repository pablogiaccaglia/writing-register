"""`wr voice show`: a rule with everything that points at it (2026-09-21).

Before changing a rule surgically, you need to know what else depends on it:
the rules that refine it, the evidence that supports it, and the decisions
that settled it. The last edit to the single-file voice changed the bold rule
and left a decision describing the old wording; this listing is what makes
such a decision impossible to miss.
"""
import io

from writing_register import voice_tools as vt
from writing_register.cli import main


def _voice(tmp_path):
    root = tmp_path / "v"
    files = {
        "voice.toml": 'format = 1\nname = "T"\norder = ["register.md", "kinds.md", "kinds/replies.md"]\n',
        "rules/register.md": "# Register\n\n- {#register.bold} Bold only a label.\n",
        "rules/kinds.md": "# By kind of text\n\n{#kinds.precedence} A kind's own rule wins.\n",
        "rules/kinds/replies.md": "# Replies\n\n{#replies.no-bold refines=register.bold} No bold in a reply.\n",
        "evidence/register.md": "## Bold measured\nRules: register.bold\n\nReplies ran at 8.95 per 1,000 words, 2026-09-16.\n",
        "decisions.md": "## Bold or not?\nRules: register.bold\nDecided: 2026-09-16\n\nOnly a label.\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def test_a_rule_is_shown_with_what_refines_it_its_evidence_and_its_decisions(tmp_path):
    text = vt.show(_voice(tmp_path), "register.bold")
    assert "register.bold (rules/register.md:3)" in text
    assert "Bold only a label." in text
    assert "replies.no-bold" in text
    assert "8.95 per 1,000 words" in text
    assert "Bold or not?" in text and "Only a label." in text


def test_a_file_is_shown_with_its_rules_and_what_they_refine(tmp_path):
    text = vt.show(_voice(tmp_path), "replies")
    assert "replies.no-bold" in text and "refines register.bold" in text


def test_an_unknown_rule_says_so(tmp_path):
    out = io.StringIO()
    assert main(["voice", "show", "register.nothing", "--voice-dir", str(_voice(tmp_path))], out=out) == 2
    assert "register.nothing" in out.getvalue()

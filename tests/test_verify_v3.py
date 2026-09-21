"""Checker v3: what the second replay (2026-09-15) showed.

1. Actor confusion. "The report fills in the finding itself" was FALSE in one
   run and TRUE in the next, citing push_findings.py, the code of another
   component. A TRUE on a sentence that names a component must cite that
   component's code or code that uses it. v3 guessed the component from the
   sentence; v4 has the checker name it (tests/test_verify_v4.py), so the test
   of the guess is gone and the ones left check that no guess misfires.
2. Laziness. On one project's README the checker answered "I didn't verify" for
   seven claims in two runs out of two and used 8 to 10 turns, where the first
   version used 17. UNVERIFIABLE has to mean searched and not found.
3. Effort. The checker's effort level is a parameter, so it can be measured.
4. The guard. "The tool raises role-drift" came back TRUE citing the line that
   adds the finding, inside `if comparable:`, which no plug-in makes true. A
   TRUE on a line inside a guard must cite the guard.
5. Rule versus happening. "Snapshot folders are removed after 13 months" came
   back TRUE citing the retention policy. A document shows a rule exists, not
   that anything carries it out."""
import json

from writing_register.changes import Change
from writing_register.spawn import Answer
from writing_register.verify import SYSTEM, verify_claims


def _repo(tmp_path):
    root = tmp_path / "export"
    (root / "scripts" / "lib").mkdir(parents=True)
    (root / "scripts" / "lib" / "report.py").write_text(
        "def build(findings):\n    return [f for f in findings if f.blocks]\n")
    (root / "scripts" / "lib" / "push_findings.py").write_text(
        "from .archive_api import Client\n\ndef push(props):\n    if prop.get('owner') != 'tool':\n        client.create_finding(props)\n")
    (root / "scripts" / "lib" / "cli.py").write_text(
        "from .report import build\n\ndef main():\n    rows = build(load())\n    print(rows)\n")
    (root / "scripts" / "lib" / "retention.py").write_text("def violations(folders):\n    return [f for f in folders if f.age > 395]\n")
    return root


class Checker:
    def __init__(self, verdicts):
        self.verdicts, self.kwargs = verdicts, []

    def run(self, prompt, **kw):
        self.kwargs.append(kw)
        return Answer(stdout=json.dumps({"structured_output": {"verdicts": self.verdicts}, "num_turns": 9,
                                         "total_cost_usd": 0.1}), command=("claude",), duration_seconds=1)


def _true(number, path, line, span):
    return {"id": number, "verdict": "TRUE", "claim_type": "mechanism", "effect": {"path": path, "line": line, "span": span},
            "condition": None, "negative": [], "reason": "r", "reason_code": None, "delta": "", "original": []}


def _run(root, sentence, verdict):
    claim = Change("added", "", sentence, number=1)
    return verify_claims(sentence, [claim], root, spawn=Checker([verdict]), probes=[]).by_number[1]


def test_a_true_citing_the_named_component_stands(tmp_path):
    root = _repo(tmp_path)
    v = _run(root, "The report keeps only the findings that block.",
             _true(1, "scripts/lib/report.py", 2, "return [f for f in findings if f.blocks]"))
    assert v.effective == "TRUE"


def test_a_true_citing_code_that_uses_the_named_component_stands(tmp_path):
    root = _repo(tmp_path)
    v = _run(root, "The command line prints what the report builds.",
             _true(1, "scripts/lib/cli.py", 4, "rows = build(load())"))
    assert v.effective == "TRUE"
    v = _run(root, "The report output is printed by the command line.",
             _true(1, "scripts/lib/cli.py", 5, "print(rows)"))
    assert v.effective == "TRUE"


def test_a_sentence_naming_no_component_is_not_touched_by_the_rule(tmp_path):
    root = _repo(tmp_path)
    v = _run(root, "Snapshot folders older than 13 months are reported as violations.",
             _true(1, "scripts/lib/retention.py", 2, "return [f for f in folders if f.age > 395]"))
    assert v.effective == "TRUE"


def test_a_false_citing_another_component_is_how_an_actor_error_is_shown(tmp_path):
    root = _repo(tmp_path)
    verdict = dict(_true(1, "scripts/lib/push_findings.py", 5, "client.create_finding(props)"), verdict="FALSE")
    assert _run(root, "The report writes each finding as a row in the archive.", verdict).effective == "FALSE"


def test_the_system_prompt_says_unverifiable_means_searched():
    lowered = SYSTEM.lower()
    assert "not searched" in lowered or "without searching" in lowered
    assert "names who" in lowered or "names a component" in lowered


def test_the_effort_level_reaches_the_checker_call(tmp_path):
    root = _repo(tmp_path)
    s = Checker([])
    verify_claims("x", [], root, spawn=s, probes=[], effort="high")
    assert s.kwargs[0]["effort"] == "high"


def _guarded(root):
    (root / "scripts" / "lib" / "reconcile.py").write_text(
        "def reconcile(ctx, module, rows):\n"
        "    comparable = getattr(module, \"ROLE_COMPARABLE\", False)\n"
        "    for row in rows:\n"
        "        if comparable and row.role != row.registered_role:\n"
        "            add(ctx, \"role-drift\", row)\n"
        "        add(ctx, \"seen\", row)\n")
    return root


def _verdict(effect, condition=None, claim_type="mechanism", verdict="TRUE"):
    return {"id": 1, "verdict": verdict, "claim_type": claim_type, "effect": effect, "condition": condition,
            "negative": [], "reason": "r", "reason_code": None, "delta": "", "original": []}


def test_the_system_prompt_separates_a_rule_from_its_enforcement():
    assert "carries it out" in SYSTEM or "carries them out" in SYSTEM
    assert "guard" in SYSTEM


def test_the_guard_and_the_rule_are_asked_for_in_the_prompt_not_enforced(tmp_path):
    """Measured on eight saved replies (2026-09-15): enforcing them caught no
    wrong sentence the checker had not, and reverted 25 more correct ones."""
    root = _guarded(_repo(tmp_path))
    effect = {"path": "scripts/lib/reconcile.py", "line": 5, "span": 'add(ctx, "role-drift", row)'}
    assert _run(root, "A differing role is recorded as role-drift.", _verdict(effect)).effective == "TRUE"
    (root / "docs").mkdir()
    (root / "docs" / "retention.md").write_text("| Snapshot folders | git | 13 months, then removed |\n")
    doc = {"path": "docs/retention.md", "line": 1, "span": "13 months, then removed"}
    assert _run(root, "Snapshot folders are removed after 13 months.", _verdict(doc, claim_type="policy")).effective == "TRUE"

"""The checker: a sandboxed model call that verifies what a rewrite added or
changed against the repository's code, and the rules wr applies to its
verdicts.

2026-09-15. Five sentences a rewrite got wrong in one repository passed every string check; a person found them by reading the diff
against the code. The checker does that reading with Read, Grep and Glob on an
export of the tracked files. Because wrong sentences are under 1% of changed
ones, the rules below exist to keep false reverts near zero: a TRUE or FALSE
must cite the code that decides the claim and that citation must check out;
a verdict resting on an absence names the grep that found nothing, which wr
re-runs; SAME is allowed for a rephrase that claims nothing new; and a run
that demonstrably did not look is refused as a whole."""
import json
import re
from pathlib import Path

from writing_register.changes import Change, changes_between, claims
from writing_register.spawn import Answer
from writing_register.verify import (SCHEMA, Verification, build_check_prompt, make_probes,
                                     verify_claims)

OLD = """# Findings

The classes are defined in `register/schema.json`. A class either blocks or it does not.

ISO 27001 does not require quarterly reviews; an interval the team keeps is worth more than a stricter one it misses.
"""
NEW = """# Findings

The classes are defined in `register/schema.json`. The report writes each finding as a row in the Findings table of the archive. A class either blocks or it does not.

ISO 27001 does not require quarterly reviews, so the team chose an interval it will keep instead of a stricter one it would miss.
"""


def _repo(tmp_path):
    root = tmp_path / "export"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "push-findings.sh").write_text(
        "#!/bin/sh\n# push every finding to the archive database\ncurl -X POST https://api.archive.example/v1/pages\n")
    (root / "scripts" / "report.py").write_text(
        "def report(findings):\n    return [f for f in findings if f.blocks]\n")
    (root / "docs").mkdir()
    (root / "docs" / "usage.md").write_text("Findings go to the archive.\n")
    (root / ".env").write_text("ARCHIVE_TOKEN=secret_abc123\n")
    return root


class Checker:
    """A fake checker: answers with the verdicts given, in the CLI's JSON shape."""

    def __init__(self, verdicts, turns=6):
        self.verdicts, self.turns, self.prompts, self.kwargs = verdicts, turns, [], []

    def run(self, prompt, **kw):
        self.prompts.append(prompt)
        self.kwargs.append(kw)
        out = {"structured_output": {"verdicts": self.verdicts(prompt) if callable(self.verdicts) else self.verdicts},
               "num_turns": self.turns, "duration_ms": 1200, "total_cost_usd": 0.1, "is_error": False}
        return Answer(stdout=json.dumps(out), command=("claude",), duration_seconds=1.2)


def _claims():
    return claims(changes_between(OLD, NEW))


def _v(id, verdict, **fields):
    base = {"id": id, "verdict": verdict, "claim_type": "mechanism", "effect": None, "condition": None,
            "negative": [], "reason": "because", "reason_code": None, "delta": ""}
    base.update(fields)
    return base


def _probe_verdicts(prompt, root):
    """TRUE with a real citation for every probe the prompt carries."""
    out = []
    for m in re.finditer(r'"id": (\d+), "kind": "added", "new": "`([^`]+)` defines `([^`]+)`', prompt):
        path = root / m.group(2)
        line = next(i for i, l in enumerate(path.read_text().splitlines(), 1) if m.group(3) in l)
        out.append(_v(int(m.group(1)), "TRUE", claim_type="definition",
                      effect={"path": m.group(2), "line": line, "span": path.read_text().splitlines()[line - 1].strip()}))
    return out


def test_the_schema_names_the_verdicts_and_the_citation_fields():
    schema = json.loads(SCHEMA)
    item = schema["properties"]["verdicts"]["items"]
    assert set(item["properties"]["verdict"]["enum"]) == {"TRUE", "FALSE", "SAME", "ORIGINAL", "NOT_A_FACT", "UNVERIFIABLE"}
    assert {"id", "verdict", "claim_type", "effect", "reason"} <= set(item["required"])


def test_the_prompt_carries_the_claims_with_ids_and_the_document(tmp_path):
    root = _repo(tmp_path)
    prompt, system = build_check_prompt(NEW, _claims(), root, make_probes(root))
    assert '"id": 1' in prompt and "The report writes each finding" in prompt
    assert "CHANGED" in prompt or '"kind": "changed"' in prompt
    assert "# The document" in prompt and "ISO 27001" in prompt
    assert "decides" in system and "SAME" in system and "grep" in system.lower()


def test_probes_are_real_definitions_from_the_export(tmp_path):
    root = _repo(tmp_path)
    probes = make_probes(root)
    assert 1 <= len(probes) <= 2
    assert all("defines" in p.new for p in probes)
    assert any("report" in p.new for p in probes)


def test_a_false_with_a_citation_that_checks_out_reverts_the_sentence(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()
    added = next(c for c in cl if "The report writes" in c.new)

    def verdicts(prompt):
        return [_v(added.number, "FALSE", effect={"path": "scripts/push-findings.sh", "line": 3,
                                                 "span": "curl -X POST https://api.archive.example/v1/pages"},
                   reason="push-findings.sh writes them, report.py only filters")] + \
               [_v(c.number, "SAME", claim_type="none") for c in cl if c is not added] + _probe_verdicts(prompt, root)
    result = verify_claims(NEW, cl, root, spawn=Checker(verdicts))
    assert isinstance(result, Verification) and not result.did_not_check
    failing = result.failing()
    assert failing == {added.number}
    v = result.by_number[added.number]
    assert v.effective == "FALSE" and v.citation_ok and "push-findings.sh:3" in v.cited


def test_a_citation_that_does_not_check_out_makes_the_verdict_unverifiable(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()
    added = next(c for c in cl if "The report writes" in c.new)
    cases = [
        {"path": "scripts/nope.sh", "line": 1, "span": "curl"},                       # missing file
        {"path": "scripts/push-findings.sh", "line": 3, "span": "rm -rf everything"},   # span absent
        {"path": "../outside.sh", "line": 1, "span": "curl"},                          # outside the export
        {"path": "docs/usage.md", "line": 1, "span": "Findings go to the archive."},         # a doc, for a mechanism
        {"path": ".env", "line": 1, "span": "ARCHIVE_TOKEN=secret_abc123"},             # a secret file
    ]
    for effect in cases:
        def verdicts(prompt, effect=effect):
            return [_v(added.number, "TRUE", effect=effect)] + \
                   [_v(c.number, "SAME", claim_type="none") for c in cl if c is not added] + _probe_verdicts(prompt, root)
        result = verify_claims(NEW, cl, root, spawn=Checker(verdicts))
        v = result.by_number[added.number]
        assert v.effective == "UNVERIFIABLE" and not v.citation_ok, effect
    assert "secret_abc123" not in " ".join(x.cited + x.reason for x in result.verdicts)


def test_a_guard_the_checker_cites_does_not_decide_the_verdict(tmp_path):
    """The checker is asked to cite the guard a line runs under, and wr used to
    reject a TRUE whose cited guard was not in the same file above the effect.
    Measured on 2026-09-15 over the saved v4 runs and the fresh v6 runs, that
    check caught no wrong sentence and put back 5 correct ones in each set, so
    the verdict now stands on its effect citation alone."""
    root = _repo(tmp_path)
    (root / "scripts" / "reconcile.py").write_text(
        "def run(ctx, module):\n    comparable = getattr(module, 'ROLE_COMPARABLE', False)\n"
        "    if comparable:\n        add(ctx, 'role-drift')\n")
    added = Change("added", "", "Role drift is recorded for modules that compare roles.", number=1)
    cl = [added]
    good = {"path": "scripts/reconcile.py", "line": 2, "span": "getattr(module, 'ROLE_COMPARABLE', False)"}
    elsewhere = {"path": "scripts/push-findings.sh", "line": 2, "span": "push every finding"}
    broken = {"path": "scripts/nope.py", "line": 9, "span": "not there at all"}
    for condition in (good, elsewhere, broken, None):
        def verdicts(prompt, condition=condition):
            return [_v(added.number, "TRUE", effect={"path": "scripts/reconcile.py", "line": 4, "span": "add(ctx, 'role-drift')"},
                       condition=condition)] + _probe_verdicts(prompt, root)
        result = verify_claims(NEW, cl, root, spawn=Checker(verdicts))
        assert result.by_number[added.number].effective == "TRUE", condition
def test_negative_evidence_is_re_run_and_a_match_rejects_the_verdict(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()
    added = next(c for c in cl if "The report writes" in c.new)
    for pattern, expected in (("ROLE_COMPARABLE", "FALSE"), ("archive", "UNVERIFIABLE")):
        def verdicts(prompt, pattern=pattern):
            return [_v(added.number, "FALSE", effect={"path": "scripts/report.py", "line": 2, "span": "if f.blocks"},
                       negative=[{"pattern": pattern, "dir": "scripts"}])] + \
                   [_v(c.number, "SAME", claim_type="none") for c in cl if c is not added] + _probe_verdicts(prompt, root)
        result = verify_claims(NEW, cl, root, spawn=Checker(verdicts))
        assert result.by_number[added.number].effective == expected, pattern


def test_false_on_a_changed_pair_needs_a_delta_the_new_wording_adds(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()
    pair = next(c for c in cl if c.kind == "changed" and "interval" in c.new)
    effect = {"path": "scripts/report.py", "line": 1, "span": "def report(findings):"}
    for delta, expected in (("the team chose", "FALSE"), ("quarterly reviews", "SAME"), ("", "SAME")):
        def verdicts(prompt, delta=delta):
            return [_v(pair.number, "FALSE", effect=effect, delta=delta)] + \
                   [_v(c.number, "SAME", claim_type="none") for c in cl if c is not pair] + _probe_verdicts(prompt, root)
        result = verify_claims(NEW, cl, root, spawn=Checker(verdicts))
        assert result.by_number[pair.number].effective == expected, delta


def test_same_on_an_added_sentence_is_unverifiable(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()
    added = next(c for c in cl if "The report writes" in c.new)
    result = verify_claims(NEW, cl, root, spawn=Checker(lambda p: [_v(c.number, "SAME", claim_type="none") for c in cl] + _probe_verdicts(p, root)))
    assert result.by_number[added.number].effective == "UNVERIFIABLE"


def test_outside_repo_is_a_reason_that_reverts_without_counting_as_not_looking(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()
    pair = next(c for c in cl if c.kind == "changed" and "interval" in c.new)
    result = verify_claims(NEW, cl, root, spawn=Checker(lambda p: [
        _v(pair.number, "UNVERIFIABLE", claim_type="history", reason_code="outside_repo",
           reason="whether the team chose an interval is not in the code")] +
        [_v(c.number, "SAME", claim_type="none") for c in cl if c is not pair] + _probe_verdicts(p, root)))
    assert pair.number in result.failing() and not result.did_not_check
    assert result.stats.omitted == 0


def test_a_run_that_did_not_look_is_refused_as_a_whole(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()
    lazy = Checker(lambda p: [_v(c.number, "UNVERIFIABLE", reason_code="not_found") for c in cl] + _probe_verdicts(p, root))
    result = verify_claims(NEW, cl, root, spawn=lazy)
    assert result.did_not_check and "unverifiable" in result.why.lower()
    missing = Checker(lambda p: [_v(c.number, "SAME", claim_type="none") for c in cl[1:]] + _probe_verdicts(p, root))
    result = verify_claims(NEW, cl, root, spawn=missing)
    assert result.did_not_check and result.stats.omitted == 1 and "omitted" in result.why.lower()


def test_a_failed_probe_means_the_checker_did_not_look(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()

    def verdicts(prompt):
        real = [_v(c.number, "SAME", claim_type="none") for c in cl]
        probes = [dict(v, verdict="UNVERIFIABLE", effect=None, reason_code="not_found") for v in _probe_verdicts(prompt, root)]
        return real + probes
    result = verify_claims(NEW, cl, root, spawn=Checker(verdicts))
    assert result.did_not_check and "probe" in result.why.lower()


def test_the_checker_is_called_with_the_read_only_sandbox_and_the_schema(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()
    s = Checker(lambda p: [_v(c.number, "SAME", claim_type="none") for c in cl] + _probe_verdicts(p, root))
    verify_claims(NEW, cl, root, spawn=s, timeout=123)
    kw = s.kwargs[0]
    assert Path(kw["read_root"]) == root and kw["json_schema"] == SCHEMA and kw["timeout"] == 123
    assert "decides" in kw["system_prompt"]


def test_spans_in_the_report_are_capped_and_key_shaped_strings_redacted(tmp_path):
    root = _repo(tmp_path)
    (root / "scripts" / "cfg.py").write_text("TOKEN = 'ghp_" + "a" * 40 + "'  # " + "x" * 300 + "\n")
    added = Change("added", "", "The token setting is read at start-up from the configuration.", number=1)
    cl = [added]
    long_span = "TOKEN = 'ghp_" + "a" * 40 + "'  # " + "x" * 300
    result = verify_claims(NEW, cl, root, spawn=Checker(lambda p: [
        _v(added.number, "TRUE", effect={"path": "scripts/cfg.py", "line": 1, "span": long_span})] +
        [_v(c.number, "SAME", claim_type="none") for c in cl if c is not added] + _probe_verdicts(p, root)))
    v = result.by_number[added.number]
    assert v.citation_ok and len(v.cited) <= 200 and "ghp_" + "a" * 40 not in v.cited and "[redacted]" in v.cited


def test_a_reply_that_is_not_the_schema_is_a_format_failure(tmp_path):
    root = _repo(tmp_path)

    class Broken:
        def run(self, prompt, **kw):
            return Answer(stdout="not json at all", command=("claude",), duration_seconds=0.1)
    result = verify_claims(NEW, _claims(), root, spawn=Broken())
    assert result.did_not_check and "format" in result.why.lower()


# After the first replay (2026-09-15): on a runbook the first version reverted
# 13 of 24 correct sentences, nearly all advice, policy quoted from a document,
# or restatements of what the original already said.

def test_original_keeps_a_restatement_whose_quote_is_in_the_original(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()
    added = next(c for c in cl if "The report writes" in c.new)
    for quotes, expected in ((["The classes are defined in `register/schema.json`."], "ORIGINAL"),
                             (["The report writes each finding"], "UNVERIFIABLE"),
                             ([], "UNVERIFIABLE")):
        def verdicts(prompt, quotes=quotes):
            return [_v(added.number, "ORIGINAL", claim_type="none", original=quotes)] + \
                   [_v(c.number, "SAME", claim_type="none") for c in cl if c is not added] + _probe_verdicts(prompt, root)
        result = verify_claims(NEW, cl, root, spawn=Checker(verdicts), original=OLD)
        v = result.by_number[added.number]
        assert v.effective == expected, quotes
        assert (added.number in result.failing()) == (expected == "UNVERIFIABLE")


def test_not_a_fact_is_kept_and_counted(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()
    added = next(c for c in cl if "The report writes" in c.new)
    result = verify_claims(NEW, cl, root, original=OLD, spawn=Checker(lambda p: [
        _v(added.number, "NOT_A_FACT", claim_type="none")] +
        [_v(c.number, "SAME", claim_type="none") for c in cl if c is not added] + _probe_verdicts(p, root)))
    assert result.by_number[added.number].effective == "NOT_A_FACT"
    assert added.number not in result.failing() and result.stats.not_a_fact == 1


def test_the_prompt_carries_the_original_document_and_the_new_verdicts(tmp_path):
    root = _repo(tmp_path)
    prompt, system = build_check_prompt(NEW, _claims(), root, make_probes(root), original=OLD)
    assert "# The original document" in prompt and "an interval the team keeps is worth more" in prompt
    assert "ORIGINAL" in system and "NOT_A_FACT" in system and "policy" in system


def test_a_mechanism_claim_may_cite_front_end_code(tmp_path):
    """Replay agreement, 2026-09-15: "the Pipeline Lens shows the prompts, outputs
    and timings of every stage" was reverted in all three runs because the checker
    cited app/web/src/components/LensPanel.jsx and .jsx was not a code suffix."""
    root = _repo(tmp_path)
    (root / "app").mkdir()
    lines = {
        "LensPanel.jsx": "export function LensPanel({ stage }) {\n  return <span className=\"stage-duration\">{stage.duration}</span>\n}\n",
        "Panel.vue": "<template>\n  <span class=\"stage-duration\">{{ stage.duration }}</span>\n</template>\n",
        "Panel.svelte": "<span class=\"stage-duration\">{stage.duration}</span>\n",
    }
    for name, text in lines.items():
        (root / "app" / name).write_text(text)
    added = Change("added", "", "Each stage shows how long it took.", number=1)
    for name, text in lines.items():
        span = next(l.strip() for l in text.splitlines() if "stage-duration" in l)
        result = verify_claims(NEW, [added], root, probes=[], spawn=Checker(lambda p, n=name, s=span: [
            _v(1, "TRUE", effect={"path": f"app/{n}", "line": 2 if not n.endswith(".svelte") else 1, "span": s})]))
        assert result.by_number[1].effective == "TRUE", (name, result.by_number[1].reason)


# Fresh gate run of 2026-09-15: one run on a project README was refused because
# the checker did not confirm one of two probe definitions, and the record could
# not show which probe or what the checker said. Probes now come only from
# names defined once, outside test, fixture and vendor folders, the refusal
# names the probe, and the checker's answers to the probes are kept.

def test_probes_skip_tests_vendored_code_and_names_defined_twice(tmp_path):
    root = tmp_path / "export"
    for rel, text in {
        "tests/test_things.py": "def helper_for_tests():\n    return 1\n",
        "vendor/lib/copied.py": "def copied_function():\n    return 1\n",
        "lib/a.py": "def build_thing():\n    return 1\n",
        "lib/b.py": "def shared_name():\n    return 1\n",
        "lib/c.py": "def shared_name():\n    return 2\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    probes = make_probes(root, count=5)
    assert [p.new for p in probes] == ["`lib/a.py` defines `build_thing`."]


def test_the_refusal_names_the_probe_and_the_replies_are_kept(tmp_path):
    root = _repo(tmp_path)
    cl = _claims()
    probes = make_probes(root)

    def verdicts(prompt):
        real = [_v(c.number, "SAME", claim_type="none") for c in cl]
        failed = [dict(v, verdict="UNVERIFIABLE", effect=None, reason_code="not_found", reason="could not find it")
                  for v in _probe_verdicts(prompt, root)]
        return real + failed
    result = verify_claims(NEW, cl, root, spawn=Checker(verdicts), probes=probes)
    assert result.did_not_check and probes[0].new.strip(".") in result.why
    assert [r["probe"] for r in result.probe_replies] == [p.new for p in probes]
    assert all(r["reply"]["verdict"] == "UNVERIFIABLE" for r in result.probe_replies)

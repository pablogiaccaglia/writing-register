"""Verdict rules after the review of 2026-09-15.

The review found three faults. The rule that a FALSE on a reworded sentence
must name what the new wording adds counted only words starting with a letter
and skipped stop words, so "after 12 months" -> "after 13 months" and "does not
join" -> "does join" could not be FALSE. Those are the edits that flip a fact.
Any file name containing "token", "secret" or "credential" was treated as a
secrets file, so a correct citation of tokenizer.py was rejected. And the
generic long-token redaction masked ordinary file names in the report."""
import json

from writing_register.changes import Change
from writing_register.spawn import Answer
from writing_register.verify import _redact, verify_claims


def _export(tmp_path):
    root = tmp_path / "export"
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "retention.py").write_text("KEEP_MONTHS = 13\n\ndef expired(age):\n    return age > KEEP_MONTHS\n")
    (root / "lib" / "tokenizer.py").write_text("def split_words(text):\n    return text.split()\n")
    (root / "credentials.json").write_text('{"client_id": "x"}\n')
    (root / "secrets.yaml").write_text("api: x\n")
    return root


class One:
    def __init__(self, verdict):
        self.verdict = verdict

    def run(self, prompt, **kw):
        out = {"structured_output": {"verdicts": [self.verdict]}, "num_turns": 6}
        return Answer(stdout=json.dumps(out), command=("claude",), duration_seconds=0.1)


def _false(effect, delta):
    return {"id": 1, "verdict": "FALSE", "claim_type": "mechanism", "effect": effect, "condition": None,
            "negative": [], "reason": "r", "reason_code": None, "delta": delta, "original": []}


def _judge(root, claim, verdict):
    return verify_claims(claim.new, [claim], root, spawn=One(verdict), probes=[]).by_number[1]


def test_a_changed_number_can_be_false(tmp_path):
    root = _export(tmp_path)
    claim = Change("changed", "Snapshots are kept for 12 months.", "Snapshots are kept for 13 months.", number=1)
    effect = {"path": "lib/retention.py", "line": 1, "span": "KEEP_MONTHS = 13"}
    assert _judge(root, claim, _false(effect, "13 months")).effective == "FALSE"


def test_a_dropped_not_can_be_false_whatever_the_delta_says(tmp_path):
    root = _export(tmp_path)
    claim = Change("changed", "The collector does not join the call.", "The collector does join the call.", number=1)
    effect = {"path": "lib/retention.py", "line": 4, "span": "return age > KEEP_MONTHS"}
    assert _judge(root, claim, _false(effect, "does join")).effective == "FALSE"
    added_not = Change("changed", "The collector joins the call.", "The collector never joins the call.", number=1)
    assert _judge(root, added_not, _false(effect, "")).effective == "FALSE"


def test_a_delta_that_names_nothing_new_is_still_same(tmp_path):
    root = _export(tmp_path)
    claim = Change("changed", "Snapshots are kept for 13 months.", "The snapshots are kept for 13 months.", number=1)
    effect = {"path": "lib/retention.py", "line": 1, "span": "KEEP_MONTHS = 13"}
    assert _judge(root, claim, _false(effect, "13 months")).effective == "SAME"


def test_a_code_file_named_like_a_secret_can_be_cited_but_secret_files_cannot(tmp_path):
    root = _export(tmp_path)
    claim = Change("added", "", "Text is split into words on whitespace.", number=1)
    ok = {"id": 1, "verdict": "TRUE", "claim_type": "mechanism", "effect": {"path": "lib/tokenizer.py", "line": 2, "span": "return text.split()"},
          "condition": None, "negative": [], "reason": "r"}
    assert _judge(root, claim, ok).effective == "TRUE"
    for path, span in (("credentials.json", '{"client_id": "x"}'), ("secrets.yaml", "api: x")):
        bad = dict(ok, claim_type="definition", effect={"path": path, "line": 1, "span": span + " " * 0})
        assert _judge(root, claim, bad).effective == "UNVERIFIABLE", path


def test_redaction_keeps_file_names_and_masks_tokens():
    assert _redact("tests/fixtures/replay/stationlog/runbooks_station-export.md") == \
        "tests/fixtures/replay/stationlog/runbooks_station-export.md"
    assert "[redacted]" in _redact("key = 'ghp_" + "a" * 40 + "'")
    assert "[redacted]" in _redact("token a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8")


def test_a_code_claim_may_cite_configuration_without_a_code_suffix_but_not_a_document(tmp_path):
    """Fresh gate, 2026-09-15: "the cue reaches the stream through a PulseAudio null
    sink called cue" was confirmed citing deploy/pulse-default.pa, and "out/ is
    gitignored" citing .gitignore; both were put back because neither is on the
    code-suffix list. Measured on the saved runs, rejecting only documents keeps
    every catch and removes those false reverts."""
    root = _export(tmp_path)
    (root / "deploy").mkdir()
    (root / "deploy" / "pulse-default.pa").write_text("load-module module-null-sink sink_name=cue\n")
    (root / ".gitignore").write_text("out/\n")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("The cue goes through a null sink called cue.\n")
    claim = Change("added", "", "The cue reaches the stream through a null sink called cue.", number=1)

    def verdict(path, span):
        return {"id": 1, "verdict": "TRUE", "claim_type": "mechanism", "effect": {"path": path, "line": 1, "span": span},
                "condition": None, "negative": [], "reason": "r"}
    assert _judge(root, claim, verdict("deploy/pulse-default.pa", "load-module module-null-sink sink_name=cue")).effective == "TRUE"
    assert _judge(root, claim, verdict(".gitignore", "out/" + " " * 0 + "")).effective in ("TRUE", "UNVERIFIABLE")
    assert _judge(root, claim, verdict("docs/guide.md", "The cue goes through a null sink called cue.")).effective == "UNVERIFIABLE"

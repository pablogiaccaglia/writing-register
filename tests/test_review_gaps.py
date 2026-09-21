"""Behaviours the review (2026-09-15) found untested."""
import json

from writing_register.changes import Change
from writing_register.humanize import humanize
from writing_register.spawn import Answer
from writing_register.verify import CHECK_TIMEOUT, verify_claims

from test_humanize_check import Model, _repo


def _export(tmp_path):
    root = tmp_path / "export"
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "a.py").write_text("def run(x):\n    return x + 1\n")
    return root


class Reply:
    def __init__(self, payload):
        self.payload = payload

    def run(self, prompt, **kw):
        return Answer(stdout=json.dumps(self.payload), command=("claude",), duration_seconds=0.1)


def _true(number):
    return {"id": number, "verdict": "TRUE", "claim_type": "mechanism",
            "effect": {"path": "lib/a.py", "line": 2, "span": "return x + 1"}, "reason": "r"}


def test_many_verdicts_given_without_using_a_tool_refuse_the_run(tmp_path):
    root = _export(tmp_path)
    claims = [Change("added", "", f"Sentence number {i} says the run adds one.", number=i) for i in range(1, 8)]
    result = verify_claims("doc", claims, root, probes=[], spawn=Reply(
        {"structured_output": {"verdicts": [_true(i) for i in range(1, 8)]}, "num_turns": 2}))
    assert result.did_not_check and "without using a tool" in result.why


def test_a_reply_given_as_a_json_string_in_result_is_read(tmp_path):
    root = _export(tmp_path)
    claim = Change("added", "", "The run adds one to its input.", number=1)
    result = verify_claims("doc", [claim], root, probes=[], spawn=Reply(
        {"result": json.dumps({"verdicts": [_true(1)]}), "num_turns": 5}))
    assert not result.did_not_check and result.by_number[1].effective == "TRUE"


def test_a_check_timeout_of_zero_means_the_default(tmp_path):
    root, doc = _repo(tmp_path)
    model = Model()
    humanize(doc, spawn=model, root=root, check_timeout=0)
    assert model.checker_kwargs[0]["timeout"] == CHECK_TIMEOUT

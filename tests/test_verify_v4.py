"""Checker v4: the rules that misfired in replay 3 (2026-09-15).

On one project's README at high effort the checker found the right code, and wr's
v3 rules rejected six of its answers. The actor rule took an ordinary opening
word ("the stationlog", "the local", "the cloud") for a component because a
file name in that project contains it. The guard rule rejected a line inside an `if`
far above the cited line. v4: the checker names the acting component's file
in `actor`, and wr checks that the cited code is that file or uses it; the
guard rule looks only a few lines up; and the raw reply is kept, so rules can
be re-judged without another model call."""
import json

from writing_register.changes import Change
from writing_register.spawn import Answer
from writing_register.verify import SCHEMA, rejudge, verify_claims


def _repo(tmp_path):
    root = tmp_path / "export"
    (root / "scripts" / "lib").mkdir(parents=True)
    (root / "scripts" / "lib" / "report.py").write_text(
        "def build(findings):\n    return [f for f in findings if f.blocks]\n")
    (root / "scripts" / "lib" / "push_findings.py").write_text(
        "from .archive_api import Client\n\ndef push(client, props):\n    client.create_finding(props)\n")
    (root / "scripts" / "lib" / "cli.py").write_text(
        "from .report import build\n\ndef main():\n    rows = build(load())\n    print(rows)\n")
    (root / "src").mkdir()
    (root / "src" / "stationlog_studio.py").write_text("def serve():\n    return 'studio'\n")
    (root / "app.sh").write_text("#!/bin/sh\npython3 -m studio.server --host 127.0.0.1\n")
    body = ["def run(job):"] + ["    if job.ready:"] + ["        step()"] * 30 + ["        gather_background_pack(job)"]
    (root / "src" / "job.py").write_text("\n".join(body) + "\n")
    return root


class Checker:
    def __init__(self, verdicts):
        self.verdicts = verdicts

    def run(self, prompt, **kw):
        return Answer(stdout=json.dumps({"structured_output": {"verdicts": self.verdicts}, "num_turns": 9,
                                         "total_cost_usd": 0.1}), command=("claude",), duration_seconds=1)


def _true(effect, actor=None, condition=None):
    return {"id": 1, "verdict": "TRUE", "claim_type": "mechanism", "effect": effect, "condition": condition,
            "actor": actor, "negative": [], "reason": "r", "reason_code": None, "delta": "", "original": []}


def _run(root, sentence, verdict):
    claim = Change("added", "", sentence, number=1)
    return verify_claims(sentence, [claim], root, spawn=Checker([verdict]), probes=[])


def test_the_schema_has_an_actor_field():
    item = json.loads(SCHEMA)["properties"]["verdicts"]["items"]
    assert "actor" in item["properties"]


def test_an_opening_word_that_is_part_of_a_file_name_is_not_an_actor(tmp_path):
    root = _repo(tmp_path)
    v = _run(root, "Stationlog Studio is a local web app that you start with `./app.sh`.",
             _true({"path": "app.sh", "line": 2, "span": "python3 -m studio.server --host 127.0.0.1"}, actor="app.sh"))
    assert v.by_number[1].effective == "TRUE", v.by_number[1].reason


def test_the_actor_the_checker_names_is_recorded_not_enforced(tmp_path):
    """v4 enforced it; the ablation of 2026-09-15 showed the enforcement only
    added false reverts, so the field stays in the reply and nothing checks it."""
    root = _repo(tmp_path)
    v = _run(root, "The report fills in the finding itself.",
             _true({"path": "scripts/lib/push_findings.py", "line": 4, "span": "client.create_finding(props)"},
                   actor="scripts/lib/report.py"))
    assert v.by_number[1].effective == "TRUE"
def test_a_citation_in_code_that_uses_the_actor_stands(tmp_path):
    root = _repo(tmp_path)
    v = _run(root, "The command line prints what the report builds.",
             _true({"path": "scripts/lib/cli.py", "line": 4, "span": "rows = build(load())"}, actor="scripts/lib/report.py"))
    assert v.by_number[1].effective == "TRUE", v.by_number[1].reason


def test_an_actor_that_is_not_a_file_in_the_export_is_ignored(tmp_path):
    root = _repo(tmp_path)
    v = _run(root, "The team keeps only the findings that block.",
             _true({"path": "scripts/lib/report.py", "line": 2, "span": "return [f for f in findings if f.blocks]"},
                   actor="the team"))
    assert v.by_number[1].effective == "TRUE", v.by_number[1].reason


def test_a_guard_far_above_the_cited_line_does_not_count(tmp_path):
    root = _repo(tmp_path)
    v = _run(root, "Before it writes anything, the job gathers a background pack.",
             _true({"path": "src/job.py", "line": 33, "span": "gather_background_pack(job)"}, actor="src/job.py"))
    assert v.by_number[1].effective == "TRUE", v.by_number[1].reason


def test_the_raw_reply_is_kept_and_can_be_judged_again(tmp_path):
    root = _repo(tmp_path)
    claim = Change("added", "", "The report fills in the finding itself.", number=1)
    raw = _true({"path": "scripts/lib/push_findings.py", "line": 4, "span": "client.create_finding(props)"},
                actor="scripts/lib/push_findings.py")
    result = verify_claims(claim.new, [claim], root, spawn=Checker([raw]), probes=[])
    assert result.raw == {1: raw}
    again = rejudge(result.raw, [claim], root, original="")
    assert again.by_number[1].effective == result.by_number[1].effective

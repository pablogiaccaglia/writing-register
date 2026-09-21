"""`wr humanize` verifies its rewrite against the code before writing it.

2026-09-15. A rewrite made without the repository can add a sentence
that is false and passes every string check. After the string checks pass,
humanize lists the sentences the rewrite added or changed, asks the checker
(a second, read-only call on an export of the tracked files) about each one,
puts back every paragraph or list item that holds a sentence the checker
contradicted or could not verify, checks again and writes the rest. The
decisions: revert per block; refuse the whole rewrite when more blocks would
go back than the document allows (half of the changed blocks, and at most
eight of them or a quarter of a long document's); refuse it, keeping the
copy, when the checker demonstrably did not check."""
import json
import re
import subprocess
from pathlib import Path

from writing_register.humanize import humanize
from writing_register.spawn import Answer

OLD = """# Findings

The report keeps only the findings that block.

The push script sends the rows to the archive.

- review each finding
- record a decision
"""
NEW = """# Findings

The report keeps only the findings that block, and it writes each one to the archive database.

The push script sends the rows to the archive, all of them in a single request.

- review each finding
- record a decision
"""
PUSH_LINE = "client.create_rows(rows)"


def _repo(tmp_path, text=OLD):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "docs").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("*.refused.md\n.env\n")
    (root / "scripts" / "report.py").write_text("def report(findings):\n    return [f for f in findings if f.blocks]\n")
    (root / "scripts" / "push.py").write_text("def push(client, rows):\n    client.create_rows(rows)\n")
    (root / ".env").write_text("ARCHIVE_TOKEN=secret\n")
    doc = root / "docs" / "FINDINGS.md"
    doc.write_bytes(text.encode())
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start"], cwd=root, check=True)
    return root, doc


class Model:
    """First call: the rewrite. Later calls with read_root: the checker."""

    def __init__(self, rewrite=NEW, judge=None):
        self.rewrite, self.judge, self.calls, self.checker_kwargs = rewrite, judge, 0, []

    def run(self, prompt, **kw):
        self.calls += 1
        if kw.get("read_root") is None:
            return Answer(stdout=self.rewrite, command=("claude",), duration_seconds=0.1)
        root = Path(kw["read_root"])
        self.checker_kwargs.append({**kw, "export_existed": root.exists(),
                                    "has_env": (root / ".env").exists(),
                                    "has_doc": (root / "docs" / "FINDINGS.md").exists()})
        verdicts = []
        for line in prompt.splitlines():
            if not line.startswith('{"id"'):
                continue
            item = json.loads(line)
            probe = re.match(r"`([^`]+)` defines `([^`]+)`", item["new"])
            if probe:
                path = root / probe.group(1)
                number = next(i for i, l in enumerate(path.read_text().splitlines(), 1) if probe.group(2) in l)
                verdicts.append(_verdict(item["id"], "TRUE", probe.group(1), number,
                                         path.read_text().splitlines()[number - 1].strip(), "definition"))
                continue
            verdicts.append((self.judge or _judge_true)(item))
        verdicts = [v for v in verdicts if v is not None]
        out = {"structured_output": {"verdicts": verdicts}, "num_turns": 12, "total_cost_usd": 0.2}
        return Answer(stdout=json.dumps(out), command=("claude",), duration_seconds=0.1)


def _verdict(number, verdict, path=None, line=None, span=None, claim_type="mechanism", delta=""):
    effect = {"path": path, "line": line, "span": span} if path else None
    return {"id": number, "verdict": verdict, "claim_type": claim_type, "effect": effect, "condition": None,
            "negative": [], "reason": "judged", "reason_code": None, "delta": delta, "original": [], "actor": None}


def _judge_true(item):
    return _verdict(item["id"], "TRUE", "scripts/push.py", 2, PUSH_LINE)


def _judge_database_false(item):
    if "writes each one" in item["new"]:
        return _verdict(item["id"], "FALSE", "scripts/push.py", 2, PUSH_LINE, delta="writes each one to the archive database")
    return _judge_true(item)


def test_a_contradicted_sentence_puts_its_paragraph_back_and_the_rest_is_written(tmp_path):
    root, doc = _repo(tmp_path)
    model = Model(judge=_judge_database_false)
    r = humanize(doc, spawn=model, root=root)
    assert r.written and r.checked and model.calls == 2, r.refused
    text = doc.read_text()
    assert "The report keeps only the findings that block.\n" in text
    assert "all of them in a single request" in text and "writes each one" not in text
    assert len(r.reverted) == 1
    change, verdict = r.reverted[0]
    assert "writes each one" in change.new and verdict.effective == "FALSE" and "push.py:2" in verdict.cited


def test_the_checker_sees_an_export_of_tracked_files_that_is_removed_afterwards(tmp_path):
    root, doc = _repo(tmp_path)
    model = Model()
    humanize(doc, spawn=model, root=root)
    kw = model.checker_kwargs[0]
    assert kw["export_existed"] and kw["has_doc"] and not kw["has_env"]
    assert Path(kw["read_root"]) != root and not Path(kw["read_root"]).exists()
    assert kw["effort"] == "high"


def test_a_checker_that_did_not_check_refuses_the_rewrite_and_keeps_it(tmp_path):
    root, doc = _repo(tmp_path)

    def omit_one(item):
        return None if "single request" in item["new"] else _judge_true(item)
    r = humanize(doc, spawn=Model(judge=omit_one), root=root)
    assert not r.written and "checker" in r.refused and "omitted" in r.refused
    assert doc.read_text() == OLD
    assert r.kept and r.kept.read_text() == NEW


def test_more_than_half_the_changed_blocks_reverted_refuses_the_rewrite(tmp_path):
    """The cap: over half the changed blocks going back refuses the rewrite.
    Since the 2026-09-15 review the half rule needs at least three changed blocks."""
    old = "# Findings\n\nThe report keeps only the findings that block.\n\nThe push script sends the rows to the archive.\n\nThe offboarding script prints commands for a person.\n"
    new = ("# Findings\n\nThe report keeps only the findings that block, and it writes each one to the archive database.\n\n"
           "The push script sends the rows to the archive, all of them in a single request.\n\n"
           "The offboarding script prints commands for a person, and runs them itself afterwards.\n")
    root, doc = _repo(tmp_path, text=old)

    def mostly_false(item):
        if "single request" in item["new"]:
            return _judge_true(item)
        return _verdict(item["id"], "FALSE", "scripts/push.py", 2, PUSH_LINE, delta=item["new"])
    r = humanize(doc, spawn=Model(rewrite=new, judge=mostly_false), root=root)
    assert not r.written and "blocks" in r.refused and doc.read_text() == old
    assert r.kept is not None
def test_no_check_makes_one_call_and_writes_the_rewrite(tmp_path):
    root, doc = _repo(tmp_path)
    model = Model(judge=_judge_database_false)
    r = humanize(doc, spawn=model, root=root, check=False)
    assert r.written and not r.checked and model.calls == 1 and doc.read_text() == NEW


def test_a_rewrite_with_no_changed_sentence_makes_no_checker_call(tmp_path):
    root, doc = _repo(tmp_path)
    model = Model(rewrite=OLD)
    r = humanize(doc, spawn=model, root=root)
    assert model.calls == 1 and not r.written and not r.refused


def test_a_dry_run_returns_the_reverted_text_and_writes_nothing(tmp_path):
    root, doc = _repo(tmp_path)
    r = humanize(doc, spawn=Model(judge=_judge_database_false), root=root, write=False)
    assert not r.written and doc.read_text() == OLD
    assert "The report keeps only the findings that block.\n" in r.text and "single request" in r.text
    assert not list(doc.parent.glob("*.refused.md"))


def test_a_crlf_document_keeps_its_line_endings_after_a_revert(tmp_path):
    root, doc = _repo(tmp_path, text=OLD.replace("\n", "\r\n"))
    r = humanize(doc, spawn=Model(judge=_judge_database_false), root=root)
    assert r.written, r.refused
    raw = doc.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") and b"The report keeps only the findings that block.\r\n" in raw


def test_without_a_repository_there_is_nothing_to_check_against(tmp_path):
    doc = tmp_path / "loose.md"
    doc.write_text(OLD)
    model = Model(judge=_judge_database_false)
    r = humanize(doc, spawn=model, root=None)
    assert model.calls == 1 and not r.checked and r.written

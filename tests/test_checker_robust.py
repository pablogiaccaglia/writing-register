"""The checker path does not crash, leak or look at the wrong tree.

Review of 2026-09-15: a malformed checker reply crashed the CLI and lost the
paid rewrite; a failed export did the same and left the export folder behind;
with a subdirectory as root the export held no code; a repository with no
commits, or none at all, gave the checker nothing; a tracked symlink in the
export pointed outside it; and a checker-supplied search pattern could hang
wr."""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from writing_register import spawn as spawn_module
from writing_register import verify
from writing_register.changes import Change
from writing_register.humanize import humanize
from writing_register.spawn import Answer, _neutral_cwd, export_tracked, remove_export
from writing_register.verify import verify_claims

from test_humanize_check import NEW, OLD, Model, _judge_true, _repo


class Raw:
    def __init__(self, stdout):
        self.stdout = stdout

    def run(self, prompt, **kw):
        if kw.get("read_root") is None:
            return Answer(stdout=NEW, command=("claude",), duration_seconds=0.1)
        return Answer(stdout=self.stdout, command=("claude",), duration_seconds=0.1)


def test_a_result_string_that_is_not_an_object_is_a_format_failure(tmp_path):
    root, doc = _repo(tmp_path)
    r = humanize(doc, spawn=Raw(json.dumps({"result": "[1]"})), root=root)
    assert not r.written and "did not check" in r.refused and r.kept is not None


def test_a_negative_entry_that_is_not_an_object_rejects_the_verdict_without_crashing(tmp_path):
    root = tmp_path / "export"
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "a.py").write_text("def upload(x):\n    return x\n")
    claim = Change("added", "", "Nothing uploads the file anywhere.", number=1)
    reply = {"structured_output": {"verdicts": [{"id": 1, "verdict": "FALSE", "claim_type": "mechanism",
             "effect": {"path": "lib/a.py", "line": 1, "span": "def upload(x):"}, "negative": ["upload"],
             "reason": "r"}]}, "num_turns": 5}

    class One:
        def run(self, prompt, **kw):
            return Answer(stdout=json.dumps(reply), command=("claude",), duration_seconds=0.1)
    result = verify_claims(claim.new, [claim], root, spawn=One(), probes=[])
    assert result.by_number[1].effective == "UNVERIFIABLE"


def _exports():
    return {p.name for p in _neutral_cwd().glob("export-*")}


def test_an_export_that_fails_refuses_keeps_the_rewrite_and_leaves_nothing_behind(tmp_path, monkeypatch):
    root, doc = _repo(tmp_path)
    before = _exports()
    real_run = subprocess.run

    def failing_tar(args, *a, **kw):
        if args and args[0] == "tar":
            raise subprocess.CalledProcessError(2, args)
        return real_run(args, *a, **kw)
    monkeypatch.setattr(spawn_module.subprocess, "run", failing_tar)
    r = humanize(doc, spawn=Model(), root=root)
    assert not r.written and "checker could not run" in r.refused and r.kept is not None
    assert doc.read_text() == OLD
    assert _exports() == before


def test_a_subdirectory_root_still_exports_the_whole_repository(tmp_path):
    root, doc = _repo(tmp_path)
    model = Model()
    r = humanize(doc, spawn=model, root=root / "docs")
    assert r.checked, r.refused
    assert model.checker_kwargs and model.checker_kwargs[0]["export_existed"]
    export = export_tracked(root / "docs", overlay=[doc])
    try:
        assert (export / "scripts" / "push.py").exists() and (export / "docs" / "FINDINGS.md").exists()
    finally:
        remove_export(export)


def test_without_a_repository_or_commits_the_rewrite_is_written_unchecked_and_says_why(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    doc = plain / "NOTES.md"
    doc.write_text(OLD)
    model = Model()
    r = humanize(doc, spawn=model, root=plain)
    assert r.written and not r.checked and model.calls == 1 and "not a git repository" in r.check_skipped
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=empty, check=True)
    doc2 = empty / "NOTES.md"
    doc2.write_text(OLD)
    r = humanize(doc2, spawn=Model(), root=empty)
    assert r.written and not r.checked and "no commits" in r.check_skipped


def test_the_cli_says_when_a_rewrite_was_not_checked_and_why(tmp_path, monkeypatch):
    import io
    from writing_register.cli import main
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    plain = tmp_path / "plain"
    plain.mkdir()
    doc = plain / "NOTES.md"
    doc.write_text(OLD)
    out = io.StringIO()
    assert main(["humanize", str(doc), "--root", str(plain)], out=out, spawn=Model()) == 0
    assert "not checked against the code: not a git repository" in out.getvalue()


def test_the_export_holds_no_symlinks(tmp_path):
    root, doc = _repo(tmp_path)
    secret = tmp_path / "outside.txt"
    secret.write_text("TOP SECRET\n")
    os.symlink(secret, root / "scripts" / "link.txt")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "link"], cwd=root, check=True)
    export = export_tracked(root, overlay=[doc])
    try:
        assert not any(p.is_symlink() for p in export.rglob("*"))
        assert (export / "scripts" / "push.py").exists()
    finally:
        remove_export(export)


def test_a_search_pattern_that_would_hang_is_stopped_and_rejects_the_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "_NEGATIVE_TIMEOUT", 2)
    root = tmp_path / "export"
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "a.py").write_text("a" * 40 + "b\n")
    started = time.monotonic()
    assert verify._negative_matches(root, [{"pattern": "(a+)+$", "dir": "lib"}]) is True
    assert time.monotonic() - started < 15
    assert verify._negative_matches(root, [{"pattern": "ROLE_COMPARABLE", "dir": "lib"}]) is False
    assert verify._negative_matches(root, [{"pattern": "a{40}b", "dir": "lib"}]) is True

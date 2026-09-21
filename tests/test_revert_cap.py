"""Two faults from the review of 2026-09-15.

First: a rewrite that changed a single block could never be partly taken
back, because one rejected sentence was "1 of 1 changed blocks", over half; and
the copy kept after a refusal by the check was shown as "to read or use", with
no word that it still holds the sentences the check rejected.
Second: the checker read the committed code, so a document written for code
not yet committed had its sentences rejected."""
import io
import json
import re
import subprocess
from pathlib import Path

from writing_register.cli import main
from writing_register.humanize import humanize
from writing_register.spawn import Answer, export_tracked, remove_export

from test_humanize_check import Model, _judge_true, _repo, _verdict, PUSH_LINE

ONE_OLD = "# Findings\n\nThe report keeps only the findings that block.\n"
ONE_NEW = "# Findings\n\nThe report keeps just the blocking findings. It also writes each one to the archive database.\n"


def test_one_changed_block_with_a_rejected_addition_writes_the_rest(tmp_path):
    root, doc = _repo(tmp_path, text=ONE_OLD)

    def judge(item):
        if "writes each one" in item["new"]:
            return _verdict(item["id"], "FALSE", "scripts/push.py", 2, PUSH_LINE)
        return _judge_true(item)
    r = humanize(doc, spawn=Model(rewrite=ONE_NEW, judge=judge), root=root)
    assert r.written, r.refused
    assert doc.read_text() == "# Findings\n\nThe report keeps just the blocking findings.\n"


def test_a_copy_kept_after_the_check_refused_says_it_is_unchecked(tmp_path, monkeypatch):
    root, doc = _repo(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    model = Model(judge=lambda item: None if "single request" in item["new"] else _judge_true(item))
    out = io.StringIO()
    assert main(["humanize", str(doc), "--root", str(root)], out=out, spawn=model) == 1
    assert "has not passed the check against the code" in out.getvalue()


def test_the_export_holds_uncommitted_edits_and_new_files_but_no_secrets(tmp_path):
    root, doc = _repo(tmp_path)
    (root / "scripts" / "push.py").write_text("def push(client, rows):\n    client.create_rows_in_batches(rows)\n")
    (root / "scripts" / "new_tool.py").write_text("def new_tool():\n    return 1\n")
    (root / ".env.local").write_text("ARCHIVE_TOKEN=secret\n")
    (root / "scripts" / "report.py").unlink()
    export = export_tracked(root, overlay=[doc])
    try:
        assert "create_rows_in_batches" in (export / "scripts" / "push.py").read_text()
        assert (export / "scripts" / "new_tool.py").exists()
        assert not (export / ".env.local").exists() and not (export / ".env").exists()
        assert not (export / "scripts" / "report.py").exists()
    finally:
        remove_export(export)


def test_the_refusal_cap_grows_with_the_number_of_changed_blocks():
    """2026-09-16: "scale properly". The flat cap of eight blocks refused a
    long document at a low rejection rate. One repository's operations guide
    changes 63 blocks in a rewrite, so nine rejected blocks, 14% of them, threw
    away a rewrite that was 86% good, while a six-block document could lose
    every block but two and still be written. The cap is now eight blocks or a
    quarter of the changed ones, whichever is larger, so it only grows for a
    document that changes more than 32 blocks. The half rule still governs the
    short ones."""
    from writing_register.humanize import _revert_cap
    assert [_revert_cap(n) for n in (1, 6, 20, 32)] == [8, 8, 8, 8]
    assert _revert_cap(33) == 9
    assert _revert_cap(63) == 16
    assert _revert_cap(208) == 52

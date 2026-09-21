"""What `wr humanize` prints when it verifies a rewrite against the code (2026-09-15)."""
import io

from writing_register.cli import main

from test_humanize_check import OLD, NEW, Model, _judge_database_false, _judge_true, _repo


def _run(argv, model, monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    out = io.StringIO()
    code = main(argv, out=out, spawn=model)
    return code, out.getvalue()


def test_the_line_says_it_was_checked_and_lists_each_sentence_put_back(tmp_path, monkeypatch):
    root, doc = _repo(tmp_path)
    model = Model(judge=_judge_database_false)
    code, out = _run(["humanize", str(doc), "--root", str(root)], model, monkeypatch, tmp_path)
    assert code == 0 and model.calls == 2
    assert "rewritten in" in out and "checked in" in out and "two model calls" in out
    assert "1 sentence put back" in out
    assert 'put back (FALSE) "The report keeps only the findings that block, and it writes' in out
    assert "scripts/push.py:2" in out


def test_no_check_says_so_and_makes_one_call(tmp_path, monkeypatch):
    root, doc = _repo(tmp_path)
    model = Model(judge=_judge_database_false)
    code, out = _run(["humanize", str(doc), "--root", str(root), "--no-check"], model, monkeypatch, tmp_path)
    assert code == 0 and model.calls == 1 and "not checked against the code" in out and "one model call" in out


def test_a_checker_that_did_not_check_is_a_refusal(tmp_path, monkeypatch):
    root, doc = _repo(tmp_path)
    model = Model(judge=lambda item: None if "single request" in item["new"] else _judge_true(item))
    code, out = _run(["humanize", str(doc), "--root", str(root)], model, monkeypatch, tmp_path)
    assert code == 1 and "refused after" in out and "the checker did not check" in out
    assert doc.read_text() == OLD


def test_a_dry_run_shows_the_diff_after_putting_back_and_writes_nothing(tmp_path, monkeypatch):
    root, doc = _repo(tmp_path)
    model = Model(judge=_judge_database_false)
    code, out = _run(["humanize", str(doc), "--root", str(root), "--dry-run"], model, monkeypatch, tmp_path)
    assert code == 0 and doc.read_text() == OLD
    assert "dry run" in out and "put back (FALSE)" in out
    assert "+The push script sends the rows to the archive, all of them in a single request." in out
    assert "writes each one" not in "".join(l for l in out.splitlines(True) if l.startswith("+"))


def test_no_sources_still_checks_against_the_code(tmp_path, monkeypatch):
    root, doc = _repo(tmp_path)
    model = Model(judge=_judge_database_false)
    code, out = _run(["humanize", str(doc), "--root", str(root), "--no-sources"], model, monkeypatch, tmp_path)
    assert code == 0 and model.calls == 2 and "checked in" in out

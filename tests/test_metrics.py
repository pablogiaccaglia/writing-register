"""The meter: what the automatic rewrite had to change, kept after the turn.

2026-09-16. Nothing survived a turn before this: the snapshot taken
before an edit is deleted inside the next hook, the recorded sentences are
deleted when the rewrite settles, and the note is deleted when Claude receives
it. Across 5,847 transcripts only 33 notes carrying an old and a new passage
existed, nearly all from the feature's own tests, so the one number that says
whether steering is working, the share of Claude's prose the rewriter still
changes, could not be computed. One line per rewrite fixes that.
"""
import json

import pytest

from writing_register import metrics


def test_a_record_is_one_json_line_under_the_month_it_happened(tmp_path, monkeypatch):
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "state"))
    metrics.append({"event": "passages", "words_sent": 120, "words_changed": 18})
    files = list((tmp_path / "state").glob("metrics/*.jsonl"))
    assert len(files) == 1 and files[0].name.endswith(".jsonl")
    row = json.loads(files[0].read_text().splitlines()[0])
    assert row["event"] == "passages" and row["words_changed"] == 18 and row["at"]


def test_the_meter_never_breaks_a_rewrite(tmp_path, monkeypatch):
    # A directory that cannot be created, which is what a read-only home looks like.
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "file" / "state"))
    (tmp_path / "file").write_text("not a directory\n")
    metrics.append({"event": "passages"})   # must not raise


def test_the_report_totals_what_the_rewriter_changed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "state"))
    for changed in (10, 30):
        metrics.append({"event": "passages", "words_sent": 100, "words_changed": changed,
                        "passages": 2, "refused": ""})
    metrics.append({"event": "message", "kind": "commit", "words_sent": 40,
                    "words_changed": 12, "refused": ""})
    text = metrics.report()
    assert "2 passage rewrites" in text and "20%" in text, text


def test_the_configuration_can_turn_the_meter_off(tmp_path, monkeypatch):
    from writing_register import config as c
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\nmetrics = false\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    assert c.load_config().metrics is False
    cfg.write_text('voice = "none"\nmetrics = "yes"\n')
    with pytest.raises(c.ConfigError):
        c.load_config()


def test_the_command_prints_the_report(tmp_path, monkeypatch):
    import io

    from writing_register.cli import main
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "state"))
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    metrics.append({"event": "passages", "words_sent": 50, "words_changed": 5, "passages": 1})
    out = io.StringIO()
    assert main(["report"], out=out) == 0
    assert "passage rewrites" in out.getvalue(), out.getvalue()

"""What the command tells the user about a check that was less than it seems.

Audit 2026-09-16. Three ways a run could be quietly weaker than the line on
screen implied: the document's sources were dropped because git could not be
asked, the checker read only part of the repository because the
export hit a cap, and a copy kept beside the file was reported as
if a checker had looked at it when the string checks had refused it before
the checker ever ran.
"""
import subprocess

from test_humanize_check import Model, _repo

from writing_register import humanize as h
from writing_register import spawn as sp
from writing_register.humanize import humanize


def test_the_line_says_when_the_sources_were_not_sent(tmp_path, monkeypatch):
    root, doc = _repo(tmp_path)
    real = subprocess.run

    def no_check_ignore(cmd, *a, **kw):
        if "check-ignore" in cmd:
            raise OSError("git is not installed")
        return real(cmd, *a, **kw)

    doc.write_text(doc.read_text() + "\nThe writer is `scripts/push.py`.\n")
    monkeypatch.setattr(h.subprocess, "run", no_check_ignore)
    r = humanize(doc, spawn=Model(), root=root, check=False, write=False)
    assert any("ignores" in n for n in r.notes), r.notes


def test_the_line_says_when_the_checker_read_only_part_of_the_repository(tmp_path, monkeypatch):
    root, doc = _repo(tmp_path)
    monkeypatch.setattr(sp, "EXPORT_MAX_FILES", 0)
    r = humanize(doc, spawn=Model(), root=root, write=False)
    assert any("tracks more than" in n or "read only" in n for n in r.notes), r.notes


def test_a_copy_kept_after_the_string_checks_refused_is_not_reported_as_checked(tmp_path):
    root, doc = _repo(tmp_path)
    # A rewrite that invents a number never reaches the checker.
    bad = doc.read_text().replace("The report keeps only the findings that block.",
                                  "The report keeps only the 42 findings that block.")
    r = humanize(doc, spawn=Model(rewrite=bad), root=root)
    assert r.refused and r.kept is not None, r.refused
    assert r.kept_unchecked, "a copy no checker ever saw must say so"


def test_the_command_prints_the_notes(tmp_path, monkeypatch):
    import io

    from writing_register.cli import main
    root, doc = _repo(tmp_path)
    monkeypatch.setattr(sp, "EXPORT_MAX_FILES", 0)
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    out = io.StringIO()
    main(["humanize", "--dry-run", "--root", str(root), str(doc)], out=out, spawn=Model())
    assert "read only" in out.getvalue(), out.getvalue()

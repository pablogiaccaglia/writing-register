"""Two faults the audit of 2026-09-16 found in what reaches the model.

First: the filter that keeps git-ignored files out of the prompt failed
open. When `git check-ignore` could not run, every candidate was sent, while
README and USAGE both say a file git ignores is never sent, and the reason
they give is that ignored files hold runtime data, such as real recordings or
a record of people.

Second: every export was made inside the directory the checker itself ran
in, so a second `wr humanize` running in another repository put its export
where the first checker's process could read it.
"""
import subprocess

import pytest

from writing_register import humanize as h
from writing_register.spawn import Answer, Spawn


def _doc(tmp_path):
    root = tmp_path
    (root / "docs").mkdir()
    (root / "out").mkdir()
    (root / "out" / "log.md").write_text("Real station text.\n")
    (root / "docs" / "SETUP.md").write_text("# Setup\n\nHow to set it up.\n")
    doc = root / "docs" / "DOC.md"
    doc.write_text("# Doc\n\nSee [setup](SETUP.md) and `out/log.md`.\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("out/\n")
    return root, doc


def test_an_ignored_file_is_never_sent_even_when_git_cannot_be_asked(tmp_path, monkeypatch):
    root, doc = _doc(tmp_path)
    assert set(h.gather_sources(doc, root)) == {"docs/SETUP.md"}, "the ignored file must be dropped"

    def no_git(*a, **kw):
        raise OSError("git is not installed")

    monkeypatch.setattr(subprocess, "run", no_git)
    notes = []
    assert h.gather_sources(doc, root, notes=notes) == {}
    assert notes and "ignore" in notes[0]


def test_outside_a_repository_the_sources_are_still_sent(tmp_path):
    root = tmp_path / "plain"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "SETUP.md").write_text("# Setup\n\nHow to set it up.\n")
    doc = root / "docs" / "DOC.md"
    doc.write_text("# Doc\n\nSee [setup](SETUP.md).\n")
    assert set(h.gather_sources(doc, root)) == {"docs/SETUP.md"}


def test_the_checker_runs_in_its_own_empty_directory(tmp_path):
    seen = {}

    def runner(cmd, **kw):
        cwd = kw["cwd"]
        seen["cwd"] = cwd
        seen["contents"] = sorted(p.name for p in __import__("pathlib").Path(cwd).iterdir())
        return subprocess.CompletedProcess(cmd, 0, stdout="text", stderr="")

    answer = Spawn(runner=runner).run("prompt")
    assert isinstance(answer, Answer)
    assert seen["contents"] == [], f"the child's directory held {seen['contents']}"
    assert not __import__("pathlib").Path(seen["cwd"]).exists(), "it is removed after the call"

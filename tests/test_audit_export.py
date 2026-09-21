"""What the checker's copy of the repository may and may not hold.

Audit 2026-09-16. The copy is made by `export_tracked`: `git archive HEAD`
for the tracked files, then the working tree's edited and new files, then the
document itself. Three faults, each proved here before it was fixed:

1. `git archive` extracts every tracked file, so a repository that commits its
   `.env` or `credentials.json` handed them to the checker, while README and
   USAGE both said files named like secrets are left out;
2. a path the archive holds as a symlink and the working tree holds as a
   regular file was copied through that symlink, writing outside the export,
   because symlinks were stripped only at the end;
3. over the file or byte cap neither archive ran, so the checker judged the
   document against an empty directory and nobody was told.
"""
import subprocess

import pytest

from writing_register import spawn
from writing_register.spawn import export_tracked, remove_export


def _repo(tmp_path, files):
    root = tmp_path / "repo"
    root.mkdir()
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first"],
                   cwd=root, check=True)
    return root


def test_a_committed_secret_file_never_reaches_the_checker(tmp_path):
    root = _repo(tmp_path, {".env": "SECRET_KEY=ghp_real\n", "credentials.json": '{"token": "sk-live"}\n',
                            "keys/server.pem": "-----BEGIN PRIVATE KEY-----\n",
                            "code.py": "def f():\n    return 1\n", "DOC.md": "# Doc\n\nA line.\n"})
    export = export_tracked(root, overlay=[root / "DOC.md"])
    try:
        names = sorted(p.name for p in export.rglob("*") if p.is_file())
        assert names == ["DOC.md", "code.py"], names
    finally:
        remove_export(export)


def test_a_path_the_working_tree_turned_into_a_file_is_not_written_through_its_symlink(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched\n")
    root = _repo(tmp_path, {"DOC.md": "# Doc\n\nA line.\n", "code.py": "x = 1\n"})
    (root / "notes.md").symlink_to(outside)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "link"],
                   cwd=root, check=True)
    (root / "notes.md").unlink()
    (root / "notes.md").write_text("a real file now\n")
    export = export_tracked(root, overlay=[root / "DOC.md"])
    try:
        assert outside.read_text() == "untouched\n"
        assert not (export / "notes.md").is_symlink()
        assert (export / "notes.md").read_text() == "a real file now\n"
    finally:
        remove_export(export)


@pytest.mark.parametrize("cap", ["EXPORT_MAX_FILES", "EXPORT_MAX_BYTES"])
def test_over_a_cap_the_export_still_holds_the_document_s_directory_and_says_so(tmp_path, monkeypatch, cap):
    root = _repo(tmp_path, {"docs/DOC.md": "# Doc\n\nA line.\n", "docs/helper.py": "def h():\n    return 2\n",
                            "far/away.py": "def a():\n    return 3\n"})
    monkeypatch.setattr(spawn, cap, 0)
    notes = []
    export = export_tracked(root, overlay=[root / "docs" / "DOC.md"], notes=notes)
    try:
        assert (export / "docs" / "helper.py").exists(), sorted(p.name for p in export.rglob("*"))
        assert not (export / "far" / "away.py").exists()
        assert notes and "docs" in notes[0]
    finally:
        remove_export(export)

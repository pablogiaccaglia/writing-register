"""A rewrite never overwrites edits made while the model was running.

When wr runs automatically at the end of a turn (since 2026-09-15), the model
call takes minutes, and Claude may edit the same file again in the next turn.
Writing the rewrite over that edit would lose it."""
from writing_register.humanize import humanize
from writing_register.spawn import Answer

DOC = "# Capture\n\nIt is not a recorder, but a note taker.\n"
CLEAN = "# Capture\n\nIt takes notes.\n"


class EditingSpawn:
    """A model call during which someone else edits the file."""

    def __init__(self, path, reply):
        self.path, self.reply = path, reply

    def run(self, prompt, **kw):
        self.path.write_text(DOC + "\nA line added during the rewrite.\n", encoding="utf-8")
        return Answer(stdout=self.reply, command=("claude",), duration_seconds=0.1)


def test_a_file_edited_during_the_rewrite_is_not_overwritten(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text(DOC, encoding="utf-8")
    r = humanize(p, spawn=EditingSpawn(p, CLEAN))
    assert not r.written and "changed" in r.refused
    assert "A line added during the rewrite." in p.read_text(encoding="utf-8")


# Audit follow-up, 2026-09-15: the guard used to read the file and then write
# it, so an edit landing between the two was overwritten. Claude Code's Write
# and Edit tools replace a file by renaming a new one into place (measured: the
# inode changes), which is exactly the edit that window lost. The rewrite is now
# swapped into place atomically and what came out is checked.

import os
import stat

from writing_register import humanize as hz


class Plain:
    def __init__(self, reply):
        self.reply = reply

    def run(self, prompt, **kw):
        return Answer(stdout=self.reply, command=("claude",), duration_seconds=0.1)


def _no_temp_files(folder):
    return [p.name for p in folder.iterdir() if p.name.endswith(".tmp")] == []


def test_an_edit_renamed_into_place_just_before_the_write_is_kept(tmp_path, monkeypatch):
    p = tmp_path / "doc.md"
    p.write_text(DOC, encoding="utf-8")
    real_swap = hz._swap

    def edit_then_swap(a, b):
        edited = tmp_path / "claude-edit.md"
        edited.write_text(DOC + "\nClaude's edit.\n", encoding="utf-8")
        os.replace(edited, p)
        return real_swap(a, b)

    monkeypatch.setattr(hz, "_swap", edit_then_swap)
    r = humanize(p, spawn=Plain(CLEAN))
    assert not r.written and "changed" in r.refused
    assert "Claude's edit." in p.read_text(encoding="utf-8")
    assert r.kept and r.kept.read_text(encoding="utf-8") == CLEAN
    assert _no_temp_files(tmp_path)


def test_an_in_place_write_through_a_file_opened_before_the_swap_is_kept(tmp_path, monkeypatch):
    p = tmp_path / "doc.md"
    p.write_text(DOC, encoding="utf-8")
    real_swap = hz._swap

    def swap_during_a_write(a, b):
        with open(p, "r+", encoding="utf-8") as editor:
            done = real_swap(a, b)
            editor.seek(0, os.SEEK_END)
            editor.write("\nWritten in place.\n")
        return done

    monkeypatch.setattr(hz, "_swap", swap_during_a_write)
    r = humanize(p, spawn=Plain(CLEAN))
    assert not r.written and "changed" in r.refused
    assert "Written in place." in p.read_text(encoding="utf-8")
    assert _no_temp_files(tmp_path)


def test_without_an_atomic_swap_the_rewrite_is_still_written(tmp_path, monkeypatch):
    p = tmp_path / "doc.md"
    p.write_text(DOC, encoding="utf-8")
    monkeypatch.setattr(hz, "_swap", lambda a, b: False)
    r = humanize(p, spawn=Plain(CLEAN))
    assert r.written and p.read_text(encoding="utf-8") == CLEAN
    assert _no_temp_files(tmp_path)


def test_a_written_rewrite_keeps_the_file_permissions_and_leaves_no_temp_file(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text(DOC, encoding="utf-8")
    os.chmod(p, 0o640)
    r = humanize(p, spawn=Plain(CLEAN))
    assert r.written and p.read_text(encoding="utf-8") == CLEAN
    assert stat.S_IMODE(p.stat().st_mode) == 0o640
    assert _no_temp_files(tmp_path)


def test_a_symlinked_document_is_rewritten_through_the_link(tmp_path):
    real = tmp_path / "real.md"
    real.write_text(DOC, encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    r = humanize(link, spawn=Plain(CLEAN))
    assert r.written and link.is_symlink() and real.read_text(encoding="utf-8") == CLEAN


def test_the_swap_exchanges_two_files(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("A", encoding="utf-8")
    b.write_text("B", encoding="utf-8")
    assert hz._swap(a, b) is True
    assert a.read_text(encoding="utf-8") == "B" and b.read_text(encoding="utf-8") == "A"

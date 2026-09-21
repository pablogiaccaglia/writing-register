"""`wr voice build`: the whole voice in one readable file (2026-09-21).

A directory is the source of a voice, and people still want to read it in one
place. The build writes a file beside the directory holding the core, the core
marker, and everything people keep. That file is marked as generated, and it
is itself a valid single-file voice whose core is exactly the directory's, so a
configuration that points at the file by path, or an older wr, gets the same
voice. A stale file is an error in `wr voice check`, so nobody edits the wrong
copy.
"""
import io

from writing_register import voice as v
from writing_register import voice_tools as vt
from writing_register.config import CORE_MARKER, voice_core


def _voice(tmp_path):
    root = tmp_path / "voice" / "tester"
    files = {
        "voice.toml": 'format = 1\nname = "Tester"\norder = ["register.md", "kinds.md", "kinds/replies.md"]\n',
        "rules/register.md": "# Register\n\n- {#register.no-dashes} No em dashes.\n- {#register.bold} Bold only a label.\n",
        "rules/kinds.md": "# By kind of text\n\n{#kinds.precedence} A kind's own rule wins.\n",
        "rules/kinds/replies.md": "# Replies\n\n{#replies.lead} Lead with the answer.\n",
        "about.md": "# About this voice\n\nBuilt from corrections. See [the readme](../../README.md).\n",
        "evidence/register.md": "## Dashes in replies\nRules: register.no-dashes\n\nA quote, 2026-09-21.\n",
        "decisions.md": ("## Bold or not?\nRules: register.bold\nDecided: 2026-09-16\nConfirmed: 2026-09-21\n\n"
                         "Only a label that opens a list item.\n"),
        "maintaining.md": "# How to change this voice\n\nTurn a correction into a rule.\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / "README.md").write_text("# Readme\n")
    return root


def test_the_view_is_a_single_file_voice_with_the_same_core(tmp_path):
    root = _voice(tmp_path)
    view = vt.build(root)
    assert view == root.with_suffix(".md")
    text = view.read_text()
    assert text.startswith("<!-- wr:generated")
    assert text.count(CORE_MARKER) == 1
    assert voice_core(text) == v.read_core(root)


def test_the_view_carries_everything_people_keep(tmp_path):
    root = _voice(tmp_path)
    text = vt.build(root).read_text()
    rest = text.partition(CORE_MARKER)[2]
    assert "Built from corrections." in rest
    assert "A quote, 2026-09-21." in rest
    assert 'Supports: register.no-dashes ("No em dashes.")' in rest
    assert "| Bold or not? | Only a label that opens a list item. | 2026-09-16, confirmed 2026-09-21 |" in rest
    assert "Turn a correction into a rule." in rest


def test_links_in_the_view_point_where_the_originals_did(tmp_path):
    root = _voice(tmp_path)
    text = vt.build(root).read_text()
    assert "[the readme](../README.md)" in text
    assert (root.parent / "../README.md").resolve() == (tmp_path / "README.md").resolve()


def test_a_stale_view_is_an_error_and_a_fresh_one_is_not(tmp_path):
    root = _voice(tmp_path)
    vt.build(root)
    assert not [f for f in vt.check(root) if f.level == "error"]
    (root / "rules" / "register.md").write_text("# Register\n\n- {#register.no-dashes} No dashes at all.\n"
                                                 "- {#register.bold} Bold only a label.\n")
    assert any("out of date" in f.message for f in vt.check(root))


def test_the_command_builds_and_checks_freshness(tmp_path):
    from writing_register.cli import main
    root = _voice(tmp_path)
    out = io.StringIO()
    assert main(["voice", "build", str(root), "--check"], out=out) == 1
    assert main(["voice", "build", str(root)], out=io.StringIO()) == 0
    assert main(["voice", "build", str(root), "--check"], out=io.StringIO()) == 0

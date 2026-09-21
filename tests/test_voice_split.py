"""`wr voice split`: a single-file voice becomes a directory, losslessly
(2026-09-21).

The split is mechanical: one rule file per `##` section, one rule per
paragraph or list item, a bold-labelled paragraph under a section becomes a
file of its own inside that section's folder, the text before the first
section becomes the untitled opening, and everything below the core marker
goes to about.md. The core the model receives afterwards is byte for byte the
core it received before, which is what lets a real voice move without anyone
having to trust the move.
"""
from writing_register import voice as v
from writing_register import voice_tools as vt
from writing_register.config import voice_core

SAMPLE = """# Voice: Sample

This voice describes how the sample reader wants to read.

## The reader

Write for a colleague who was not in the room.

They may read it weeks later.

## Register

These rules apply on top of the skill:

- No em dashes.
- No slogans: no teaser lead-ins.

## By kind of text

**Documentation.** Context first, then depth.

**Replies to the reader.** Lead with the answer.

**Commit messages:** say what changed.

## When rewriting

Keep every fact.

<!-- wr:end-of-core -->

# About this voice

It was built from corrections, see [the guide](../docs/GUIDE.md).

## Decisions

| Question | Decision | When |
|---|---|---|
| Dashes? | None | 2026-09-14 |
"""


def _file(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "GUIDE.md").write_text("# Guide\n")
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    f = voice_dir / "sample.md"
    f.write_text(SAMPLE)
    return f


def test_the_split_core_is_the_file_s_core_byte_for_byte(tmp_path):
    f = _file(tmp_path)
    root = vt.split(f, tmp_path / "voice" / "sample-dir")
    assert v.read_core(root) == voice_core(SAMPLE)


def test_the_split_keeps_the_structure_as_files(tmp_path):
    root = vt.split(_file(tmp_path), tmp_path / "voice" / "sample-dir")
    order = v.load(root).order
    assert order[0] == "scope.md"
    assert "by-kind-of/documentation.md" in order and "by-kind-of/commit-messages.md" in order
    assert vt.check(root) == [] or all(f.level != "error" for f in vt.check(root))


def test_names_can_be_given_to_sections_and_kinds(tmp_path):
    root = vt.split(_file(tmp_path), tmp_path / "voice" / "sample-dir",
                    names={"By kind of text": "kinds", "Documentation": "docs", "The reader": "reader"})
    order = v.load(root).order
    assert "kinds.md" in order and "kinds/docs.md" in order and "reader.md" in order
    assert v.read_core(root) == voice_core(SAMPLE)


def test_everything_below_the_marker_survives_and_its_links_still_resolve(tmp_path):
    root = vt.split(_file(tmp_path), tmp_path / "voice" / "sample-dir")
    about = (root / "about.md").read_text()
    assert "[the guide](../../docs/GUIDE.md)" in about
    view = vt.build(root).read_text()
    appendix = SAMPLE.partition("<!-- wr:end-of-core -->")[2]
    for line in appendix.splitlines():
        if line.strip():
            assert line.replace("../docs/GUIDE.md", "../docs/GUIDE.md") in view, line


def test_the_command_splits_a_file_into_a_directory(tmp_path):
    import io

    from writing_register.cli import main
    f = _file(tmp_path)
    out = io.StringIO()
    assert main(["voice", "split", str(f), "--into", str(tmp_path / "voice" / "cmd")], out=out) == 0
    assert v.read_core(tmp_path / "voice" / "cmd") == voice_core(SAMPLE)
    assert main(["voice", "split", str(f), "--into", str(tmp_path / "voice" / "cmd")], out=io.StringIO()) == 2

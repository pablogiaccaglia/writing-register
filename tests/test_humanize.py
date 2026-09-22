"""One pass, the humanizer's way, with only the guarantees that cost nothing.

These tests pin the simple path: one call, the file rewritten, and a refusal
only where a check can be made without a model.
"""
from pathlib import Path

from writing_register.humanize import humanize
from writing_register.spawn import Answer

DOC = """# Capture

The logger polls the station. It is not a recorder, but a note taker. It uses
three slots — each owning one Chrome profile — and 900MB per capture.

```bash
python -m src.station_poll --slots 3
```

See [the setup guide](SETUP.md) and run `start_in_thread()`.
"""


class Spawn:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def run(self, prompt, **kw):
        self.calls += 1
        self.prompt = prompt
        return Answer(stdout=self.reply, command=("claude",), duration_seconds=0.1)


def _file(tmp_path, text=DOC):
    p = tmp_path / "doc.md"
    p.write_text(text, encoding="utf-8")
    return p


CLEAN = DOC.replace("It is not a recorder, but a note taker.", "It takes notes.").replace(
    "three slots — each owning one Chrome profile — and 900MB",
    "three slots, each owning one Chrome profile, and 900MB")


def test_one_call_rewrites_the_file(tmp_path):
    p, s = _file(tmp_path), Spawn(CLEAN)
    r = humanize(p, spawn=s)
    assert s.calls == 1
    assert r.written
    assert p.read_text(encoding="utf-8") == CLEAN


def test_the_prompt_carries_the_catalogue_and_the_document(tmp_path):
    p, s = _file(tmp_path), Spawn(CLEAN)
    humanize(p, spawn=s)
    assert "Not X but Y" in s.prompt or "not-X-but-Y" in s.prompt.lower()
    assert "When not to act" in s.prompt
    assert "It is not a recorder, but a note taker." in s.prompt


def test_a_rewrite_that_invents_a_number_is_refused(tmp_path):
    p = _file(tmp_path)
    r = humanize(p, spawn=Spawn(CLEAN.replace("900MB", "1200MB")))
    assert not r.written and "1200" in r.refused
    assert p.read_text(encoding="utf-8") == DOC


def test_a_rewrite_that_changes_a_code_block_is_refused(tmp_path):
    p = _file(tmp_path)
    r = humanize(p, spawn=Spawn(CLEAN.replace("--slots 3", "--slots=3")))
    assert not r.written and "code" in r.refused
    assert p.read_text(encoding="utf-8") == DOC


def test_a_rewrite_that_changes_inline_code_or_a_link_is_refused(tmp_path):
    p = _file(tmp_path)
    for bad in (CLEAN.replace("start_in_thread()", "start()"),
                CLEAN.replace("(SETUP.md)", "(setup.md)")):
        r = humanize(p, spawn=Spawn(bad))
        assert not r.written
    assert p.read_text(encoding="utf-8") == DOC


def test_a_rewrite_that_guts_the_document_is_refused(tmp_path):
    p = _file(tmp_path)
    r = humanize(p, spawn=Spawn("# Capture\n\nIt takes notes.\n"))
    assert not r.written
    assert p.read_text(encoding="utf-8") == DOC


def test_a_dry_run_writes_nothing(tmp_path):
    p = _file(tmp_path)
    r = humanize(p, spawn=Spawn(CLEAN), write=False)
    assert not r.written and r.text == CLEAN
    assert p.read_text(encoding="utf-8") == DOC


def test_mentioning_an_identifier_the_document_already_has_is_allowed(tmp_path):
    """Seen live on 2026-09-14: USAGE.md's rewrite added `wr loop` and
    `wr apply` as inline references in prose, both commands the document already
    documents, and was refused as "inline code changed". 115 seconds of rewrite
    thrown away for a mention. Losing or altering a span is a code change;
    naming something the document already names is prose."""
    p = _file(tmp_path)
    reuse = CLEAN.replace("It takes notes.", "It takes notes; see `start_in_thread()`.")
    r = humanize(p, spawn=Spawn(reuse))
    assert r.written, r.refused


def test_inventing_an_identifier_is_still_refused(tmp_path):
    p = _file(tmp_path)
    r = humanize(p, spawn=Spawn(CLEAN.replace("It takes notes.", "It takes notes via `record_all()`.")))
    assert not r.written and "record_all" in r.refused


def test_a_refused_rewrite_is_kept_beside_the_file(tmp_path):
    """A refusal used to discard the whole rewrite. The work took minutes and
    usually differs from an acceptable one in a single place, so it is saved
    where the person deciding can read it."""
    p = _file(tmp_path)
    bad = CLEAN.replace("900MB", "1200MB")
    r = humanize(p, spawn=Spawn(bad))
    assert not r.written
    kept = tmp_path / "doc.refused.md"
    assert r.kept == kept and kept.read_text(encoding="utf-8") == bad
    assert p.read_text(encoding="utf-8") == DOC


def test_dropping_backticks_from_a_name_that_stays_is_allowed(tmp_path):
    """Seen on one repository's docs on 2026-09-14: two architecture
    documents were refused because the rewrite wrote one of two mentions of a
    function name and a source file path without backticks. The
    name is still in the text; only its formatting changed. The mirror of
    mentioning an existing name in backticks, which is already allowed."""
    p = _file(tmp_path, DOC + "\nThe entry point is `start_in_thread()` again.\n")
    base = p.read_text(encoding="utf-8")
    clean = base.replace("It is not a recorder, but a note taker.", "It takes notes.").replace(
        "three slots — each owning one Chrome profile — and 900MB",
        "three slots, each owning one Chrome profile, and 900MB").replace(
        "The entry point is `start_in_thread()` again.", "The entry point is start_in_thread() again.")
    r = humanize(p, spawn=Spawn(clean))
    assert r.written, r.refused


def test_a_name_that_disappears_entirely_is_still_refused(tmp_path):
    p = _file(tmp_path)
    r = humanize(p, spawn=Spawn(CLEAN.replace("run `start_in_thread()`", "start it")))
    assert not r.written and "start_in_thread" in r.refused


def test_the_prompt_says_a_computed_number_is_a_new_number():
    """Three of five refusals on one repository's docs on 2026-09-14 were the model
    doing arithmetic on the document's own numbers: "one fewer than the 416 that
    146 and 270 add up to", "1,622 tests (1,606 + 16)", "900 second (15 minute)".
    Each was correct, and each is a figure the document never stated. The prompt
    said not to add a number and the model did not count arithmetic as adding."""
    from writing_register.humanize import build_prompt
    p = build_prompt("Some prose with 146 and 270.\n", "catalogue").lower()
    assert "sum" in p and "conversion" in p


def test_adding_a_link_to_a_heading_in_the_document_is_allowed(tmp_path):
    """Seen on one repository's docs on 2026-09-14: a design document was
    refused because the rewrite added `[How it composes with the pipeline](#how-it-
    composes-with-the-pipeline)` to a sentence, naming the section it pointed at. No link
    was lost, and a check comparing link sets exactly called it a changed link
    target."""
    doc = DOC + "\n## Where it runs\n\nOn the box.\n"
    p = _file(tmp_path, doc)
    clean = doc.replace("It is not a recorder, but a note taker.", "It takes notes.").replace(
        "three slots — each owning one Chrome profile — and 900MB",
        "three slots, each owning one Chrome profile, and 900MB").replace(
        "See [the setup guide](SETUP.md)", "See [Where it runs](#where-it-runs) and [the setup guide](SETUP.md)")
    r = humanize(p, spawn=Spawn(clean))
    assert r.written, r.refused


def test_adding_a_link_to_a_file_the_document_never_named_is_refused(tmp_path):
    p = _file(tmp_path)
    bad = CLEAN.replace("See [the setup guide](SETUP.md)",
                        "See [the setup guide](SETUP.md) and [ops](OPS.md)")
    r = humanize(p, spawn=Spawn(bad))
    assert not r.written and "OPS.md" in r.refused


def test_a_code_span_wrapped_across_a_line_is_one_span():
    """Seen on one repository's EXTENSION.md on 2026-09-14: "(`command -v" ended one
    line and "claude`)" began the next, and the rewrite put the span on one line.
    A pattern that forbade a newline inside a span paired every later backtick
    differently in the two versions and reported "`), logged in, OR set `" as
    lost code. The original must be wrapped and the rewrite unwrapped, or both
    mispair identically and the test proves nothing."""
    from writing_register.humanize import check
    old = ("Install it from a shell where (`command -v\n"
           "claude`) works, logged in, or set `KEY` / `MODE=api` in `.env`.\n") * 3
    new = ("Install it from a shell where (`command -v claude`) works, logged in, "
           "or set `KEY` / `MODE=api` in `.env`.\n") * 3
    assert check(old, new) == "", check(old, new)


def test_a_double_backtick_span_holding_a_backtick_is_one_span():
    from writing_register.humanize import check
    old = "Quote a backtick as ``a ` b`` inside prose, then use `x`.\n" * 3
    new = old.replace("Quote a backtick", "Write a backtick")
    assert check(old, new) == "", check(old, new)


def test_a_code_span_does_not_pair_across_a_blank_line():
    from writing_register.humanize import _inline_spans
    assert _inline_spans("one ` stray\n\nand `real` here") == ["real"]


# Sources. The first pass on 2026-09-14 read "very good", but it "still misses
# details and background explanations". The prompt forbade adding any fact
# the document did not state, so the background could not arrive: it lives in the
# code and the docs the document points at. These give the call those files and
# widen the checks to them, and nothing further.

def _repo(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "docs" / "SETUP.md").write_text(
        "# Setup\n\nThe box keeps 7 days of spool in `SPOOL_DIR`.\n", encoding="utf-8")
    (tmp_path / "src" / "bot.py").write_text(
        "def start_in_thread():\n    '''Joins with a warm browser.'''\n", encoding="utf-8")
    doc = tmp_path / "docs" / "CAPTURE.md"
    doc.write_text(DOC.replace("run `start_in_thread()`",
                               "run `start_in_thread()` from `src/bot.py`")
                   + "\nMore at [Meet](https://meet.google.com) and [gone](MISSING.md).\n",
                   encoding="utf-8")
    return doc


def _tidy(text):
    return text.replace("three slots — each owning one Chrome profile — and",
                        "three slots, each owning one Chrome profile, and")


def test_sources_are_the_local_files_the_document_links_or_names(tmp_path):
    from writing_register.humanize import gather_sources
    doc = _repo(tmp_path)
    assert set(gather_sources(doc, tmp_path)) == {"docs/SETUP.md", "src/bot.py"}


def test_with_sources_the_prompt_allows_background_taken_from_them():
    from writing_register.humanize import build_prompt
    p = build_prompt("doc\n", "catalogue",
                     sources={"docs/SETUP.md": "The box keeps 7 days of spool."})
    assert "The box keeps 7 days of spool." in p and "docs/SETUP.md" in p
    assert "background" in p.lower() and "did not build" in p.lower()
    assert "not already in the document" not in p


def test_code_the_rewrite_adds_must_be_copied_exactly_from_the_sources():
    """2026-09-21: on the invented sample corpus a rewrite added `out/reports/`
    (the source has "out/reports"), `stationlog/config.py` (inferred from an
    import) and a log message paraphrased with a number filled in; the check
    refused the whole rewrite. The prompt now says so before the model writes."""
    from writing_register.humanize import build_prompt
    prompt = build_prompt("# Doc\n", "skill", sources={"a.py": "x = 1\n"})
    assert "character for character" in prompt and "plain words" in prompt


def test_a_number_written_differently_but_equal_is_not_invented():
    """2026-09-21: a correct sentence about readings "between -60 and 60" was
    refused because the source says `-60.0, 60.0`."""
    from writing_register.humanize import _invented_numbers
    assert _invented_numbers("readings between -60 and 60 degrees", "VALID = -60.0, 60.0") == []
    assert _invented_numbers("1000 rows", "a batch of 1,000 rows") == []
    assert _invented_numbers("version 1.2", "version 1.2.0") == ["1.2"], "a version is not a number"
    assert _invented_numbers("61 degrees", "60.0") == ["61"]


def test_each_source_says_how_to_link_to_it_from_the_document(tmp_path):
    """2026-09-21: the prompt allowed a link to a source "by its path relative
    to the document" but labelled sources from the repository root, so from
    docs/ the model linked `stationlog/push.py`, which the check refused."""
    import subprocess
    (tmp_path / "docs").mkdir()
    (tmp_path / "stationlog").mkdir()
    (tmp_path / "stationlog" / "push.py").write_text("BATCH = 50\n")
    doc = tmp_path / "docs" / "ARCH.md"
    doc.write_text("# Arch\n\n`stationlog/push.py` posts the rows.\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    s = Spawn(doc.read_text())
    humanize(doc, spawn=s, root=tmp_path, check=False, write=False)
    assert "# Source: stationlog/push.py" in s.prompt
    assert "../stationlog/push.py" in s.prompt


def test_background_from_a_source_passes_the_checks(tmp_path):
    doc = _repo(tmp_path)
    old = doc.read_text(encoding="utf-8")
    new = _tidy(old).replace(
        "It is not a recorder, but a note taker.",
        "It takes notes. The box keeps 7 days of spool in `SPOOL_DIR`, as "
        "[the setup guide](SETUP.md) explains.")
    # Only the string checks are under test here; the checker has its own tests.
    r = humanize(doc, spawn=Spawn(new), root=tmp_path, check=False)
    assert r.written, r.refused


def test_a_fact_in_neither_the_document_nor_its_sources_is_still_refused(tmp_path):
    doc = _repo(tmp_path)
    old = _tidy(doc.read_text(encoding="utf-8"))
    for bad, word in ((old.replace("note taker.", "note taker kept 30 days."), "30"),
                      (old.replace("note taker.", "note taker; see `PURGE_DIR`."),
                       "PURGE_DIR")):
        r = humanize(doc, spawn=Spawn(bad), root=tmp_path)
        assert not r.written and word in r.refused


def test_a_refused_dry_run_writes_nothing_either(tmp_path):
    """Audit, 2026-09-14: a dry run left `<name>.refused.md` behind when the
    rewrite was refused, while USAGE said a dry run writes nothing."""
    p = _file(tmp_path)
    r = humanize(p, spawn=Spawn(CLEAN.replace("900MB", "1200MB")), write=False)
    assert r.refused and r.kept is None and "1200MB" in r.text
    assert sorted(f.name for f in tmp_path.iterdir()) == ["doc.md"]


def test_a_file_git_ignores_is_never_a_source(tmp_path):
    """Audit, 2026-09-14: in one repository's checkout its docs name two
    files under out/ that hold real recordings and what the tool knows about
    people. Both are gitignored and both would
    have gone into the prompt, after which the checks accept any name in them."""
    import subprocess
    from writing_register.humanize import gather_sources
    doc = _repo(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("out/\n", encoding="utf-8")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "station_log.txt").write_text("Speaker: private\n", encoding="utf-8")
    doc.write_text(doc.read_text(encoding="utf-8")
                   + "\nThe capture is `out/station_log.txt`.\n", encoding="utf-8")
    s = gather_sources(doc, tmp_path)
    assert "out/station_log.txt" not in s
    assert "src/bot.py" in s and "docs/SETUP.md" in s


def test_env_files_and_non_text_files_are_never_sources(tmp_path):
    from writing_register.humanize import gather_sources
    doc = _repo(tmp_path)
    (tmp_path / ".env").write_text("ARCHIVE_TOKEN=secret\n", encoding="utf-8")
    (tmp_path / "docs" / "logo.png").write_bytes(b"\x89PNG")
    doc.write_text(doc.read_text(encoding="utf-8")
                   + "\nSee [env](../.env) and [logo](logo.png).\n", encoding="utf-8")
    s = gather_sources(doc, tmp_path)
    assert ".env" not in s and "docs/logo.png" not in s


def test_a_file_outside_the_root_is_never_a_source(tmp_path):
    from writing_register.humanize import gather_sources
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "elsewhere.md").write_text("outside\n", encoding="utf-8")
    doc = root / "README.md"
    doc.write_text("# R\n\nSee [x](../elsewhere.md).\n", encoding="utf-8")
    assert gather_sources(doc, root) == {}


def test_sources_are_cut_per_file_and_in_total(tmp_path):
    from writing_register.humanize import SOURCE_CHARS, SOURCES_CHARS, gather_sources
    (tmp_path / "docs").mkdir()
    links = []
    for i in range(6):
        (tmp_path / "docs" / f"s{i}.md").write_text("x" * (SOURCE_CHARS + 5000),
                                                    encoding="utf-8")
        links.append(f"[s{i}](s{i}.md)")
    doc = tmp_path / "docs" / "d.md"
    doc.write_text("# D\n\n" + " ".join(links) + "\n", encoding="utf-8")
    s = gather_sources(doc, tmp_path)
    assert all(len(body) <= SOURCE_CHARS for body in s.values())
    assert sum(map(len, s.values())) == SOURCES_CHARS


def test_the_voice_comes_before_the_skill_and_sources_come_last():
    from writing_register.humanize import build_prompt
    p = build_prompt("THE DOC\n", "THE SKILL", voice="THE VOICE",
                     sources={"a.md": "THE SOURCE"})
    assert p.index("THE VOICE") < p.index("THE SKILL") < p.index("THE DOC") < p.index("THE SOURCE")


def test_a_missing_skill_says_how_to_install(monkeypatch, tmp_path):
    """Audit, 2026-09-14: a normal `pip install` puts the package where
    vendor/ and voice/ are not, and the first rewrite died with a traceback."""
    import pytest
    from writing_register import humanize as h
    monkeypatch.setattr(h, "SKILL", tmp_path / "missing" / "SKILL.md")
    with pytest.raises(h.SetupError, match="uv tool install .*install.sh"):
        h.load_skill()


def test_a_new_link_to_a_file_that_exists_is_allowed(tmp_path):
    """ARCHITECTURE.md was refused twice on 2026-09-14 for linking four other
    documents, every one a real file beside it. A link to a file that exists is not invented. A link to one that does not
    is still refused, which the OPS.md test above keeps."""
    doc = _repo(tmp_path)
    (tmp_path / "docs" / "OPS.md").write_text("# Ops\n", encoding="utf-8")
    new = _tidy(doc.read_text(encoding="utf-8")).replace(
        "note taker.", "note taker; [ops](OPS.md#health) runs it.")
    r = humanize(doc, spawn=Spawn(new))
    assert r.written, r.refused


# A span rewritten as prose. On 2026-09-14 a retention guide was refused
# three times because a rewrite turned "`owner = retention` rows" into "rows
# whose owner is `retention`": the same two words, the same meaning, a different
# span. It is allowed, provided the words survive together.

RETENTION_OLD = ("# Registry\n\nThe sweeper enforces only `owner = retention` rows; the rest "
                 "are catalogued.\n\nTiers are listed in `.env.example`.\n")


def test_a_span_rewritten_as_prose_with_all_its_words_in_one_paragraph_is_allowed():
    from writing_register.humanize import check
    new = ("# Registry\n\nThe sweeper enforces only the rows whose owner is `retention`. "
           "The rest are catalogued.\n\nTiers are listed in `.env.example`.\n")
    assert check(RETENTION_OLD, new) == "", check(RETENTION_OLD, new)


def test_a_span_whose_words_changed_is_still_refused():
    from writing_register.humanize import check
    new = ("# Registry\n\nThe sweeper enforces only the rows whose owner is `cleanup`. "
           "The rest are catalogued.\n\nTiers are listed in `.env.example`.\n")
    assert "owner = retention" in check(RETENTION_OLD, new)


def test_a_span_whose_words_are_scattered_across_paragraphs_is_still_refused():
    from writing_register.humanize import check
    new = ("# Registry\n\nThe sweeper enforces only some rows, by owner. The rest are "
           "catalogued.\n\nTiers are listed in `.env.example`, and retention is one.\n")
    assert "owner = retention" in check(RETENTION_OLD, new)


def test_numbers_that_differ_as_versions_or_decimals_are_still_invented():
    """Audit 2026-09-22: comparing by value accepted 3.1 for 3.10."""
    from writing_register.humanize import _invented_numbers
    assert _invented_numbers("Python 3.1", "requires Python 3.10") == ["3.1"]
    assert _invented_numbers("4.2 volts", "4.20 volts") == ["4.2"]
    assert _invented_numbers("7 agents", "agent 007") == ["7"]
    assert _invented_numbers("60 degrees", "60.0 degrees") == []
    assert _invented_numbers("1000 rows", "1,000 rows") == []

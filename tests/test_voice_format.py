"""A voice read from a directory of rule files (2026-09-21).

A voice used to be one markdown file, split by a marker into the core the model
receives and the evidence people keep. As it grew, rules of different kinds
overlapped and an edit could land a rule in the core without its evidence. A
voice can now be a directory: a manifest, rule files the model receives in the
manifest's order, and evidence, decisions and notes the model never receives.
Every consumer still gets one string, so the hooks, the output style and the
rewriter do not change, and a single-file voice keeps working as before.
"""
import pytest

from writing_register import voice
from writing_register.config import voice_core


def _voice(tmp_path, files, order=None, extra=""):
    root = tmp_path / "v"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    names = order if order is not None else [f for f in files if f.startswith("rules/")]
    listed = ", ".join(f'"{f.removeprefix("rules/")}"' for f in names)
    (root / "voice.toml").write_text(f'format = 1\nname = "Tester"\norder = [{listed}]\n{extra}')
    return root


def test_the_core_reads_as_the_single_file_voice_did(tmp_path):
    root = _voice(tmp_path, {
        "rules/scope.md": "{#scope.what} This voice describes how the tester wants to read.\n",
        "rules/register.md": "# Register\n\n"
                             "{#register.aim} The text reads like a careful engineer.\n"
                             "{#register.plain refines=register.aim} It says things plainly.\n\n"
                             "- {#register.no-dashes} No em dashes. {#register.range} A range keeps its hyphen.\n"
                             "- {#register.no-slogans} No slogans.\n",
        "rules/kinds.md": "# By kind of text\n\n{#kinds.precedence} A kind's own rule wins over a general one.\n",
        "rules/kinds/replies.md": "# Replies\n\n{#replies.lead} Lead with the answer.\n",
        "rules/kinds/commits.md": "# Commit messages\n\n- {#commits.what} Say what changed.\n",
    }, order=["rules/scope.md", "rules/register.md", "rules/kinds.md",
              "rules/kinds/replies.md", "rules/kinds/commits.md"])
    assert voice.read_core(root) == (
        "# Voice: Tester\n\n"
        "This voice describes how the tester wants to read.\n\n"
        "## Register\n\n"
        "The text reads like a careful engineer. It says things plainly.\n\n"
        "- No em dashes. A range keeps its hyphen.\n"
        "- No slogans.\n\n"
        "## By kind of text\n\n"
        "A kind's own rule wins over a general one.\n\n"
        "**Replies.** Lead with the answer.\n\n"
        "**Commit messages.**\n"
        "- Say what changed.\n")


def test_no_rule_marker_reaches_the_model(tmp_path):
    root = _voice(tmp_path, {"rules/a.md": "# A\n\n{#a.one} One. {#a.two refines=a.one} Two.\n"})
    core = voice.read_core(root)
    assert "{#" not in core and "refines=" not in core


def test_the_model_never_receives_evidence_decisions_or_notes(tmp_path, monkeypatch):
    root = _voice(tmp_path, {
        "rules/a.md": "# A\n\n{#a.one} One.\n",
        "evidence/a.md": "## Why\nRules: a.one\n\nA quote, 2026-09-21.\n",
        "decisions.md": "## Question\nRules: a.one\nDecided: 2026-09-21\n",
        "about.md": "How it was built.\n",
        "maintaining.md": "How to change it.\n",
    })
    opened = []
    real = type(root).read_text

    def spy(self, *a, **kw):
        opened.append(self.name)
        return real(self, *a, **kw)

    monkeypatch.setattr(type(root), "read_text", spy)
    voice.read_core(root)
    assert set(opened) <= {"voice.toml", "a.md"}, opened
    assert "decisions.md" not in opened and "about.md" not in opened


def test_a_file_the_manifest_lists_but_does_not_exist_is_an_error(tmp_path):
    root = _voice(tmp_path, {"rules/a.md": "# A\n\n{#a.one} One.\n"},
                  order=["rules/a.md", "rules/missing.md"])
    with pytest.raises(voice.VoiceError, match="missing.md"):
        voice.read_core(root)


@pytest.mark.parametrize("manifest, message", [
    ('format = 1\nname = "T"\norder = []\noder = []\n', "unknown"),
    ('format = 2\nname = "T"\norder = []\n', "format"),
    ('format = 1\norder = []\n', "name"),
])
def test_a_manifest_that_is_wrong_says_how(tmp_path, manifest, message):
    root = tmp_path / "v"
    root.mkdir()
    (root / "voice.toml").write_text(manifest)
    with pytest.raises(voice.VoiceError, match=message):
        voice.read_core(root)


def test_a_single_file_voice_still_works(tmp_path):
    f = tmp_path / "v.md"
    f.write_text("# Voice: One\n\nA rule.\n\n<!-- wr:end-of-core -->\n\nEvidence.\n")
    assert voice.read_core(f) == "# Voice: One\n\nA rule.\n"
    assert "Evidence." in voice.read_full(f)


def test_the_full_voice_of_a_directory_carries_the_people_files(tmp_path):
    root = _voice(tmp_path, {"rules/a.md": "# A\n\n{#a.one} One.\n",
                             "evidence/a.md": "## Why\nRules: a.one\n\nA quote, 2026-09-21.\n"})
    full = voice.read_full(root)
    assert full.startswith(voice.read_core(root).rstrip("\n"))
    assert "A quote, 2026-09-21." in full


def test_a_generated_notice_on_the_first_line_is_not_part_of_the_core():
    text = "<!-- wr:generated from voice/x/ by wr voice build; edit the files there -->\n# Voice: X\n\nA rule.\n"
    assert voice_core(text) == "# Voice: X\n\nA rule.\n"

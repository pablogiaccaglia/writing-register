"""`wr humanize` from the command line: the voice, the sources and the exit codes."""
import io

from writing_register.cli import main
from writing_register.spawn import Answer

DOC = "# Capture\n\nIt is not a recorder, but a note taker. It uses 3 slots.\n"
CLEAN = "# Capture\n\nIt takes notes. It uses 3 slots.\n"


class Spawn:
    def __init__(self, reply):
        self.reply, self.prompt = reply, ""

    def run(self, prompt, **kw):
        self.prompt = prompt
        return Answer(stdout=self.reply, command=("claude",), duration_seconds=0.1)


def _run(tmp_path, *extra, reply=CLEAN):
    doc = tmp_path / "doc.md"
    doc.write_text(DOC, encoding="utf-8")
    out, s = io.StringIO(), Spawn(reply)
    # These tests are about the voice and the output; verifying against the
    # code has its own tests (test_cli_check.py), and this fake only rewrites.
    code = main(["humanize", str(doc), "--root", str(tmp_path), "--no-check", *extra],
                out=out, spawn=s)
    return code, out.getvalue(), s, doc


VOICE = ("# Voice: Tester\n\nWrite plainly.\n\n<!-- wr:end-of-core -->\n\n"
         "## Evidence\n\nAPPENDIX ONLY FOR PEOPLE.\n")


def _voice_setup(tmp_path, monkeypatch, config_text=None):
    from writing_register import config as c
    d = tmp_path / "voices"
    d.mkdir()
    (d / "tester.md").write_text(VOICE, encoding="utf-8")
    monkeypatch.setattr(c, "VOICES_DIR", d)
    if config_text is not None:
        cfg = tmp_path / "config.toml"
        cfg.write_text(config_text, encoding="utf-8")
        monkeypatch.setenv("WR_CONFIG", str(cfg))


def test_without_configuration_no_voice_is_sent(tmp_path):
    """The default is the humanizer skill alone, so nobody gets the maintainer's
    voice without asking for it (2026-09-14)."""
    code, out, s, doc = _run(tmp_path)
    assert code == 0, out
    assert "# The voice to write in" not in s.prompt and "# Voice:" not in s.prompt
    assert "Humanizer: remove AI writing patterns" in s.prompt
    assert "no voice" in out
    assert doc.read_text(encoding="utf-8") == CLEAN


def test_a_voice_set_in_config_sends_its_core_only(tmp_path, monkeypatch):
    _voice_setup(tmp_path, monkeypatch, 'voice = "tester"\n')
    code, out, s, _ = _run(tmp_path)
    assert code == 0, out
    assert "# Voice: Tester" in s.prompt and "Write plainly." in s.prompt
    assert "APPENDIX ONLY FOR PEOPLE" not in s.prompt
    assert "voice tester (config)" in out


def test_full_voice_sends_the_appendix_too(tmp_path, monkeypatch):
    _voice_setup(tmp_path, monkeypatch, 'voice = "tester"\n')
    code, _, s, _ = _run(tmp_path, "--full-voice")
    assert code == 0 and "APPENDIX ONLY FOR PEOPLE" in s.prompt


def test_voice_none_on_the_command_line_beats_the_config(tmp_path, monkeypatch):
    _voice_setup(tmp_path, monkeypatch, 'voice = "tester"\n')
    code, out, s, _ = _run(tmp_path, "--voice", "none")
    assert code == 0 and "# Voice: Tester" not in s.prompt and "no voice" in out


def test_a_voice_name_on_the_command_line_works_without_config(tmp_path, monkeypatch):
    _voice_setup(tmp_path, monkeypatch)
    code, out, s, _ = _run(tmp_path, "--voice", "tester")
    assert code == 0 and "# Voice: Tester" in s.prompt and "voice tester (--voice)" in out


def test_a_voice_file_path_on_the_command_line_works(tmp_path):
    mine = tmp_path / "mine.md"
    mine.write_text("# Voice: Someone else\n", encoding="utf-8")
    code, out, s, _ = _run(tmp_path, "--voice", str(mine))
    assert code == 0 and "# Voice: Someone else" in s.prompt and "voice mine" in out


def test_a_missing_voice_file_stops_before_any_call(tmp_path):
    code, out, s, _ = _run(tmp_path, "--voice", str(tmp_path / "nope.md"))
    assert code == 2 and not s.prompt and "nope.md" in out


def test_a_broken_config_stops_before_any_call(tmp_path, monkeypatch):
    _voice_setup(tmp_path, monkeypatch, 'vocie = "tester"\n')
    code, out, s, _ = _run(tmp_path)
    assert code == 2 and not s.prompt and "vocie" in out


def _voice_cmd(*args):
    out = io.StringIO()
    code = main(["voice", *args], out=out)
    return code, out.getvalue()


def test_wr_voice_says_when_no_voice_is_set():
    code, out = _voice_cmd()
    assert code == 0 and "no voice" in out


def test_wr_voice_names_the_active_voice_and_where_it_came_from(tmp_path, monkeypatch):
    _voice_setup(tmp_path, monkeypatch, 'voice = "tester"\n')
    code, out = _voice_cmd()
    assert code == 0 and "tester" in out and "config" in out


def test_wr_voice_core_prints_the_core_for_an_agent_to_follow(tmp_path, monkeypatch):
    _voice_setup(tmp_path, monkeypatch, 'voice = "tester"\n')
    code, out = _voice_cmd("--core")
    assert code == 0 and out.startswith("# Voice: Tester") and "APPENDIX" not in out


def test_wr_voice_core_prints_nothing_when_no_voice_is_set():
    assert _voice_cmd("--core") == (0, "")


def test_a_refusal_exits_1_and_says_where_the_rewrite_is(tmp_path):
    code, out, _, doc = _run(tmp_path, reply=CLEAN.replace("3 slots", "4 slots"))
    assert code == 1 and "refused" in out and "doc.refused.md" in out
    assert doc.read_text(encoding="utf-8") == DOC


def test_a_dry_run_prints_the_diff_and_writes_nothing(tmp_path):
    """Audit, 2026-09-14: a dry run paid for the model call and threw the rewrite
    away, so there was nothing to judge."""
    code, out, _, doc = _run(tmp_path, "--dry-run")
    assert code == 0
    assert "-It is not a recorder, but a note taker. It uses 3 slots." in out
    assert "+It takes notes. It uses 3 slots." in out
    assert doc.read_text(encoding="utf-8") == DOC
    assert sorted(f.name for f in tmp_path.iterdir()) == ["doc.md"]


def test_a_refused_dry_run_prints_the_diff_and_keeps_nothing(tmp_path):
    code, out, _, _ = _run(tmp_path, "--dry-run", reply=CLEAN.replace("3 slots", "4 slots"))
    assert code == 1 and "refused" in out and "+It takes notes. It uses 4 slots." in out
    assert sorted(f.name for f in tmp_path.iterdir()) == ["doc.md"]


def test_a_missing_skill_exits_2_and_names_the_installer(tmp_path, monkeypatch):
    from writing_register import humanize as h
    monkeypatch.setattr(h, "SKILL", tmp_path / "missing.md")
    code, out, s, _ = _run(tmp_path)
    assert code == 2 and "uv tool install" in out and "install.sh" in out and not s.prompt


def test_a_path_that_is_not_a_file_exits_2(tmp_path):
    out = io.StringIO()
    assert main(["humanize", str(tmp_path / "missing.md")], out=out,
                spawn=Spawn(CLEAN)) == 2
    assert "not a file" in out.getvalue()

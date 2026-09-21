"""Rewriting a piece of text rather than a file: commit messages and PR bodies.

Since 2026-09-15 wr runs on every commit message and PR description Claude
writes. A trial rewrite of a real commit message that day dropped the blank
line between the subject and the body, which git needs, so a commit is
rewritten with its structure held by code, not by the model."""
import io

from writing_register.humanize import humanize_text
from writing_register.spawn import Answer


class Spawn:
    def __init__(self, reply):
        self.reply, self.prompt, self.calls = reply, "", 0

    def run(self, prompt, **kw):
        self.calls += 1
        self.prompt = prompt
        return Answer(stdout=self.reply, command=("claude",), duration_seconds=0.1)


COMMIT = (
    "fix(render): CARD_OWNERS means the same thing everywhere\n"
    "\n"
    "It's worth noting that station_facts accepts on, 1, true, yes while both\n"
    "renderers accepted only \"1\". Now both renderers ask `owners_enabled`.\n"
    "\n"
    "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
    "Claude-Session: https://claude.ai/code/session_01Mgdkf9Ryzcp7NnBgM6wwj5\n"
)
BODY_REWRITE = ("The helper in station_facts accepts on, 1, true and yes, while both renderers "
                "accepted only \"1\". Both renderers now call `owners_enabled`.")


def test_a_commit_keeps_its_trailers_verbatim_and_never_sends_them():
    s = Spawn("fix(render): CARD_OWNERS means the same thing everywhere\n\n" + BODY_REWRITE + "\n")
    r = humanize_text(COMMIT, kind="commit", spawn=s)
    assert not r.refused, r.refused
    assert r.text.endswith("Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
                           "Claude-Session: https://claude.ai/code/session_01Mgdkf9Ryzcp7NnBgM6wwj5\n")
    assert "Claude-Session" not in s.prompt and "Co-Authored-By" not in s.prompt


def test_a_commit_whose_rewrite_lost_the_blank_line_gets_it_back():
    s = Spawn("fix(render): CARD_OWNERS means the same thing everywhere\n" + BODY_REWRITE + "\n")
    r = humanize_text(COMMIT, kind="commit", spawn=s)
    assert not r.refused, r.refused
    lines = r.text.split("\n")
    assert lines[0] == "fix(render): CARD_OWNERS means the same thing everywhere"
    assert lines[1] == "" and lines[2].startswith("The helper")


def test_a_commit_rewrite_that_drops_the_conventional_prefix_is_refused():
    s = Spawn("Read CARD_OWNERS the same way in both renderers\n\n" + BODY_REWRITE + "\n")
    r = humanize_text(COMMIT, kind="commit", spawn=s)
    assert "fix(render):" in r.refused
    assert r.text == COMMIT


def test_a_commit_rewrite_that_invents_a_number_is_refused_and_the_original_kept():
    s = Spawn("fix(render): CARD_OWNERS means the same thing everywhere\n\n"
              + BODY_REWRITE + " The new tests cover 8 cases.\n")
    r = humanize_text(COMMIT, kind="commit", spawn=s)
    assert "8" in r.refused and r.text == COMMIT


def test_the_commit_prompt_says_what_a_commit_message_is():
    s = Spawn("fix(render): CARD_OWNERS means the same thing everywhere\n\n" + BODY_REWRITE + "\n")
    humanize_text(COMMIT, kind="commit", spawn=s)
    assert "commit message" in s.prompt.lower() and "subject" in s.prompt.lower()


PR = (
    "## Summary\n\nIt's worth noting this fixes the owners knob.\n\n"
    "## Test plan\n\n- `make test`\n\n"
    "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
)


def test_a_pr_body_keeps_the_generated_footer_verbatim_and_never_sends_it():
    s = Spawn("## Summary\n\nThis fixes the owners knob.\n\n## Test plan\n\n- `make test`\n")
    r = humanize_text(PR, kind="pr", spawn=s)
    assert not r.refused, r.refused
    assert r.text.endswith("\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n")
    assert "Generated with" not in s.prompt


def test_text_that_needs_nothing_is_returned_unchanged():
    s = Spawn(COMMIT.split("\n\nCo-Authored-By")[0] + "\n")
    r = humanize_text(COMMIT, kind="commit", spawn=s)
    assert not r.refused and r.text == COMMIT


def test_the_cli_text_mode_reads_stdin_and_prints_the_rewrite(monkeypatch):
    from writing_register.cli import main
    monkeypatch.setattr("sys.stdin", io.StringIO(COMMIT))
    out = io.StringIO()
    s = Spawn("fix(render): CARD_OWNERS means the same thing everywhere\n\n" + BODY_REWRITE + "\n")
    code = main(["humanize", "--text", "--kind", "commit"], out=out, spawn=s)
    assert code == 0 and out.getvalue().startswith("fix(render):")
    assert "Claude-Session" in out.getvalue()


def test_the_cli_text_mode_prints_the_original_when_refused(monkeypatch):
    from writing_register.cli import main
    monkeypatch.setattr("sys.stdin", io.StringIO(COMMIT))
    out = io.StringIO()
    s = Spawn("Read CARD_OWNERS the same way\n\n" + BODY_REWRITE + "\n")
    code = main(["humanize", "--text", "--kind", "commit"], out=out, spawn=s)
    assert code == 1 and out.getvalue() == COMMIT


# Audit 2026-09-15: a reply identical to the message except for its trailing
# newline was reported as a rewrite, and the hook stripped the newline.

def test_a_reply_that_differs_only_in_trailing_newlines_is_not_a_change():
    from writing_register.humanize import humanize_text
    from writing_register.spawn import Answer

    class Same:
        def run(self, prompt, **kw):
            return Answer(stdout="fix: same\n", command=("claude",), duration_seconds=0.1)

    r = humanize_text("fix: same\n\n", kind="commit", spawn=Same())
    assert not r.changed and r.text == "fix: same\n\n"


def test_the_hook_does_not_report_an_unchanged_message_as_rewritten(tmp_path, monkeypatch):
    import io
    import json
    from writing_register import hooks
    from writing_register.spawn import Answer

    class Same:
        def run(self, prompt, **kw):
            return Answer(stdout="fix: same\n", command=("claude",), duration_seconds=0.1)

    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "none"\nauto = ["commit"]\n', encoding="utf-8")
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    payload = {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "fix: same\n"'}}
    out = io.StringIO()
    assert hooks.run("pre-bash", io.StringIO(json.dumps(payload)), out, spawn=Same()) == 0
    assert out.getvalue() == ""


# Audit follow-up, 2026-09-15. The half-length guard was built for documents.
# Live, it refused a commit message whose body was all filler. On 14 real
# commit messages from the session logs no rewrite came out shorter than the
# original (ratios 1.00 to 1.68), so a commit or PR may shrink to a quarter;
# a rewrite that guts it below that is still refused, and the reason names the
# message instead of "the document".

def test_a_commit_message_that_was_mostly_filler_may_shrink():
    original = ("chore: add b\n\nIt's worth noting that this pivotal file serves as a "
                "testament to our commitment.\n")
    r = humanize_text(original, kind="commit", spawn=Spawn("chore: add b\n\nAdd the file b.\n"))
    assert not r.refused and r.changed


def test_a_gutted_commit_message_is_still_refused_and_called_a_message():
    body = " ".join(["The worker sync now copies the scripts folder along with the sources, "
                     "because the cleanup tasks on each worker need those scripts."] * 6)
    r = humanize_text(f"fix(sync): copy scripts to workers\n\n{body}\n", kind="commit",
                      spawn=Spawn("fix(sync): copy scripts to workers\n\nCopies scripts.\n"))
    assert "a cut, not a rewrite" in r.refused
    assert "message" in r.refused and "document" not in r.refused


def test_a_gutted_pr_description_is_refused_and_called_a_description():
    body = " ".join(["This change makes both renderers read the same owners setting, so a "
                     "card and its preview never disagree about who owns a task."] * 6)
    r = humanize_text(f"## Summary\n\n{body}\n", kind="pr", spawn=Spawn("## Summary\n\nFixes owners.\n"))
    assert "a cut, not a rewrite" in r.refused and "description" in r.refused

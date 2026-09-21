"""The plugin's hooks: wr rewrites what Claude writes, when the user asked for it.

Since 2026-09-15, commit messages, PR descriptions and markdown files go
through wr automatically. The shapes below are the ones Claude used in 173
real commits and 99 real PR commands found in the session logs that day."""
import io
import json

import pytest

from writing_register import hooks
from writing_register.spawn import Answer


class Spawn:
    def __init__(self, reply=None, fn=None):
        self.reply, self.fn, self.calls, self.prompts = reply, fn, 0, []

    def run(self, prompt, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        text = self.fn(prompt) if self.fn else self.reply
        return Answer(stdout=text, command=("claude",), duration_seconds=0.1)


def _auto(tmp_path, monkeypatch, values='["markdown", "commit", "pr"]', voice="none"):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'voice = "{voice}"\nauto = {values}\n', encoding="utf-8")
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")


def _pre_bash(command, spawn):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command, "description": "Commit"}}
    out = io.StringIO()
    code = hooks.run("pre-bash", io.StringIO(json.dumps(payload)), out, spawn=spawn)
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else None)


HEREDOC = """git add -A && git commit -q -m "$(cat <<'EOF'
fix(render): owners mean the same everywhere

It's worth noting both renderers now ask owners_enabled.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)" && git push -q"""
REWRITE = "fix(render): owners mean the same everywhere\n\nBoth renderers now ask owners_enabled.\n"


def test_a_heredoc_commit_message_is_rewritten_in_place(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    code, out = _pre_bash(HEREDOC, Spawn(REWRITE))
    assert code == 0
    new = out["hookSpecificOutput"]["updatedInput"]["command"]
    assert "Both renderers now ask owners_enabled." in new
    assert "It's worth noting" not in new
    assert "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\nEOF\n)\" && git push -q" in new
    assert new.startswith("git add -A && git commit -q -m \"$(cat <<'EOF'\n")


def test_the_rewrite_keeps_the_other_tool_input_fields_and_never_grants_permission(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    _, out = _pre_bash(HEREDOC, Spawn(REWRITE))
    spec = out["hookSpecificOutput"]
    assert spec["updatedInput"]["description"] == "Commit"
    assert "permissionDecision" not in spec


STDIN_HEREDOC = """git commit -q -F - <<'MSG' && git log --oneline -2
carry the import fixes into the Worker

It's worth noting the audit found more.
MSG"""


def test_a_commit_message_on_stdin_is_rewritten(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    _, out = _pre_bash(STDIN_HEREDOC, Spawn("carry the import fixes into the Worker\n\nThe audit found more.\n"))
    new = out["hookSpecificOutput"]["updatedInput"]["command"]
    assert new == ("git commit -q -F - <<'MSG' && git log --oneline -2\n"
                   "carry the import fixes into the Worker\n\nThe audit found more.\nMSG")


QUOTED = 'git commit -qam "say what the redraw showed\n\nIt\'s worth noting the \\"redraw\\" matched.\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>" && git push'


def test_a_double_quoted_commit_message_is_rewritten_and_re_escaped(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    _, out = _pre_bash(QUOTED, Spawn('say what the redraw showed\n\nThe "redraw" matched, $5 of it.\n'))
    # $5 is an invented number, so this one is refused; use a clean rewrite instead
    assert out is None or "updatedInput" not in out.get("hookSpecificOutput", {})
    _, out = _pre_bash(QUOTED, Spawn('say what the redraw showed\n\nThe "redraw" matched.\n'))
    new = out["hookSpecificOutput"]["updatedInput"]["command"]
    assert new == ('git commit -qam "say what the redraw showed\n\nThe \\"redraw\\" matched.\n\n'
                   'Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" && git push')


PR = """gh pr create --title "Owners fix" --body "$(cat <<'EOF'
## Summary

It's worth noting this fixes the owners knob.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)" """


def test_a_pr_body_heredoc_is_rewritten_and_keeps_its_footer(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    _, out = _pre_bash(PR, Spawn("## Summary\n\nThis fixes the owners knob.\n"))
    new = out["hookSpecificOutput"]["updatedInput"]["command"]
    assert "This fixes the owners knob." in new and "worth noting" not in new
    assert "🤖 Generated with [Claude Code](https://claude.com/claude-code)\nEOF\n)\"" in new
    assert '--title "Owners fix"' in new


def test_nothing_happens_when_auto_does_not_list_the_kind(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch, values='["markdown"]')
    s = Spawn(REWRITE)
    code, out = _pre_bash(HEREDOC, s)
    assert code == 0 and out is None and s.calls == 0


def test_nothing_happens_without_any_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    s = Spawn(REWRITE)
    code, out = _pre_bash(HEREDOC, s)
    assert code == 0 and out is None and s.calls == 0


@pytest.mark.parametrize("entrypoint", ["sdk-cli", "sdk-py", "sdk-ts"])
def test_nothing_happens_in_a_scripted_claude_run(tmp_path, monkeypatch, entrypoint):
    """wr humanize itself starts `claude -p`, and so can any tool that drives Claude from a script."""
    _auto(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", entrypoint)
    s = Spawn(REWRITE)
    code, out = _pre_bash(HEREDOC, s)
    assert code == 0 and out is None and s.calls == 0


@pytest.mark.parametrize("command", [
    "git commit -q --no-edit && git log --oneline -1",
    "git commit -m first -m second",
    'git commit -m "$(echo generated)"',
    "git status && ls",
    "gh pr edit 81 --base experiments/x",
    "gh pr create --title T --body-file pr.md",
])
def test_shapes_it_cannot_rewrite_safely_are_left_alone(tmp_path, monkeypatch, command):
    _auto(tmp_path, monkeypatch)
    s = Spawn(REWRITE)
    code, out = _pre_bash(command, s)
    assert code == 0 and out is None and s.calls == 0


def test_a_refused_rewrite_leaves_the_command_alone_and_says_why(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    code, out = _pre_bash(HEREDOC, Spawn("Owners mean the same everywhere\n\nBoth renderers now ask owners_enabled.\n"))
    assert code == 0
    assert "updatedInput" not in out.get("hookSpecificOutput", {})
    assert "wr" in out["systemMessage"] and "fix(render):" in out["systemMessage"]


def test_a_rewrite_that_would_end_the_heredoc_early_is_refused(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    _, out = _pre_bash(HEREDOC, Spawn("fix(render): owners mean the same everywhere\n\nEOF\n"))
    assert "updatedInput" not in (out or {}).get("hookSpecificOutput", {})


def test_a_failed_model_call_leaves_the_commit_alone(tmp_path, monkeypatch):
    from writing_register.spawn import SpawnFailed

    class Broken:
        def run(self, prompt, **kw):
            raise SpawnFailed("claude timed out after 90 seconds")

    _auto(tmp_path, monkeypatch)
    code, out = _pre_bash(HEREDOC, Broken())
    assert code == 0 and "updatedInput" not in (out or {}).get("hookSpecificOutput", {})


def _session_start():
    out = io.StringIO()
    code = hooks.run("session-start", io.StringIO(json.dumps({"source": "startup"})), out)
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else None)


def test_session_start_gives_claude_the_voice_core(tmp_path, monkeypatch):
    """Moved from the user's own settings into the plugin on 2026-09-15."""
    voice = tmp_path / "mine.md"
    voice.write_text("# Voice\n\nExplain first.\n\n<!-- wr:end-of-core -->\n\nEvidence.\n",
                     encoding="utf-8")
    _auto(tmp_path, monkeypatch, values="[]", voice="./mine.md")
    code, out = _session_start()
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert code == 0 and out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Explain first." in ctx and "Evidence." not in ctx
    assert "humanizer" in ctx and "rewrites" not in ctx


def test_session_start_says_what_wr_rewrites_automatically(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch, values='["commit", "markdown"]')
    _, out = _session_start()
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "commit messages" in ctx and "markdown files" in ctx
    assert "PR descriptions" not in ctx


def test_session_start_still_sends_the_patterns_with_no_voice_and_no_auto(tmp_path, monkeypatch):
    """2026-09-16: both halves travel in every modality. A user with no
    voice and no automatic rewrites is still writing for people, so the
    machine-writing patterns go to them."""
    _auto(tmp_path, monkeypatch, values="[]")
    code, reply = _session_start()
    assert code == 0
    assert "Not X but Y" in reply["hookSpecificOutput"]["additionalContext"]


def test_session_start_is_silent_in_a_scripted_run(tmp_path, monkeypatch):
    """A scripted `claude -p` session gets nothing: each rewrite wr makes is
    one of those, and a hook acting there would start a rewrite inside a
    rewrite."""
    _auto(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
    assert _session_start() == (0, None)


# Audit 2026-09-15: CLAUDE.md says a hook exits 0 whatever goes wrong, and a
# reviewer made `wr hook` exit 1 with a traceback on payloads like these.

@pytest.mark.parametrize("event", ["session-start", "pre-bash", "post-edit", "stop", "prompt"])
@pytest.mark.parametrize("payload", ["null", "[]", '"text"',
                                     '{"tool_name": "Edit", "tool_input": "x.md"}',
                                     '{"tool_input": {"command": 5}}',
                                     '{"session_id": 7, "tool_name": "Write", "tool_input": {"file_path": 3}}'])
def test_a_malformed_payload_never_breaks_a_hook(tmp_path, monkeypatch, event, payload):
    _auto(tmp_path, monkeypatch)
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "state"))
    out = io.StringIO()
    assert hooks.run(event, io.StringIO(payload), out, spawn=Spawn(REWRITE)) == 0


def test_a_handler_that_raises_still_exits_zero(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)

    def boom(payload, spawn=None):
        raise RuntimeError("unexpected")

    monkeypatch.setitem(hooks.HANDLERS, "pre-bash", boom)
    out = io.StringIO()
    assert hooks.run("pre-bash", io.StringIO("{}"), out) == 0
    message = json.loads(out.getvalue())["systemMessage"]
    assert "wr hook pre-bash failed" in message and "unexpected" in message


def test_a_voice_file_that_is_not_utf8_does_not_break_the_hooks(tmp_path, monkeypatch):
    (tmp_path / "latin.md").write_bytes(b"# Voce\n\nPerch\xe9 no.\n")
    _auto(tmp_path, monkeypatch, voice="./latin.md")
    s = Spawn(REWRITE)
    code, _ = _pre_bash(HEREDOC, s)
    assert code == 0
    assert _session_start()[0] == 0


def test_an_unusable_state_dir_does_not_break_the_edit_hook(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("WR_STATE_DIR", str(blocker))
    payload = {"session_id": "s", "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "a.md")}}
    assert hooks.run("post-edit", io.StringIO(json.dumps(payload)), io.StringIO()) == 0


# Audit 2026-09-15: a typo in the configuration turned every automatic rewrite
# and the voice off, and nobody was told.

def test_session_start_tells_the_user_when_the_config_has_a_mistake(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('voice = "nosuchvoice"\nauto = ["commit"]\n', encoding="utf-8")
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    code, out = _session_start()
    assert code == 0
    assert "nosuchvoice" in out["systemMessage"] and "wr" in out["systemMessage"]
    assert "config.toml" in out["systemMessage"]


def test_session_start_tells_the_user_when_the_voice_cannot_be_read(tmp_path, monkeypatch):
    (tmp_path / "latin.md").write_bytes(b"# Voce\n\nPerch\xe9 no.\n")
    _auto(tmp_path, monkeypatch, values='["commit"]', voice="./latin.md")
    code, out = _session_start()
    assert code == 0 and "latin.md" in out["systemMessage"]
    assert "commit messages" in out["hookSpecificOutput"]["additionalContext"]


# Audit 2026-09-15: the reviewer's command shapes. `git commit` and `gh pr`
# count only where a command starts, and the message must belong to that
# command, outside quotes and heredoc bodies.

PR_MENTIONING_COMMIT = """gh pr create --title "Fix the git commit hook" --body "$(cat <<'EOF'
## Summary

It's worth noting that git now runs the commit hook, and pytest -m "slow" still passes.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)" """


def test_a_pr_that_mentions_git_commit_is_still_rewritten_as_a_pr(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    s = Spawn("## Summary\n\nGit now runs the commit hook, and pytest -m \"slow\" still passes.\n")
    _, out = _pre_bash(PR_MENTIONING_COMMIT, s)
    assert s.calls == 1 and "pull request description" in s.prompts[0]
    new = out["hookSpecificOutput"]["updatedInput"]["command"]
    assert "Git now runs the commit hook" in new and '--title "Fix the git commit hook"' in new


def test_git_commit_with_a_repository_option_is_rewritten(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    command = HEREDOC.replace("git commit -q", "git -C ../repo commit -q")
    _, out = _pre_bash(command, Spawn(REWRITE))
    assert "Both renderers now ask owners_enabled." in out["hookSpecificOutput"]["updatedInput"]["command"]


@pytest.mark.parametrize("command", [
    """git commit -m 'Document the -m "text" flag'""",
    """cat > notes.txt <<EOF
pytest -m "slow"
EOF
git commit -F notes.txt""",
    'git commit --amend --no-edit && git tag -a v1 -m "release notes for the tag"',
    'git merge -m "merge the commit branch" feature',
    'git commit-tree abc123 -m "a tree message"',
    'gh pr create --body-file pr.md && gh pr comment 5 --body "thanks for the review"',
    'echo "git commit -m \\"not a command\\""',
])
def test_a_message_that_does_not_belong_to_the_commit_or_pr_is_left_alone(tmp_path, monkeypatch, command):
    _auto(tmp_path, monkeypatch)
    s = Spawn(REWRITE)
    code, out = _pre_bash(command, s)
    assert code == 0 and out is None and s.calls == 0


def test_a_shell_comment_with_an_apostrophe_does_not_hide_the_commit(tmp_path, monkeypatch):
    """Found by replaying real commands from the session logs: a comment line
    such as `# already git mv'd` hid the commit that followed it."""
    _auto(tmp_path, monkeypatch)
    command = "git add docs/a.md\n# the rename is already git mv'd\n" + HEREDOC.split("&& ", 1)[1]
    _, out = _pre_bash(command, Spawn(REWRITE))
    assert "Both renderers now ask owners_enabled." in out["hookSpecificOutput"]["updatedInput"]["command"]


def test_a_pr_command_continued_over_several_lines_is_rewritten(tmp_path, monkeypatch):
    """Found by the replay: `\\` at the end of a line continues the command."""
    _auto(tmp_path, monkeypatch)
    command = "cd ~/repo\ngh pr create --base main --head feat/x \\\n  --title \"Owners fix\" \\\n  " + PR.split("--title \"Owners fix\" ", 1)[1]
    _, out = _pre_bash(command, Spawn("## Summary\n\nThis fixes the owners knob.\n"))
    assert "This fixes the owners knob." in out["hookSpecificOutput"]["updatedInput"]["command"]


def test_git_commit_with_a_quoted_repository_path_is_rewritten(tmp_path, monkeypatch):
    """Found by the replay: `git -C "$A" commit -F - <<'EOF'`."""
    _auto(tmp_path, monkeypatch)
    command = STDIN_HEREDOC.replace("git commit -q", 'git -C "$A" commit -q')
    _, out = _pre_bash(command, Spawn("carry the import fixes into the Worker\n\nThe audit found more.\n"))
    assert "The audit found more." in out["hookSpecificOutput"]["updatedInput"]["command"]


def test_nested_quotes_in_a_command_substitution_do_not_hide_the_commit(tmp_path, monkeypatch):
    """Found by the replay: `"$(echo "$X" | grep -c '^RED')"` before the commit."""
    _auto(tmp_path, monkeypatch)
    command = ("if [ \"$(echo \"$PROOFS\" | grep -c '^RED')\" = \"2\" ]; then\n  "
               + HEREDOC.split("&& ", 1)[1] + "\nfi")
    _, out = _pre_bash(command, Spawn(REWRITE))
    assert "Both renderers now ask owners_enabled." in out["hookSpecificOutput"]["updatedInput"]["command"]


def test_git_commit_with_a_quoted_config_value_is_rewritten(tmp_path, monkeypatch):
    """Found by the replay: `git -c user.name="Omar K" commit`."""
    _auto(tmp_path, monkeypatch)
    command = HEREDOC.replace("git commit -q", 'git -c user.email=a@b.c -c user.name="Omar K" commit -q')
    _, out = _pre_bash(command, Spawn(REWRITE))
    assert "Both renderers now ask owners_enabled." in out["hookSpecificOutput"]["updatedInput"]["command"]


# Audit 2026-09-15: behaviours the reviewer found claimed but untested.

def test_a_quoted_rewrite_reaches_git_exactly_as_the_model_wrote_it(tmp_path, monkeypatch):
    """The rewritten command is run by a real shell with a stand-in `git` that
    prints its message, so escaping is checked end to end."""
    import subprocess
    _auto(tmp_path, monkeypatch)
    command = 'git commit -m "keep the \\"quotes\\", the \\\\ backslash, and the HOME literal"'
    reply = 'keep the "quotes", the \\ backslash, and the literal $HOME and `x`\n'

    monkeypatch.setattr(hooks, "humanize_text", lambda text, **kw: type(
        "R", (), {"refused": "", "changed": True, "text": reply, "seconds": 0.0})())
    _, out = _pre_bash(command, Spawn(reply))
    new = out["hookSpecificOutput"]["updatedInput"]["command"]
    shown = subprocess.run(["bash", "-c", 'git() { printf "%s" "$3"; }; ' + new],
                           capture_output=True, text=True).stdout
    assert shown == reply.rstrip("\n")


def test_a_double_quoted_pr_body_is_rewritten(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    command = 'gh pr create --title "Owners" --body "It\'s worth noting this fixes the owners knob."'
    _, out = _pre_bash(command, Spawn("This fixes the owners knob.\n"))
    assert out["hookSpecificOutput"]["updatedInput"]["command"] == (
        'gh pr create --title "Owners" --body "This fixes the owners knob."')


def test_gh_pr_edit_body_heredoc_is_rewritten(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    command = PR.replace('gh pr create --title "Owners fix"', "gh pr edit 81")
    _, out = _pre_bash(command, Spawn("## Summary\n\nThis fixes the owners knob.\n"))
    assert "This fixes the owners knob." in out["hookSpecificOutput"]["updatedInput"]["command"]


@pytest.mark.parametrize("event", ["post-edit", "stop", "prompt"])
def test_the_markdown_hooks_do_nothing_in_a_scripted_run(tmp_path, monkeypatch, event):
    _auto(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
    monkeypatch.setenv("WR_STATE_DIR", str(tmp_path / "state"))
    payload = {"session_id": "s", "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "a.md")}}
    out = io.StringIO()
    s = Spawn(REWRITE)
    assert hooks.run(event, io.StringIO(json.dumps(payload)), out, spawn=s) == 0
    assert out.getvalue() == "" and s.calls == 0 and not (tmp_path / "state").exists()


def test_no_hook_ever_answers_with_a_permission_decision():
    import inspect
    assert "permissionDecision" not in inspect.getsource(hooks)


# Audit follow-up, 2026-09-15: a commit and a PR in one command used to get only
# the commit rewritten.

COMMIT_AND_PR = HEREDOC.replace(" && git push -q", "") + """ && gh pr create --title "Owners fix" --body "$(cat <<'EOF'
## Summary

It's worth noting this fixes the owners knob.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)" """


def _by_kind(commit_reply, pr_reply):
    return Spawn(fn=lambda prompt: pr_reply if "pull request description" in prompt else commit_reply)


def test_a_commit_and_a_pr_in_one_command_are_both_rewritten(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    s = _by_kind(REWRITE, "## Summary\n\nThis fixes the owners knob.\n")
    _, out = _pre_bash(COMMIT_AND_PR, s)
    new = out["hookSpecificOutput"]["updatedInput"]["command"]
    assert s.calls == 2
    assert "Both renderers now ask owners_enabled." in new and "This fixes the owners knob." in new
    assert "worth noting" not in new
    assert "commit message" in out["systemMessage"] and "PR description" in out["systemMessage"]


def test_when_one_of_two_messages_is_refused_the_other_is_still_rewritten(tmp_path, monkeypatch):
    _auto(tmp_path, monkeypatch)
    lost_prefix = "Owners mean the same everywhere\n\nBoth renderers now ask owners_enabled.\n"
    s = _by_kind(lost_prefix, "## Summary\n\nThis fixes the owners knob.\n")
    _, out = _pre_bash(COMMIT_AND_PR, s)
    new = out["hookSpecificOutput"]["updatedInput"]["command"]
    assert "This fixes the owners knob." in new
    assert "It's worth noting both renderers now ask owners_enabled." in new
    assert "kept the commit message" in out["systemMessage"] and "rewrote the PR description" in out["systemMessage"]

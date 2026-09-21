"""The sandboxed spawn.

Three implementations of this existed across three repositories and they had
drifted. Everything each of them learned the hard way is a test here."""
import subprocess
from pathlib import Path

import pytest
from writing_register.spawn import SpawnFailed, Spawn, run


class Fake:
    """Records the call instead of making it."""

    def __init__(self, stdout="ok", stderr="", returncode=0, raises=None):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.raises = raises
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout,
                                           self.stderr)

    @property
    def cmd(self):
        return self.calls[0][0]

    @property
    def kw(self):
        return self.calls[0][1]


def test_the_prompt_goes_on_stdin_and_never_in_argv():
    """Passed as an argument it can cross the operating system's single-argument
    ceiling, and the spawn then fails with 'argument list too long' while the
    stage returns nothing. Observed live at 143 KB."""
    f = Fake()
    run("a very long prompt", runner=f)
    assert f.kw["input"] == "a very long prompt"
    assert "a very long prompt" not in f.cmd


def test_no_mcp_server_is_reachable():
    """An allowed-tools list is a PERMISSION list, not a capability
    restriction: measured, a call with no flag, with an empty list, and with one
    read tool named all reported the same servers present. Only an empty MCP
    config removes them."""
    f = Fake()
    run("p", runner=f)
    assert "--strict-mcp-config" in f.cmd
    i = f.cmd.index("--mcp-config")
    assert f.cmd[i + 1] == '{"mcpServers":{}}'


def test_the_working_directory_is_neutral():
    """Project skills, settings and CLAUDE.md are discovered by walking up from
    the working directory. Spawning inside a project turns a text synthesis into
    an agent carrying that project's instructions."""
    import os
    import tempfile
    f = Fake()
    run("p", runner=f)
    cwd = Path(f.kw["cwd"]).resolve()
    assert cwd != Path(os.getcwd()).resolve()
    assert str(cwd).startswith(str(Path(tempfile.gettempdir()).resolve()))
    assert not (cwd / "CLAUDE.md").exists() and not (cwd / ".mcp.json").exists()


def test_the_environment_is_an_allowlist_not_an_inheritance(monkeypatch):
    monkeypatch.setenv("ARCHIVE_TOKEN", "secret")
    monkeypatch.setenv("DISCORD_TOKEN", "secret")
    monkeypatch.setenv("CLAUDECODE", "1")
    f = Fake()
    run("p", runner=f)
    env = f.kw["env"]
    assert "PATH" in env and "HOME" in env
    assert "ARCHIVE_TOKEN" not in env
    assert "DISCORD_TOKEN" not in env
    assert "CLAUDECODE" not in env


def test_the_child_gets_its_own_process_group():
    """So a timed-out tree can be killed cleanly rather than orphaned."""
    f = Fake()
    run("p", runner=f)
    assert f.kw["start_new_session"] is True


def test_the_timeout_is_explicit_and_passed_through():
    f = Fake()
    run("p", runner=f, timeout=42)
    assert f.kw["timeout"] == 42


def test_the_model_and_effort_reach_the_command():
    f = Fake()
    run("p", runner=f, model="claude-opus-5", effort="high")
    assert "--model" in f.cmd and "claude-opus-5" in f.cmd
    assert "--effort" in f.cmd and "high" in f.cmd


def test_empty_output_is_a_failure_not_a_success():
    """Silent success is the failure mode this keeps hitting."""
    with pytest.raises(SpawnFailed) as e:
        run("p", runner=Fake(stdout="   \n"))
    assert "empty" in str(e.value)


def test_a_non_zero_exit_carries_its_stderr():
    with pytest.raises(SpawnFailed) as e:
        run("p", runner=Fake(returncode=1, stderr="model unavailable"))
    assert "model unavailable" in str(e.value)


def test_a_timeout_says_what_timed_out_and_after_how_long():
    err = subprocess.TimeoutExpired(cmd=["claude"], timeout=30)
    with pytest.raises(SpawnFailed) as e:
        run("p", runner=Fake(raises=err), timeout=30)
    assert "30" in str(e.value) and "timed out" in str(e.value)


def test_a_missing_binary_says_which_one():
    with pytest.raises(SpawnFailed) as e:
        run("p", runner=Fake(raises=FileNotFoundError("claude")))
    assert "claude" in str(e.value)


def test_the_spawn_records_what_it_ran_for_the_journal():
    s = Spawn(runner=Fake())
    out = s.run("a distinctive prompt body")
    assert out.stdout == "ok"
    assert out.command and out.duration_seconds >= 0
    assert "distinctive" not in " ".join(out.command)


def test_a_failure_message_on_stdout_reaches_the_caller():
    """`claude` prints "Not logged in - Please run /login" on stdout and exits
    1, with stderr empty. The wrapper read stderr, found nothing, and reported
    "no stderr", so two runs failed with the reason on the screen and thrown
    away. Whatever the process says about why it failed is the message."""
    def runner(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="Not logged in · Please run /login\n", stderr="")

    with pytest.raises(SpawnFailed) as e:
        Spawn(runner=runner).run("prompt")
    assert "Not logged in" in str(e.value)


def test_stderr_still_wins_when_there_is_some():
    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="junk",
                                           stderr="the real reason")

    with pytest.raises(SpawnFailed) as e:
        Spawn(runner=runner).run("prompt")
    assert "the real reason" in str(e.value)


def test_a_failure_with_nothing_anywhere_still_says_so():
    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    with pytest.raises(SpawnFailed) as e:
        Spawn(runner=runner).run("prompt")
    assert "said nothing" in str(e.value)


# 2026-09-15: the checker that verifies a rewrite against the code is
# a second call through this one spawn point, with read-only access to an
# export of the repository's tracked files and nothing else.

import subprocess as _subprocess

from writing_register.spawn import export_tracked, remove_export


def test_a_read_root_adds_the_read_only_sandbox_flags(tmp_path):
    cmd = Spawn().command(None, None, read_root=tmp_path, system_prompt="be strict", json_schema='{"type":"object"}')
    joined = " ".join(cmd)
    for flag in ("--safe-mode", "--tools Read,Grep,Glob", "--allowedTools Read,Grep,Glob",
                 "--permission-prompts none", f"--add-dir {tmp_path}", "--output-format json",
                 "--system-prompt be strict", '--json-schema {"type":"object"}'):
        assert flag in joined, flag
    assert "--strict-mcp-config" in joined
    plain = Spawn().command(None, None)
    assert plain == ["claude", "-p", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']


def test_the_export_holds_the_tracked_files_and_the_overlay(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "a.py").write_text("committed = 1\n")
    (repo / ".env").write_text("SECRET=1\n")
    (repo / ".gitignore").write_text(".env\nout/\n")
    (repo / "out").mkdir()
    (repo / "out" / "log.txt").write_text("real station\n")
    _subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    _subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one"], cwd=repo, check=True)
    (repo / "a.py").write_text("committed = 2\n")          # uncommitted edit
    (repo / "docs").mkdir()
    (repo / "docs" / "new.md").write_text("# new, untracked\n")
    export = export_tracked(repo, overlay=[repo / "docs" / "new.md", repo / "a.py"])
    try:
        assert (export / "a.py").read_text() == "committed = 2\n"      # overlay wins
        assert (export / "docs" / "new.md").exists()                    # overlay adds
        assert (export / ".gitignore").exists()
        assert not (export / ".env").exists() and not (export / "out").exists()
        assert "writing-register-spawn" in str(export)
    finally:
        remove_export(export)
    assert not export.exists()

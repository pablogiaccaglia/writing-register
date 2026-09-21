"""Every test runs without the user's real configuration.

A voice switched on in ~/.config/writing-register/config.toml on the machine
running the suite would otherwise change what the prompt tests see, and the
user's global git ignore file would otherwise decide what the hooks skip:
the check is `git check-ignore`, which reads that file, so a machine whose
global file lists *.refused.md passed a test that asserts a repository
without the line is skipped (audit 2026-09-16)."""
import os

import pytest


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path_factory, monkeypatch):
    missing = tmp_path_factory.mktemp("no-config") / "config.toml"
    monkeypatch.setenv("WR_CONFIG", str(missing))
    # An empty config home, so neither wr's config.toml nor git's global ignore
    # file is the machine's. `GIT_CONFIG_GLOBAL` alone is not enough: git finds
    # its global ignore file from the config home, not from the config file, so
    # the config it points at has to name an empty excludes file.
    empty = tmp_path_factory.mktemp("no-config-home")
    gitconfig = empty / "gitconfig"
    gitconfig.write_text(f"[core]\n\texcludesFile = {os.devnull}\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(empty))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    # And an empty Claude Code configuration directory, so a test never reads
    # the machine's settings.json. With the output style selected there, the
    # session-start hook correctly sends nothing, which made hook tests fail
    # against the machine rather than the code (the same trap as the git ignore file above).
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("no-claude-dir")))

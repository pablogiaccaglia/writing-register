"""One sandboxed way to ask a model a question.

Three implementations of this existed across three repositories and had already
drifted. Each had learned something the others had not, and all of it is here.

**The prompt goes on stdin, never in argv.** Passed as an argument it can cross
the operating system's single-argument ceiling; the spawn then fails with
"argument list too long" and the stage returns nothing. Observed live at 143 KB.

**The sandbox is structural, not requested.** A permitted-tools list is a
permission list, not a capability restriction: measured, a call with no flag,
with an empty list, and with a single read tool named all reported the same MCP
servers present. Only an empty MCP config removes them. A read-only guarantee
written into a prompt is a request; this is the version the process enforces.

**The working directory is neutral.** Project skills, settings and CLAUDE.md are
found by walking up from the working directory, so spawning inside a project
turns a text synthesis into an agent carrying that project's instructions.

**The environment is an allowlist.** The parent carries service credentials the
child does not need, and inherited CLAUDE_* markers make a nested run behave as a
sub-agent of its parent.

**Empty output is a failure.** Silent success is the failure mode this keeps
hitting.

**A read-only child sees an export, not the repository.** The checker that
verifies a rewrite against the code (since 2026-09-15) gets `--safe-mode`,
so no CLAUDE.md, skill, hook or plugin reaches it; `--tools Read,Grep,Glob`,
which removes the other tools rather than denying them (measured: the child
lists exactly those three); `--permission-prompts none`; and `--add-dir` on an
export of the repository's tracked files under the neutral directory, so a
git-ignored `.env` or a folder of real transcripts is never readable. Variadic
flags such as `--add-dir` swallow a positional prompt, which is one more
reason the prompt goes on stdin. `--output-format json` with `--json-schema`
puts the parsed reply in `structured_output` next to `num_turns`, which counts
2 even for a call that used no tool."""
from __future__ import annotations

import contextlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

BINARY = "claude"
NO_MCP = ("--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}')

# Runtime plumbing only. Anything not here does not reach the child.
ALLOWED_ENV = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TZ", "TMPDIR", "TMP", "TEMP", "XDG_RUNTIME_DIR",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)

DEFAULT_TIMEOUT = 600
READ_TOOLS = "Read,Grep,Glob"
# Whole names of files that hold secrets; never copied into an export and never
# citable (verify.py). Review 2026-09-15.
SECRET_FILE = re.compile(
    r"^(\.env(\..*)?|\.netrc|\.npmrc|\.pypirc|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?"
    r"|credentials(\.json)?|secrets?\.(json|ya?ml|toml|env|txt|ini)|.*\.(pem|key|p12|pfx|keystore|jks))$", re.I)
OVERLAY_MAX_FILES = 2000
OVERLAY_MAX_BYTES = 1_000_000
# Above either cap the export is reduced to the files the caller names.
EXPORT_MAX_FILES = 20_000
EXPORT_MAX_BYTES = 50_000_000


class SpawnFailed(Exception):
    """The call did not produce usable output. Never returns empty on failure."""


@dataclass(frozen=True)
class Answer:
    stdout: str
    command: tuple[str, ...]
    duration_seconds: float


def _neutral_cwd() -> Path:
    d = Path(tempfile.gettempdir()) / "writing-register-spawn"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextlib.contextmanager
def _call_cwd():
    """An empty directory of its own for one child process.

    Audit 2026-09-16: every child ran in the directory that also
    holds the exports, so a second `wr humanize` in another repository put its
    copy of that repository where this child could read it."""
    d = _neutral_cwd() / f"cwd-{os.getpid()}-{secrets.token_hex(3)}"
    d.mkdir(parents=True, exist_ok=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _child_env() -> dict:
    return {k: os.environ[k] for k in ALLOWED_ENV if k in os.environ}


@dataclass
class Spawn:
    """The single place a model is invoked. Nothing else builds this command."""

    runner: object = field(default=None)
    binary: str = BINARY

    def command(self, model: str | None, effort: str | None, read_root=None,
                system_prompt: str | None = None, json_schema: str | None = None) -> list[str]:
        cmd = [self.binary, "-p", *NO_MCP]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        if read_root is not None:
            cmd += ["--safe-mode", "--tools", READ_TOOLS, "--allowedTools", READ_TOOLS,
                    "--permission-prompts", "none", "--add-dir", str(read_root),
                    "--output-format", "json"]
        if system_prompt:
            cmd += ["--system-prompt", system_prompt]
        if json_schema:
            cmd += ["--json-schema", json_schema]
        return cmd

    def run(self, prompt: str, *, model: str | None = None,
            effort: str | None = None, timeout: int = DEFAULT_TIMEOUT,
            read_root=None, system_prompt: str | None = None,
            json_schema: str | None = None) -> Answer:
        cmd = self.command(model, effort, read_root, system_prompt, json_schema)
        runner = self.runner or subprocess.run
        started = time.monotonic()
        try:
            with _call_cwd() as cwd:
                proc = runner(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(cwd),
                    env=_child_env(),
                    start_new_session=True,
                )
        except subprocess.TimeoutExpired as e:
            raise SpawnFailed(f"{self.binary} timed out after {timeout} "
                              f"seconds") from e
        except FileNotFoundError as e:
            raise SpawnFailed(f"{self.binary} is not on PATH") from e
        except OSError as e:
            raise SpawnFailed(f"{self.binary} could not be started ({e})") from e

        if proc.returncode != 0:
            # Whatever it said about why, from wherever it said it. `claude`
            # prints "Not logged in - Please run /login" on stdout with stderr
            # empty, and reading only stderr reported "no stderr" while the
            # reason sat on the screen. Two runs failed undiagnosably that way.
            said = ((proc.stderr or "").strip()
                    or (proc.stdout or "").strip()
                    or "said nothing on stdout or stderr")
            raise SpawnFailed(f"{self.binary} exited {proc.returncode}: "
                              f"{said[-400:]}")
        if not (proc.stdout or "").strip():
            raise SpawnFailed(
                f"{self.binary} exited 0 and produced empty output. Treating "
                f"that as success is how a stage silently does nothing.")
        return Answer(stdout=proc.stdout, command=tuple(cmd),
                      duration_seconds=time.monotonic() - started)


def run(prompt: str, *, runner=None, model: str | None = None,
        effort: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Convenience wrapper returning the text."""
    return Spawn(runner=runner).run(prompt, model=model, effort=effort,
                                    timeout=timeout).stdout


class ExportError(Exception):
    """The repository cannot be exported for the checker. `skip` is true when
    there is simply nothing to check against (no repository, no commits, a
    document outside it), false when exporting failed."""

    def __init__(self, message: str, skip: bool = False):
        super().__init__(message)
        self.skip = skip


def repository_root(path) -> Path:
    """The top of the git repository holding `path`, or ExportError(skip=True)."""
    path = Path(path).resolve()
    start = path if path.is_dir() else path.parent
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                         capture_output=True, text=True, timeout=30)
    if top.returncode != 0:
        raise ExportError("not a git repository", skip=True)
    root = Path(top.stdout.strip()).resolve()
    head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=root,
                          capture_output=True, text=True, timeout=30)
    if head.returncode != 0:
        raise ExportError("the repository has no commits", skip=True)
    return root


def export_tracked(root, overlay=(), notes=None) -> Path:
    """A copy of the repository's tracked files at HEAD, plus the working-tree
    text of the `overlay` paths, under the neutral directory.

    Only tracked files, so nothing git ignores is readable: a repository's
    ignored `out/` can hold private data and its `.env` holds keys. The overlay is for the
    document being checked and its sources, which may be uncommitted. Over the
    caps, or when git cannot list or archive the repository, the export holds
    only the directories of the `overlay` paths, and a line saying why is added
    to `notes` for the caller to report. Remove it with `remove_export`."""
    # Review 2026-09-15: `git archive HEAD` run in a subdirectory
    # archives only that subdirectory, so the export starts at the top.
    root = repository_root(root)
    for p in overlay:
        if not Path(p).resolve().is_relative_to(root):
            raise ExportError(f"{p} is outside the repository", skip=True)
    target = _neutral_cwd() / f"export-{os.getpid()}-{secrets.token_hex(3)}"
    target.mkdir(parents=True)
    try:
        _fill_export(root, target, overlay, notes if notes is not None else [])
    except BaseException:
        # Review 2026-09-15: a failure used to leave the half-made export behind.
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def _scrub(target: Path) -> None:
    """Remove from the export what the checker must never read.

    Symlinks, because `git archive` keeps a tracked symlink and it can point
    anywhere on the machine (review 2026-09-15). Files named like
    secrets, because the archive extracts a committed `.env` like any other
    tracked file, and both guides say such a file is left out (audit
    2026-09-16). Running this before the working-tree copies also
    stops a path the archive holds as a symlink and the working tree holds as a
    regular file from being copied through that symlink, writing outside the
    export (found in a review)."""
    for dirpath, dirnames, filenames in os.walk(target):
        for name in dirnames + filenames:
            entry = Path(dirpath) / name
            if entry.is_symlink():
                entry.unlink()
            elif entry.is_file() and SECRET_FILE.match(name):
                entry.unlink()


def _fill_export(root: Path, target: Path, overlay, notes) -> None:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, timeout=60)
    files = [f for f in listed.stdout.split(b"\0") if f] if listed.returncode == 0 else []
    why = ""
    if listed.returncode != 0:
        why = "git could not list the repository's files"
    elif len(files) > EXPORT_MAX_FILES:
        why = f"the repository tracks more than {EXPORT_MAX_FILES:,} files"
    whole = False
    if not why:
        archive = subprocess.run(["git", "archive", "HEAD"], cwd=root, capture_output=True, timeout=120)
        if archive.returncode != 0:
            why = "git could not archive the repository"
        elif len(archive.stdout) > EXPORT_MAX_BYTES:
            why = f"the tracked files are larger than {EXPORT_MAX_BYTES // 1_000_000} MB"
        else:
            subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout, check=True, timeout=120)
            whole = True
    # Audit 2026-09-16: this used to archive nothing at all over a
    # cap, so the checker judged the document against an empty directory.
    if not whole:
        paths = sorted({str(Path(p).resolve().relative_to(root).parent) for p in overlay
                        if Path(p).resolve().is_relative_to(root)})
        if paths:
            reduced = subprocess.run(["git", "archive", "HEAD", "--", *paths], cwd=root,
                                     capture_output=True, timeout=120)
            if reduced.returncode == 0:
                subprocess.run(["tar", "-x", "-C", str(target)], input=reduced.stdout, check=True, timeout=120)
        where = ", ".join(p for p in paths if p != ".") or "the repository's top level"
        notes.append(f"{why}, so the checker read only {where}")
    _scrub(target)
    # Review 2026-09-15: the checker read committed code only, so a
    # document written for code not yet committed was judged against the old
    # code. Edited and new files (not ignored, not secrets) replace the archive's,
    # and files deleted in the working tree leave the export.
    edited = subprocess.run(["git", "diff", "--name-only", "-z", "HEAD"], cwd=root,
                            capture_output=True, timeout=60)
    new_files = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root,
                               capture_output=True, timeout=60)
    working = [f.decode("utf-8", "surrogateescape") for out in (edited, new_files) if out.returncode == 0
               for f in out.stdout.split(b"\0") if f][:OVERLAY_MAX_FILES]
    for rel in working:
        source, dest = root / rel, target / rel
        if not source.exists() and not source.is_symlink():
            dest.unlink(missing_ok=True)
            continue
        if (source.is_symlink() or not source.is_file() or SECRET_FILE.match(source.name)
                or source.stat().st_size > OVERLAY_MAX_BYTES):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    for p in overlay:
        p = Path(p).resolve()
        if p.is_file() and p.is_relative_to(root):
            dest = target / p.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
    _scrub(target)


def remove_export(path) -> None:
    path = Path(path)
    if path.is_relative_to(_neutral_cwd()) and path.exists():
        shutil.rmtree(path, ignore_errors=True)

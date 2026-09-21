"""The plugin's hooks: wr rewrites what Claude writes, when the user asked for it.

Since 2026-09-15 wr can run on the commit messages, PR descriptions and markdown
files Claude produces. Each hook reads Claude Code's hook JSON on stdin and
prints the hook JSON reply, or nothing.

Nothing happens unless the user's configuration lists the kind of text in
`auto`, and nothing happens in a scripted `claude -p` run, whose entrypoint
starts with "sdk": wr humanize itself starts one, and so can any other tool
that drives Claude from a script. A rewrite that fails its checks, a model call that fails, and a command
shape that cannot be rewritten safely all leave Claude's text exactly as it was.

The hooks never grant permission. A rewritten command goes through the same
permission rules as the original (verified 2026-09-15 with `claude -p`).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from . import metrics
from .config import ConfigError, config_path, load_config
from .voice import read_core
from .humanize import SetupError, _lf, _read, humanize_text
from .passages import humanize_passages, new_unit_keys, unit_keys
from .spawn import SpawnFailed

# A hook blocks Claude while it runs; a short text takes about 14 seconds.
HOOK_TIMEOUT = 90

# The shapes Claude used in 173 commits and 99 PR commands found in the session
# logs on 2026-09-15. A message inside a quoted heredoc is literal text; a
# double-quoted message is accepted only without `$` or backticks, which the
# shell would expand.
_M_HEREDOC = re.compile(
    r"""(?<![\w-])-[a-zA-Z]*m\s+"\$\(cat <<'(?P<tag>\w+)'\n(?P<body>.*?)\n(?P=tag)\n\s*\)\"""", re.S)
_F_HEREDOC = re.compile(
    r"""(?<![\w-])-F\s+-\s+<<'(?P<tag>\w+)'[^\n]*\n(?P<body>.*?)\n(?P=tag)(?=\n|$)""", re.S)
_M_QUOTED = re.compile(r"""(?<![\w-])-[a-zA-Z]*m\s+"(?P<body>(?:[^"\\$`]|\\["\\])*)\"""")
_BODY_HEREDOC = re.compile(
    r"""--body\s+"\$\(cat <<'(?P<tag>\w+)'\n(?P<body>.*?)\n(?P=tag)\n\s*\)\"""", re.S)
_BODY_QUOTED = re.compile(r"""--body\s+"(?P<body>(?:[^"\\$`]|\\["\\])*)\"""")
_QUOTED = (_M_QUOTED, _BODY_QUOTED)

# Audit 2026-09-15: the words `git commit` inside a PR body, or a `-m "..."`
# belonging to `git tag` or sitting inside single quotes, were taken for the
# message to rewrite. The command is now read on a skeleton in which heredoc
# bodies and quoted text are blanked, so only real command words count.
_WORD = r"(?:[^\s\"']|\"[^\"]*\"|'[^']*')+"
_GIT_COMMIT = re.compile(
    r"(?:^|[;&|(\n])\s*git(?:\s+(?:-[cC]\s+" + _WORD + r"|--[\w-]+(?:=" + _WORD + r")?))*"
    r"\s+commit(?![\w-])")
_GH_PR = re.compile(r"(?:^|[;&|(\n])\s*gh\s+pr\s+(?:create|edit)(?![\w-])")
_SEGMENT_END = re.compile(r"[;&|\n]")
_HEREDOC_MARK = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1")
_KINDS = (("commit", _GIT_COMMIT, (_M_HEREDOC, _F_HEREDOC, _M_QUOTED), "commit message"),
          ("pr", _GH_PR, (_BODY_HEREDOC, _BODY_QUOTED), "PR description"))


def _skeleton(command: str) -> str:
    """The command with heredoc bodies, quoted text and comments replaced by spaces.

    It has the same length as the command, so a position in one is the same
    position in the other. It reads the command once, keeping a stack of the
    quotes and command substitutions it is inside, because `"$(echo "$X")"`
    nests quotes. A heredoc marker counts outside single quotes, as in
    `-m "$(cat <<'EOF'`, and its body starts on the next line. A shape this
    does not understand blanks too much, which leaves the command alone."""
    chars = list(command)
    n = len(command)
    stack, pending, i = [], [], 0

    def quoted():
        return '"' in stack or "'" in stack

    while i < n:
        c = command[i]
        top = stack[-1] if stack else None
        if c == "\n" and pending and top != "'":
            if quoted():
                chars[i] = " "
            start = i + 1
            for tag in pending:
                end = re.compile(rf"^[ \t]*{re.escape(tag)}[ \t]*$", re.M).search(command, start)
                stop = end.end() if end else n
                chars[start:stop] = " " * (stop - start)
                start = stop
            pending, i = [], start
            continue
        if top == "'":
            if c == "'":
                stack.pop()
            if c != "'" or quoted():
                chars[i] = " "
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            # An escaped character is text; an escaped newline continues the
            # command, so it must not end the command's segment.
            if quoted() or command[i + 1] == "\n":
                chars[i] = chars[i + 1] = " "
            i += 2
            continue
        mark = _HEREDOC_MARK.match(command, i) if c == "<" else None
        if mark and not command.startswith("<<<", i):
            pending.append(mark.group(2))
            if quoted():
                chars[i:mark.end()] = " " * (mark.end() - i)
            i = mark.end()
            continue
        if command.startswith("$(", i) and not command.startswith("$((", i):
            if quoted():
                chars[i] = chars[i + 1] = " "
            stack.append("(")
            i += 2
            continue
        if top == '"':
            if c == '"':
                stack.pop()
            if c != '"' or quoted():
                chars[i] = " "
            i += 1
            continue
        if c == ")" and top == "(":
            stack.pop()
        elif c == "#" and not quoted() and (i == 0 or command[i - 1] in " \t\n;&|()"):
            stop = command.find("\n", i)
            stop = n if stop < 0 else stop
            chars[i:stop] = " " * (stop - i)
            i = stop
            continue
        elif c in "'\"":
            if quoted():
                chars[i] = " "
            stack.append(c)
            i += 1
            continue
        if quoted():
            chars[i] = " "
        i += 1
    return "".join(chars)


def _owned(command: str, skeleton: str, keyword, patterns) -> list:
    """The message options that belong to a `keyword` command: inside that
    command's own segment and not inside quotes or a heredoc body."""
    segments = []
    for k in keyword.finditer(skeleton):
        end = _SEGMENT_END.search(skeleton, k.end())
        segments.append((k.end(), end.start() if end else len(skeleton)))
    return [(p, m) for p in patterns for m in p.finditer(command)
            if skeleton[m.start()] != " " and any(a <= m.start() < b for a, b in segments)]


def _scripted() -> bool:
    return os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk")


def _settings():
    """The user's configuration, the core of their voice, and what went wrong.

    A configuration that cannot be read gives (None, "", reason); a voice file
    that cannot be read gives (cfg, "", reason). The reason is empty otherwise."""
    try:
        cfg = load_config()
    except ConfigError as e:
        return None, "", str(e)
    if not cfg.voice:
        return cfg, "", ""
    try:
        return cfg, read_core(cfg.voice), ""
    except (ConfigError, OSError, UnicodeDecodeError) as e:
        return cfg, "", f"the voice {cfg.voice} cannot be read: {e}"


def _unescape(body: str) -> str:
    return re.sub(r'\\(["\\])', r"\1", body)


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace('"', '\\"')
                .replace("$", "\\$").replace("`", "\\`"))


def pre_bash(payload: dict, spawn=None) -> dict | None:
    """Rewrite the message of a `git commit` or the body of `gh pr create/edit`."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str):
        return None
    skeleton = _skeleton(command)
    candidates = []
    for kind, keyword, patterns, label in _KINDS:
        found = _owned(command, skeleton, keyword, patterns)
        if len(found) == 1:
            candidates.append((kind, label, *found[0]))
    if not candidates:
        return None
    cfg, voice, _ = _settings()
    usable = [c for c in candidates if cfg is not None and c[0] in cfg.auto]
    if not usable:
        return None
    # Audit follow-up, 2026-09-15: a command holding both a commit and a PR used
    # to get only the commit rewritten. Every usable message is rewritten, the
    # model calls run side by side, and the replacements are applied from the
    # end of the command so the earlier positions stay valid.
    with ThreadPoolExecutor(max_workers=len(usable)) as pool:
        outcomes = list(pool.map(lambda c: _rewrite_message(c, voice, spawn), usable))
    notes = [note for _, _, note in outcomes if note]
    edits = sorted(((m.start("body"), m.end("body"), new) for m, new, _ in outcomes
                    if new is not None), reverse=True)
    reply = {"systemMessage": "; ".join(notes)} if notes else {}
    if edits:
        for begin, finish, new in edits:
            command = command[:begin] + new + command[finish:]
        updated = dict(tool_input)
        updated["command"] = command
        reply["hookSpecificOutput"] = {"hookEventName": "PreToolUse", "updatedInput": updated}
    return reply or None


def _rewrite_message(candidate, voice: str, spawn):
    """Rewrite one message found in a command.

    Returns the match, the replacement text for its body or None, and a note
    for the user or ""."""
    kind, label, pattern, m = candidate
    quoted = pattern in _QUOTED
    text = _unescape(m.group("body")) if quoted else m.group("body")
    if not text.strip():
        return m, None, ""
    try:
        result = humanize_text(text + "\n", kind=kind, spawn=spawn, voice=voice,
                               timeout=HOOK_TIMEOUT)
    except (SpawnFailed, SetupError) as e:
        return m, None, f"wr left the {label} as written: {e}"
    if result.refused:
        return m, None, f"wr kept the {label} as written: {result.refused}"
    _record_message(kind, text, result)
    if not result.changed:
        return m, None, ""
    new = result.text.rstrip("\n")
    if quoted:
        new = _escape(new)
    elif any(line.strip() == m.group("tag") for line in new.split("\n")):
        return m, None, (f"wr kept the {label} as written: the rewrite has a line that "
                         "would end the heredoc early")
    return m, new, f"wr rewrote the {label} in {result.seconds:.0f}s"


# Markdown. A rewrite takes minutes, so the edit hook only records the file, the
# end-of-turn hook (async in the plugin) rewrites what the turn touched, and the
# next prompt tells Claude what changed so it reads the file again.

STATE_ENV = "WR_STATE_DIR"
_EDIT_TOOLS = {"Write", "Edit", "MultiEdit"}
_SKIPPED_DIRS = {"vendor", "node_modules", "tests", "test", "fixtures", "testdata", ".claude"}


def _state_dir(session, create: bool = False) -> Path | None:
    """The folder holding one session's queue and notes, or None without a
    session id. The id is hashed into the folder name: the audit on 2026-09-15
    wrote outside the state folder with a session id of `..`."""
    if not isinstance(session, str) or not session:
        return None
    base = os.environ.get(STATE_ENV) or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache"),
        "writing-register", "auto")
    d = Path(base) / hashlib.sha256(session.encode("utf-8", "surrogatepass")).hexdigest()[:32]
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


# How long to wait before deciding that a conflicting change was not a Claude
# edit: Claude Code runs the edit hook a moment after its Write tool finishes.
_SETTLE_SECONDS = 2.0
_EDIT_LOG_DAYS = 1


@contextmanager
def _locked(d: Path):
    """Hold the session folder's lock while reading or changing its files.

    Audit follow-up, 2026-09-15: one hook renamed the queue or the notes while
    another could still be appending to them, which lost the line. The lock is
    held only around those short reads and writes, never during a model call."""
    d.mkdir(parents=True, exist_ok=True)
    with (d / "lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _take(path: Path) -> str:
    """Read a state file and remove it, so two hooks never handle it twice."""
    taken = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}")
    try:
        path.rename(taken)
    except OSError:
        return ""
    try:
        return taken.read_text(encoding="utf-8")
    finally:
        taken.unlink(missing_ok=True)


def _git(args: list[str], cwd: Path):
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _eligible(path: Path) -> tuple[Path | None, str]:
    """The repository root when the file may be rewritten, and a note for Claude.

    Skipped without a note: files that are gone, not markdown, refused rewrites,
    changelogs, files under a folder in `_SKIPPED_DIRS`, files outside a git
    repository and files git ignores. Skipped with a note: a repository that does not ignore
    *.refused.md, where a refusal would leave an untracked file behind."""
    if (not path.is_file() or path.suffix != ".md" or path.name.endswith(".refused.md")
            or path.name.upper().startswith("CHANGELOG")):
        return None, ""
    top = _git(["rev-parse", "--show-toplevel"], path.parent)
    if top is None or top.returncode != 0:
        return None, ""
    root = Path(top.stdout.strip()).resolve()
    try:
        rel = path.resolve().relative_to(root)
    except ValueError:
        return None, ""
    # Audit 2026-09-15: a golden file under tests/fixtures was rewritten, which
    # breaks a byte-exact test; files under .claude/ are instructions for agents.
    if _SKIPPED_DIRS.intersection(rel.parts[:-1]):
        return None, ""
    ignored = _git(["check-ignore", "-q", "--", str(rel)], root)
    if ignored is None or ignored.returncode == 0:
        return None, ""
    refused = rel.with_name(f"{path.stem}.refused.md")
    covered = _git(["check-ignore", "-q", "--no-index", "--", str(refused)], root)
    if covered is None or covered.returncode != 0:
        return None, (f"wr did not rewrite {path} because {root} does not ignore "
                      f"*.refused.md; add that line to its .gitignore")
    return root, ""


# What Claude wrote. Review 3, 2026-09-15: the first design guessed Claude's
# text by comparing snapshots of the file across turns, and the guess failed
# when the background rewrite of one turn finished after the next turn's edit,
# or when someone typed after Claude's last edit. The edit hooks now record, at
# the moment of each of Claude's own edits, the sentences and list items that
# edit added; the end-of-turn rewrite sends only recorded text. Text typed in
# an editor, written by a shell command or present before Claude touched the
# file is never recorded, so it is never rewritten.

_MAX_ATTEMPTS = 3


def _record_name(path) -> str:
    return hashlib.sha256(str(path).encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _before_file(d: Path, path) -> Path:
    return d / "before" / _record_name(path)


def _units_file(d: Path, path) -> Path:
    return d / "claude" / f"{_record_name(path)}.json"


def _load_units(d: Path, path) -> dict:
    """Claude's recorded, not yet handled units of a file: key -> attempts."""
    try:
        data = json.loads(_units_file(d, path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): int(n) for key, n in data.items()} if isinstance(data, dict) else {}


def _save_units(d: Path, path, units: dict) -> None:
    target = _units_file(d, path)
    if units:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(units, ensure_ascii=False), encoding="utf-8")
    else:
        target.unlink(missing_ok=True)


def _markdown_edit(payload: dict):
    """The session folder and resolved path of a Write, Edit or MultiEdit of a
    markdown file when the user turned the markdown rewrite on, or None."""
    if payload.get("tool_name") not in _EDIT_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(path, str) or not path.endswith(".md"):
        return None
    cfg, _, _ = _settings()
    if cfg is None or "markdown" not in cfg.auto:
        return None
    d = _state_dir(payload.get("session_id"), create=True)
    return None if d is None else (d, Path(path).resolve())


def pre_edit(payload: dict, spawn=None) -> None:
    """Save a markdown file's text right before one of Claude's edits.

    2026-09-15: the end-of-turn rewrite used to send the whole file every
    turn, so untouched paragraphs drifted and a word Claude changed on request
    could be changed back. With the text before and after each edit, the edit
    hook knows exactly what Claude added. A file that does not exist yet saves
    as empty: all of it is Claude's."""
    found = _markdown_edit(payload)
    if found is None:
        return None
    d, target = found
    try:
        text = _lf(_read(target))
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError):
        return None
    with _locked(d):
        before = _before_file(d, target)
        before.parent.mkdir(parents=True, exist_ok=True)
        before.write_text(text, encoding="utf-8")
    return None


def post_edit(payload: dict, spawn=None) -> None:
    """Queue a markdown file Claude wrote or edited, and record what it added."""
    found = _markdown_edit(payload)
    if found is None:
        return None
    d, target = found
    line = str(target)
    with _locked(d):
        with (d / "queue").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        # The edit log is never consumed: it tells a rewrite that finds its file
        # changed whether Claude edited it again, see `_rewrite_one`.
        with (d / "edits").open("a", encoding="utf-8") as f:
            f.write(f"{time.time():.6f}\t{line}\n")
        before_file = _before_file(d, target)
        if not before_file.exists():
            return None
        before = before_file.read_text(encoding="utf-8")
        before_file.unlink()
        try:
            after = _lf(_read(target))
        except UnicodeDecodeError:
            note = f"wr cannot rewrite {target}: it is not UTF-8"
            done = d / "done"
            if note not in (done.read_text(encoding="utf-8") if done.exists() else ""):
                with done.open("a", encoding="utf-8") as f:
                    f.write(note + "\n")
            return None
        except OSError:
            return None
        units = _load_units(d, target)
        for key in new_unit_keys(before, after, set(units)):
            units.setdefault(key, 0)
        _save_units(d, target, units)
    return None


def stop(payload: dict, spawn=None) -> None:
    """Rewrite the markdown files this turn touched, once each."""
    d = _state_dir(payload.get("session_id"))
    if d is None or not d.is_dir():
        return None
    started = time.time()
    with _locked(d):
        queued = _take(d / "queue")
        _prune_edits(d, started - _EDIT_LOG_DAYS * 86400)
    paths = list(dict.fromkeys(line.strip() for line in queued.splitlines() if line.strip()))
    if not paths:
        return None
    cfg, voice, _ = _settings()
    if cfg is None or "markdown" not in cfg.auto:
        return None
    for raw in paths:
        path = Path(raw)
        try:
            note = _rewrite_one(path, spawn, voice, d, started)
        except Exception as e:
            # Audit 2026-09-15: one unreadable file used to end the loop, and
            # the files queued after it were dropped without a word.
            note = f"wr could not rewrite {path}: {e}"
        if note:
            # Written per file, so the files already handled still reach
            # Claude if this hook is stopped halfway.
            with _locked(d), (d / "done").open("a", encoding="utf-8") as f:
                f.write(note + "\n")
    return None


def _rewrite_one(path: Path, spawn, voice: str, d: Path, started: float) -> str:
    """Rewrite what Claude wrote in one queued file and return the note, or ""."""
    with _locked(d):
        units = _load_units(d, path)
    if not units:
        return ""
    if not path.exists():
        with _locked(d):
            _save_units(d, path, {})
        return ""
    root, note = _eligible(path)
    if root is None:
        # A skipped file starts fresh: once the repository ignores
        # *.refused.md, only prose Claude adds from then on is rewritten.
        with _locked(d):
            _save_units(d, path, {})
        return note
    r = humanize_passages(path, keys=set(units), spawn=spawn, voice=voice)
    _settle_units(d, path, r)
    _record_rewrite(path, r)
    if r.written:
        return _rewritten_note(path, r)
    if r.conflict and _edited_since(d, path, started):
        # Claude edited the file again while it was being rewritten. That edit
        # is queued, so the newer version is rewritten after the turn that made
        # it, and the stale rewrite is not worth keeping.
        if r.kept:
            r.kept.unlink(missing_ok=True)
        return (f"wr did not rewrite {path}: Claude edited it again while wr was "
                "rewriting it, so the newer version is rewritten after that turn ends")
    if r.refused:
        kept = f"; its rewrite is kept in {r.kept}" if r.kept else ""
        return f"wr kept {path} as written ({r.refused}){kept}"
    return ""


def _record_message(kind: str, text: str, result) -> None:
    """Keep one line about a commit message or pull request description."""
    cfg, _, _ = _settings()
    if cfg is None or not getattr(cfg, "metrics", True):
        return
    metrics.append({"event": "message", "kind": kind, "words_sent": len(text.split()),
                    "words_changed": len(result.text.split()) if result.changed else 0,
                    "seconds": round(result.seconds, 1), "refused": result.refused})


def _record_rewrite(path: Path, r) -> None:
    """Keep one line about this rewrite, so the share of Claude's own prose wr
    still has to change can be read later (2026-09-16)."""
    cfg, _, _ = _settings()
    if cfg is None or not getattr(cfg, "metrics", True):
        return
    sent = sum(len(old.split()) for old, _ in getattr(r, "pairs", []) or [])
    changed = sum(len(new.split()) for old, new in getattr(r, "pairs", []) or []
                  if " ".join(old.split()) != " ".join(new.split()))
    metrics.append({"event": "passages", "file": path.name, "passages": r.passages,
                    "pairs": len(getattr(r, "pairs", []) or []), "words_sent": sent,
                    "words_changed": changed, "seconds": round(r.seconds, 1),
                    "refused": r.refused, "written": bool(r.written)})


_NOTE_PAIRS = 20
_NOTE_CHARS = 400


def _rewritten_note(path: Path, r) -> str:
    """The note for Claude after an end-of-turn rewrite: every passage it changed,
    old and new, so Claude checks each still says what it meant.

    2026-09-15: the note used to say only which file changed. A rewrite can
    shift a sentence's meaning without breaking any string check, and Claude,
    who wrote the sentence, is the one who can tell. From a later review: each
    side is quoted with its inner quotes escaped, a long
    passage is cut around the first difference, and a rewrite that changed only
    spacing says so instead of promising a list."""
    import json as _json

    head = (f"wr rewrote {r.passages} passage{'s' if r.passages != 1 else ''} in {path} in "
            f"{r.seconds:.0f}s")
    if not r.pairs:
        return head + "; it changed only spacing."

    def window(old: str, new: str) -> tuple[str, str]:
        old, new = " ".join(old.split()), " ".join(new.split())
        first = next((i for i, (a, b) in enumerate(zip(old, new)) if a != b), min(len(old), len(new)))
        start = max(0, first - _NOTE_CHARS // 3)

        def cut(text):
            piece = text[start:start + _NOTE_CHARS]
            return ("..." if start else "") + piece + ("..." if start + _NOTE_CHARS < len(text) else "")
        return cut(old), cut(new)

    lines = [head + ". Each passage it changed is below, old -> new; check that each still says "
             "what you meant, and fix any that does not:"]
    for old, new in r.pairs[:_NOTE_PAIRS]:
        a, b = window(old, new)
        lines.append(f"  {_json.dumps(a, ensure_ascii=False)} -> {_json.dumps(b, ensure_ascii=False)}")
    if len(r.pairs) > _NOTE_PAIRS:
        lines.append(f"  and {len(r.pairs) - _NOTE_PAIRS} more; read the file")
    return "\n".join(lines)


def _settle_units(d: Path, path: Path, r) -> None:
    """Update Claude's recorded units after a rewrite.

    The end-of-turn hook runs in the background and can finish after the next
    turn has recorded more units, so it reloads the record under the lock and
    removes only the units it sent. A reply that broke the format keeps them
    for another try, up to _MAX_ATTEMPTS; after a conflict they stay for the
    next turn; otherwise they are done. Units no longer in the file are dropped."""
    with _locked(d):
        units = _load_units(d, path)
        for key in r.sent_keys:
            if key not in units:
                continue
            if r.retry:
                units[key] += 1
                if units[key] >= _MAX_ATTEMPTS:
                    del units[key]
            elif not r.conflict:
                del units[key]
        try:
            present = unit_keys(_lf(_read(path)))
            units = {key: n for key, n in units.items() if key in present}
        except (OSError, UnicodeDecodeError):
            units = {}
        _save_units(d, path, units)


def _edited_since(d: Path, path: Path, since: float) -> bool:
    """Whether the edit log records an edit of `path` at or after `since`,
    waiting once for an edit hook that may still be starting."""
    for attempt in range(2):
        with _locked(d):
            try:
                log = (d / "edits").read_text(encoding="utf-8")
            except OSError:
                log = ""
        for line in log.splitlines():
            stamp, _, logged = line.partition("\t")
            try:
                if logged == str(path) and float(stamp) >= since:
                    return True
            except ValueError:
                continue
        if attempt == 0:
            time.sleep(_SETTLE_SECONDS)
    return False


def _prune_edits(d: Path, before: float) -> None:
    """Drop edit-log lines older than `before`. The caller holds the lock."""
    log = d / "edits"
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    keep = []
    for line in lines:
        try:
            if float(line.partition("\t")[0]) >= before:
                keep.append(line)
        except ValueError:
            continue
    log.write_text("".join(k + "\n" for k in keep), encoding="utf-8")


def prompt(payload: dict, spawn=None) -> dict | None:
    """Tell Claude which files wr rewrote since its last turn."""
    d = _state_dir(payload.get("session_id"))
    if d is None or not d.is_dir():
        return None
    with _locked(d):
        done = _take(d / "done")
    if not done.strip():
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": ("Since your last message, wr ran in the background on the "
                              "markdown files you wrote. Read a file again before "
                              "editing it.\n" + done)}}


# Session start. Moved here from the user's own settings on 2026-09-15, so the
# voice reaches every interactive session wherever the plugin is installed.

_AUTO_LABELS = (("commit", "commit messages"), ("pr", "PR descriptions"),
                ("markdown", "markdown files"))


# Claude Code shows the model at most about 10,000 characters of a hook's
# additionalContext; above that it saves the text to a file and shows a 2KB
# preview (largest delivered whole in the transcripts: 9.7KB; smallest persisted:
# 9.9KB). The voice is longer, so the plugin registers the session and subagent
# hooks PARTS times, and each registration sends one part (2026-09-21).
PART_LIMIT = 9000
PARTS = 4
_REST = ("The rest of this context did not fit in what Claude Code delivers to a hook; "
         "run `wr voice --core` to read the whole voice.")


def _pieces(text: str, limit: int) -> list[str]:
    """Sections, then paragraphs, then hard cuts, each at most `limit`."""
    out = []
    for section in re.split(r"\n\n(?=## )", text):
        if len(section) <= limit:
            out.append(section)
            continue
        for para in section.split("\n\n"):
            while len(para) > limit:
                cut = para.rfind(" ", 0, limit)
                cut = cut if cut > 0 else limit
                out.append(para[:cut])
                para = para[cut:].lstrip()
            out.append(para)
    return [p for p in out if p.strip()]


def split_parts(text: str, limit: int = PART_LIMIT) -> list[str]:
    """The text in consecutive parts of at most `limit` characters, cut where a
    section starts when possible, otherwise between paragraphs."""
    parts, current = [], ""
    for piece in _pieces(text, limit):
        joined = f"{current}\n\n{piece}" if current else piece
        if len(joined) <= limit:
            current = joined
        else:
            parts.append(current)
            current = piece
    if current:
        parts.append(current)
    return parts


def _part(text: str, part: int | None) -> str | None:
    """Part `part` of `text`, labelled, or the whole text when no part is asked."""
    if part is None:
        return text
    parts = split_parts(text)
    if len(parts) > PARTS:
        # Keep the label and the pointer within the limit of the last part.
        head = split_parts("\n\n".join(parts[:PARTS - 1]))[:PARTS - 1]
        tail = "\n\n".join(parts[PARTS - 1:])
        parts = head + [tail[:PART_LIMIT - len(_REST) - 2] + "\n\n" + _REST]
    if part > len(parts):
        return None
    return (f"(This context arrives in {len(parts)} parts, from {len(parts)} hooks; this is "
            f"part {part} of {len(parts)}. Read all of them together.)\n\n{parts[part - 1]}")


def session_start(payload: dict, spawn=None, part: int | None = None) -> dict | None:
    """Give Claude the voice core and say what wr rewrites automatically."""
    cfg, voice, problem = _settings()
    # Audit 2026-09-15: a mistake in the configuration used to switch every
    # automatic rewrite and the voice off without telling anyone.
    notice = {}
    if cfg is None:
        if not problem:
            return None
        head = "wr: " if str(config_path()) in problem else f"wr cannot use {config_path()}: "
        return {"systemMessage": f"{head}{problem}. The voice and the automatic "
                                 "rewrites are off until it is fixed; `wr voice` shows "
                                 "the error again."}
    if problem:
        notice = {"systemMessage": f"wr: {problem}. This session has no voice."}
    parts = []
    # When the voice and the patterns are already in the system prompt as an
    # output style, sending them again here would put them in twice.
    from .patterns import card
    from .style import active as style_active, plain_active
    cwd = payload.get("cwd")
    if style_active(cwd):
        voice = ""
        patterns_card = ""
    elif plain_active(cwd):
        patterns_card = ""
    else:
        patterns_card = card()
    if voice.strip():
        parts.append(
            "Write all prose in this session in the voice below: replies in this chat, "
            "documentation, READMEs, reports, cards, commit messages, pull request "
            "descriptions and team messages. The patterns after it are the machine-writing "
            "patterns to avoid; where they and the voice disagree, the voice wins. This "
            f"voice is turned on in {config_path()}.")
    elif patterns_card:
        parts.append(
            "Write all prose in this session, in this chat and in the files you write, so "
            "it reads as a person wrote it. No voice is set, so follow the patterns below.")
    auto = [label for key, label in _AUTO_LABELS if key in cfg.auto]
    if auto:
        parts.append(
            f"wr rewrites these automatically: {', '.join(auto)}. A commit message or PR "
            "description is rewritten before its command runs. A markdown file is "
            "rewritten after your turn ends, prose only, adding no facts, and the next "
            "prompt lists each passage it changed, old and new, so you can check that "
            "each still says what you meant. Write them as usual.")
    if voice.strip():
        parts.append(voice)
    if patterns_card:
        parts.append(patterns_card)
    if part not in (None, 1):
        notice = {}
    text = _part("\n\n".join(parts), part) if parts else None
    if not text:
        return notice or None
    return {**notice, "hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": text}}


def subagent_start(payload: dict, spawn=None, part: int | None = None) -> dict | None:
    """Give a subagent the same voice the session has.

    2026-09-16. The session-start hook does not reach a subagent, and the
    corpus shows the difference: across 5,654 transcripts, after the voice
    began reaching every session, the main conversation writes 0.40 em or en
    dashes per 1,000 words and a subagent 6.57, against 1.58 in what the
    person types themselves, with parenthetical glosses at 3.08 against 13.62. Subagents
    write the reports, reviews and answers that reach people, so they are given
    the voice on the event Claude Code fires when one starts."""
    from .patterns import card
    cfg, voice, _ = _settings()
    if cfg is None:
        return None
    patterns_card = card()
    if not voice.strip() and not patterns_card:
        return None
    head = ("Write whatever a person will read so it reads as a person wrote it: the "
            "documents, code comments and docstrings you write, and any commit message or "
            "pull request text. The report you return goes to the agent that called you, "
            "not to a person, so keep that plain, dense and literal and spend no words on "
            "register there.")
    if voice.strip():
        head += " Follow the voice below; where it and the patterns disagree, the voice wins."
    body = _part("\n\n".join(p for p in (head, voice.strip(), patterns_card) if p), part)
    if not body:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": body}}


HANDLERS = {"session-start": session_start, "subagent-start": subagent_start, "pre-bash": pre_bash, "pre-edit": pre_edit, "post-edit": post_edit, "stop": stop, "prompt": prompt}


def run(event: str, stdin, out, spawn=None, part: int | None = None) -> int:
    """Answer one hook event. Always exits 0: a hook must never break Claude's work."""
    handler = HANDLERS.get(event)
    if handler is None or _scripted():
        return 0
    try:
        payload = json.load(stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        reply = handler(payload, spawn=spawn, **({"part": part} if part else {}))
    except Exception as e:
        # Audit 2026-09-15: a reviewer found payloads and files that made a
        # handler raise, so `wr hook` exited 1 with a traceback. The failure is
        # shown to the user instead of being swallowed.
        reply = {"systemMessage": f"wr hook {event} failed: {type(e).__name__}: {e}"}
    if reply:
        out.write(json.dumps(reply, ensure_ascii=False) + "\n")
    return 0

"""One pass, the humanizer's way.

The rewrite follows the humanizer skill, vendored unchanged from
github.com/blader/humanizer into `vendor/humanizer/`, in one model call per
file. When the user has turned on a voice, the voice says what the result
should read like and wins where it differs from the skill; without one
the skill works alone.

A model call is the expensive part and a string comparison is not, so what is
checked is only what costs nothing. The rewrite is refused and the file left
alone when it invents a number, changes code, inline code or a link target, or
cuts the document to under half its length. The skill's own file mode already
asks for the first three; these checks make sure it did.

With a root, the call also gets the files the document links to or names by
path, and may add background taken from them. A first pass over one
repository's docs on 2026-09-14 read well but was short of background: the
prompt forbade any fact the document did not state, and the background lives in
the code and the other docs. The checks widen to those files and no further.
"""
from __future__ import annotations

import collections
import ctypes
import ctypes.util
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .resources import SKILL

DEFAULT_TIMEOUT = 600

GUT_FLOOR = 0.5

# A long source is cut rather than skipped: its opening usually says what it is.
SOURCE_CHARS = 40_000
SOURCES_CHARS = 160_000
# Only text a reader of the repository would read. `.env` names a path too, and
# it holds secrets that must never go into a prompt.
SOURCE_SUFFIXES = {".md", ".py", ".js", ".mjs", ".ts", ".sh", ".toml", ".yaml",
                   ".yml", ".json", ".txt", ".html", ".css"}

_NUMBER = re.compile(r"\d[\d.,]*")


def _invented_numbers(replacement: str, source: str) -> list[str]:
    known = {n.rstrip(".,") for n in _NUMBER.findall(source)}
    return [n.rstrip(".,") for n in _NUMBER.findall(replacement)
            if n.rstrip(".,") not in known]


_FENCE = re.compile(r"^```.*?^```", re.M | re.S)
# A code span opens with a run of backticks and closes with a run of the same
# length, may wrap onto the next line, and never crosses a blank line. The first
# pattern here, one backtick to the next on the same line, paired every backtick
# after a wrapped span differently in the original and the rewrite and invented
# "lost" code (EXTENSION.md, 2026-09-14).
_SPAN = re.compile(r"(?<!`)(`+)(?!`)((?:(?!\n\s*\n).)+?)(?<!`)\1(?!`)", re.S)


def _inline_spans(text: str) -> list[str]:
    """Inline code contents, whitespace normalised, fenced blocks excluded."""
    return [" ".join(m.group(2).split()) for m in _SPAN.finditer(_FENCE.sub("", text))]


_WORD = re.compile(r"\w+")


def _words_in_one_paragraph(span: str, text: str) -> bool:
    """Whether every word of a span appears together in one paragraph of text."""
    words = {w.lower() for w in _WORD.findall(span)}
    if not words:
        return False
    for paragraph in re.split(r"\n\s*\n", _FENCE.sub("", text)):
        if words <= {w.lower() for w in _WORD.findall(paragraph)}:
            return True
    return False


_LINK = re.compile(r"\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
_PATH_SPAN = re.compile(r"^[\w.-]+(?:/[\w.-]+)+$")


def _slug(heading: str) -> str:
    """The anchor a markdown renderer gives a heading."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).strip().replace(" ", "-")


def _local_file(target: str, base: Path | None) -> Path | None:
    """The file a relative link points at, when it exists."""
    target = target.split("#", 1)[0]
    if base is None or not target or "://" in target or target.startswith("mailto:"):
        return None
    candidate = (base / target).resolve()
    return candidate if candidate.is_file() else None


class SetupError(Exception):
    """wr cannot find the files it reads from its own clone."""


def load_skill() -> str:
    """The vendored humanizer skill, exactly as upstream ships it.

    It is read from the clone, or from the copy an installed package carries
    (resources.py). When neither has it the installation is incomplete; the
    audit on 2026-09-14 found that the first rewrite then died with a bare
    traceback, so the reason and the fix are given instead."""
    try:
        return SKILL.read_text(encoding="utf-8")
    except OSError as e:
        raise SetupError(
            f"the humanizer skill is not at {SKILL}, so this installation of wr is "
            f"incomplete. Reinstall it with `uv tool install --force "
            f"git+https://github.com/pablogiaccaglia/writing-register`, or run "
            f"install.sh in a clone") from e


def _git_ignored(root: Path, rels: list[str]) -> set[str] | None:
    """The paths among `rels` that git ignores under root, an empty set outside
    a git repository, and None when git could not be asked.

    Audit 2026-09-16: every failure used to mean "nothing is
    ignored", so a git that was missing, slow or broken sent the files this
    filter exists to keep out of the prompt. The caller sends nothing rather
    than guessing."""
    if not rels:
        return set()
    try:
        proc = subprocess.run(["git", "-C", str(root), "check-ignore", "--stdin"],
                              input="\n".join(rels) + "\n", capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode in (0, 1):      # some are ignored, none are
        return set(proc.stdout.splitlines())
    if proc.returncode == 128:         # not a git repository: no rules to apply
        return set()
    return None


def gather_sources(path, root, notes=None) -> dict[str, str]:
    """The local files a document links to or names by path, keyed by their
    path under root, in the order the document first mentions them.

    When git cannot say which files it ignores, no source is sent and the
    reason is added to `notes` for the caller to report."""
    path, root = Path(path).resolve(), Path(root).resolve()
    text = path.read_text(encoding="utf-8")
    found: list[Path] = []
    for target in _LINK.findall(text):
        f = _local_file(target, path.parent)
        if f:
            found.append(f)
    for span in _inline_spans(text):
        if _PATH_SPAN.match(span):
            found += [f for f in ((root / span).resolve(), (path.parent / span).resolve())
                      if f.is_file()][:1]
    candidates: dict[str, Path] = {}
    for f in found:
        try:
            rel = f.relative_to(root).as_posix()
        except ValueError:
            continue
        if f != path and f.suffix in SOURCE_SUFFIXES:
            candidates.setdefault(rel, f)
    # A file git ignores is runtime data, not documentation. The audit on
    # 2026-09-14 found one repository's docs naming private runtime data under
    # out/, such as real transcripts, all ignored, all about to be sent.
    ignored = _git_ignored(root, list(candidates))
    if ignored is None:
        if notes is not None:
            notes.append("git could not say which files it ignores, so the document's "
                         "sources were not sent")
        return {}
    sources: dict[str, str] = {}
    budget = SOURCES_CHARS
    for rel, f in candidates.items():
        if rel in ignored or budget <= 0:
            continue
        body = f.read_text(encoding="utf-8", errors="replace")[:min(SOURCE_CHARS, budget)]
        sources[rel] = body
        budget -= len(body)
    return sources


_WRAPPED = re.compile(r"\A\s*```[a-zA-Z]*\n(.*)\n```\s*\Z", re.S)


@dataclass
class Result:
    path: Path
    text: str
    written: bool = False
    refused: str = ""
    seconds: float = 0.0
    kept: Path | None = None
    sources: list[str] = field(default_factory=list)
    # Lines about a run that was less than it looks: the sources were not sent,
    # or the checker read only part of the repository (audit 2026-09-16).
    notes: list[str] = field(default_factory=list)
    # True when the rewrite was refused because the file changed underneath it.
    conflict: bool = False
    # How many changed passages a passage rewrite found (see passages.py).
    passages: int = 0
    # True when the reply broke the expected format, so the same passages are
    # worth sending again next turn (see hooks._rewrite_one).
    retry: bool = False
    # The keys of the sentences and list items a passage rewrite sent.
    sent_keys: set = field(default_factory=set)
    # Since 2026-09-15: whether the checker verified the rewrite against the
    # code, its answer, the sentence-level changes, and the (Change, Verdict)
    # pairs whose blocks were put back.
    checked: bool = False
    # Why the rewrite was not checked when it had nothing to check against.
    check_skipped: str = ""
    # True when the check refused the rewrite: the kept copy still holds the
    # sentences the checker rejected or never judged.
    kept_unchecked: bool = False
    check_seconds: float = 0.0
    verification: object = None
    changes: list = field(default_factory=list)
    reverted: list = field(default_factory=list)
    # The (old, new) text of each passage an end-of-turn rewrite changed.
    pairs: list = field(default_factory=list)


# What the model is told about a piece of text that is not a document file.
KIND_NOTES = {
    "commit": ("The document is a git commit message. Its first line is the subject: "
               "keep it a single plain line and keep any conventional prefix such as "
               "fix(render): exactly as it is. Then a blank line, then the body. Do "
               "not add headings, bullet lists or sign-offs that were not there."),
    "pr": ("The document is a pull request description in markdown. Keep its "
           "headings and checklists, and rewrite the prose under them."),
    "prose": "",
}

_COMPUTED = ("A number you work out from the document's own numbers counts as a "
             "new number: a sum, a difference, a percentage or a unit conversion "
             "such as 900 seconds into 15 minutes. Leave the numbers as the "
             "document states them.")


def build_prompt(document: str, skill: str, voice: str = "",
                 sources: dict[str, str] | None = None, kind: str = "file") -> str:
    mode = "file mode" if kind == "file" else "embedded mode"
    keep = ("Follow the humanizer skill below: its section How to work is the "
            "process, its numbered patterns are what to look for, and its section "
            f"When not to act says what to leave alone. Use its {mode}. Keep "
            "code blocks, inline code, commands, paths, link targets, tables of "
            "data and front matter exactly as they are.")
    facts = _facts(sources)
    note = KIND_NOTES.get(kind, "")
    parts = [
        "Rewrite the document at the end so it reads as a person wrote it.",
        "", keep + " " + facts + (" " + note if note else ""), "",
        "Reply with the final document only: no preamble, no summary, no list "
        "of changes, no code fence around it.",
    ]
    return _assemble(parts, document, skill, voice, sources)


def _facts(sources: dict[str, str] | None) -> str:
    """What the model may add, which depends on whether it has sources."""
    if sources:
        return ("The reader is a competent engineer on the team who did not build "
                "this part and has not read its code. Where the document assumes "
                "something that reader would not know, such as what a component "
                "does, why it exists, or what a term means, add that background "
                "briefly where the reader first needs it. Take every added "
                "fact from the sources at the end, which are the files the "
                "document links to or names. Do not add a fact, name, number, "
                "date or claim that neither the document nor the sources state, "
                "and do not copy whole passages of a source. You may link to a "
                "source by its path relative to the document. " + _COMPUTED)
    return ("Change prose only. Do not add a fact, name, number, date or "
            "claim that is not already in the document. " + _COMPUTED)


def _assemble(parts: list[str], document: str, skill: str, voice: str,
              sources: dict[str, str] | None) -> str:
    """The instructions, then the voice, the skill, the document and its sources."""
    parts = list(parts)
    if voice:
        parts += ["", "# The voice to write in, which wins where it differs from "
                  "the skill", "", voice]
    parts += ["", "# The humanizer skill", "", skill,
              "", "# The document", "", document]
    for rel, body in (sources or {}).items():
        parts += ["", f"# Source: {rel}", "", body]
    return "\n".join(parts)


def _unwrap(reply: str) -> str:
    m = _WRAPPED.match(reply)
    text = m.group(1) if m else reply.strip("\n")
    return text + "\n"


def check(old: str, new: str, sources: str = "", base: Path | None = None, *,
          floor: float = GUT_FLOOR, unit: str = "the document", anchors: str | None = None) -> str:
    """Why a rewrite must not be written, or "" if it may.

    `sources` is the text of the files the call was given: a name or number the
    rewrite adds may come from there. `base` is the document's directory: a new
    link may point at a file that exists there. `floor` is the shortest share of
    the original a rewrite may keep, and `unit` names the text in the reason.
    `anchors` is the text whose headings a new `#link` may point at, when
    `new` is only part of a document; by default it is `new` itself."""
    where = "the document or its sources" if sources else "the document"
    if not new.strip():
        return "the reply was empty"
    if len(new) < len(old) * floor:
        share = "half" if floor == 0.5 else "a quarter of" if floor == 0.25 else f"{floor:.0%} of"
        return (f"the rewrite is {len(new)} characters against {len(old)}, under "
                f"{share} {unit}; that is a cut, not a rewrite")
    if _FENCE.findall(old) != _FENCE.findall(new):
        return "a code block changed, and the rewrite may change prose only"
    # A span lost or altered is a code change. A new span is prose when it names
    # something the document already names: on 2026-09-14 USAGE.md's rewrite
    # mentioned two commands the document documents, and a check that compared
    # the two sets exactly threw away 115 seconds of rewrite for it.
    before = collections.Counter(_inline_spans(old))
    after = collections.Counter(_inline_spans(new))
    flat_known = " ".join((old + "\n" + sources).split())
    flat_new = " ".join(new.split())
    # A span whose name is still in the rewrite lost its backticks, not its code:
    # on 2026-09-14 two architecture documents were refused for writing one of
    # two mentions of a function name without them.
    # A span rewritten as prose keeps its words together: on 2026-09-14 the
    # a retention guide was refused three times for turning "`owner =
    # retention` rows" into "rows whose owner is `retention`".
    lost = sorted(span for span in (before - after).elements()
                  if span not in flat_new and not _words_in_one_paragraph(span, new))
    if lost:
        return (f"inline code was lost or altered: "
                f"{', '.join('`' + x + '`' for x in lost[:5])}; the "
                f"rewrite may change prose only")
    invented = sorted({span for span in after - before if span not in flat_known})
    if invented:
        return (f"the rewrite introduces inline code {where} never mentions: "
                f"{', '.join('`' + x + '`' for x in invented[:5])}")
    # A link lost or altered is refused. A new link is prose when it points at a
    # heading in the rewrite (CONTEXT_PROMPTS.md, 2026-09-14, naming the section
    # a sentence pointed at) or at a file that exists (ARCHITECTURE.md the same
    # day, linking four real docs). A new link to a missing file or a URL could
    # be invented.
    links_before = collections.Counter(_LINK.findall(old))
    links_after = collections.Counter(_LINK.findall(new))
    lost_links = sorted((links_before - links_after).elements())
    if lost_links:
        return (f"a link target was lost or altered: {', '.join(lost_links[:5])}; "
                f"the rewrite may change prose only")
    if isinstance(anchors, (set, frozenset)):
        headings = anchors
    else:
        headings = {_slug(h) for h in _HEADING.findall(new if anchors is None else anchors)}
    new_links = sorted({t for t in links_after - links_before
                        if not (t.startswith("#") and t[1:] in headings)
                        and not _local_file(t, base)})
    if new_links:
        return (f"the rewrite adds a link the document never had: "
                f"{', '.join(new_links[:5])}")
    # A link target is checked above, so its digits are not new numbers: a link
    # to a second "Setup" heading is `#setup-1` (review 3, 2026-09-15).
    invented = _invented_numbers(re.sub(r"\]\([^)]*\)", "]()", new), old + "\n" + sources)
    if invented:
        return (f"the rewrite introduces {', '.join(sorted(set(invented)))}, which "
                f"is not in {where}")
    return ""


# Put back more blocks than a document allows, and the rewrite is refused whole
# (2026-09-15): what is left is not the rewrite. The allowance is the
# smaller of two limits, half the changed blocks and `_revert_cap` below.
_MAX_REVERTED_BLOCKS = 8
# Past the floor the cap is one changed block in this many.
_REVERTED_SHARE = 4
# The half rule needs this many changed blocks: with one or two, a single
# rejected sentence is "over half" and the rewrite could never be partly kept
# (review 2026-09-15).
_HALF_RULE_MIN_BLOCKS = 3


def _revert_cap(changed_blocks: int) -> int:
    """How many blocks may go back before the whole rewrite is refused.

    A flat eight refused a long document at a low rate of rejection
    (2026-09-16, "scale properly"): one repository's operations guide changes 63
    blocks in a rewrite, so nine rejected blocks, 14% of them, threw away a
    rewrite that was 86% good, while a six-block document could lose every
    block but two and still be written. Past 32 changed blocks the cap grows
    with the document, and the half rule governs the shorter ones."""
    return max(_MAX_REVERTED_BLOCKS, -(-changed_blocks // _REVERTED_SHARE))


def _string_checks(*args, **kwargs) -> str:
    """`check`, under a name `humanize`'s `check` flag does not hide."""
    return check(*args, **kwargs)


def humanize(path, *, spawn=None, voice: str = "", write: bool = True,
             model: str | None = None, timeout: int = DEFAULT_TIMEOUT,
             root=None, sources: bool = True, check: bool = True,
             check_timeout: int | None = None, check_effort: str | None = "high") -> Result:
    """Rewrite one file in one call, then verify it against the code.

    With `root`, the files the document links to or names by path go into the
    call as sources (unless `sources` is false), and after the string checks
    pass the checker verifies every sentence the rewrite added or changed
    against an export of the repository's tracked files (unless `check` is
    false). Blocks holding a sentence the checker contradicted or could not
    verify are put back; see `_verify_against_code`."""
    from .spawn import Spawn
    path = Path(path)
    raw = _read(path)
    old = _lf(raw)
    notes: list[str] = []
    source_texts = gather_sources(path, root, notes=notes) if root is not None and sources else {}
    spawn = spawn or Spawn()
    started = time.monotonic()
    answer = spawn.run(build_prompt(old, load_skill(), voice, source_texts), model=model,
                       timeout=timeout)
    new = _unwrap(getattr(answer, "stdout", answer) or "")
    result = Result(path=path, text=new, seconds=time.monotonic() - started,
                    sources=list(source_texts))
    result.refused = _string_checks(old, new, "\n".join(source_texts.values()),
                                    base=path.resolve().parent)
    rewrite = new
    if check and root is not None and not result.refused and new != old:
        _verify_against_code(result, path, Path(root), old, new, source_texts, spawn,
                             model, check_timeout, check_effort)
        new = result.text
    # When wr runs by itself at the end of a turn (since 2026-09-15), the model
    # call takes minutes and Claude may edit the file again meanwhile; writing the
    # rewrite over that edit would lose it.
    if write and not result.refused and new != old:
        if _write_unless_changed(path, raw, _with_eol(new, raw)):
            result.written = True
        else:
            result.conflict = True
            result.refused = ("the file changed while it was being rewritten, so the "
                              "rewrite was not written over those changes")
    if write and result.refused and rewrite.strip():
        # The rewrite took minutes and usually differs from an acceptable one in
        # a single place. Throwing it away made a refusal cost the whole run.
        result.kept = path.with_name(f"{path.stem}.refused{path.suffix}")
        _write(result.kept, _with_eol(rewrite, raw))
        # Every kept copy is one the checks refused, whether the string checks
        # stopped it before the checker ran or the checker rejected it. Saying
        # "it has not passed the check" is true of both (found in an audit).
        result.kept_unchecked = True
    result.notes += notes
    return result


def _verify_against_code(result: Result, path: Path, root: Path, old: str, new: str,
                         source_texts: dict, spawn, model, check_timeout, check_effort) -> None:
    """Verify the rewrite's added and changed sentences, and put back the blocks
    that hold one the checker contradicted or could not verify.

    2026-09-15: in one repository a rewrite added sentences that were false and
    passed every string check. The checker reads an export of the tracked files. A run it did not do refuses the rewrite; so
    do more blocks going back than the document allows (`_revert_cap`)."""
    from .changes import RevertUnsafe, blocks_to_revert, changes_between, claims, revert_blocks
    from .spawn import ExportError, export_tracked, remove_export
    from .verify import CHECK_TIMEOUT, verify_claims

    changes = changes_between(old, new)
    result.changes = changes
    found = claims(changes)
    if not found:
        result.checked = True
        return
    overlay = [path] + [root / rel for rel in source_texts if (root / rel).is_file()]
    export = None
    try:
        export = export_tracked(root, overlay, notes=result.notes)
        verification = verify_claims(new, found, export, spawn=spawn, model=model,
                                     timeout=check_timeout or CHECK_TIMEOUT, original=old,
                                     effort=check_effort)
    except ExportError as e:
        if e.skip:
            result.check_skipped = str(e)
            return
        result.refused = f"the checker could not run ({e}), so the rewrite is not written"
        return
    except (OSError, subprocess.SubprocessError) as e:
        # Review 2026-09-15: this used to be a traceback that lost the rewrite.
        result.refused = f"the checker could not run ({e}), so the rewrite is not written"
        return
    finally:
        if export is not None:
            remove_export(export)
    result.checked = True
    result.verification, result.check_seconds = verification, verification.seconds
    if verification.did_not_check:
        result.refused = f"the checker did not check ({verification.why}), so the rewrite is not written"
        return
    failing = verification.failing()
    if not failing:
        return
    result.reverted = [(c, verification.by_number[c.number]) for c in found if c.number in failing]
    targets = blocks_to_revert(changes, failing)
    changed_blocks = {c.new_block for c in found if c.new_block >= 0}
    allowed = _revert_cap(len(changed_blocks))
    if len(changed_blocks) >= _HALF_RULE_MIN_BLOCKS:
        allowed = min(allowed, len(changed_blocks) // 2)
    if len(targets) > allowed:
        # The blocks put back can outnumber the blocks the rewrite changed (an
        # added block is put back by removing it), so the message states both
        # counts rather than one "of" the other (found in an audit).
        result.refused = (f"the checker contradicted or could not verify sentences in {len(targets)} "
                          f"blocks, more than the {allowed} this document may put back "
                          f"({len(changed_blocks)} blocks changed), so the rewrite is not written")
        return
    try:
        reverted = revert_blocks(new, old, changes, failing)
    except RevertUnsafe as e:
        result.refused = (f"the rewrite reshaped the text around a sentence the checker rejected ({e}), "
                          "so it cannot be put back in part")
        return
    again = _string_checks(old, reverted, "\n".join(source_texts.values()), base=path.resolve().parent)
    result.text = reverted
    if again:
        result.refused = f"after putting back the blocks the checker rejected, {again}"


_RENAME_SWAP = 0x2       # renamex_np flag on macOS
_RENAME_EXCHANGE = 0x2   # renameat2 flag on Linux
_AT_FDCWD = -100


def _swap(a: Path, b: Path) -> bool:
    """Exchange the names of two files in one atomic step.

    Returns False where the system offers no such call, and the caller falls
    back to a plain write."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
            done = libc.renamex_np(os.fsencode(a), os.fsencode(b), ctypes.c_uint(_RENAME_SWAP))
        elif hasattr(libc, "renameat2"):
            done = libc.renameat2(_AT_FDCWD, os.fsencode(a), _AT_FDCWD, os.fsencode(b),
                                  ctypes.c_uint(_RENAME_EXCHANGE))
        else:
            return False
    except (OSError, AttributeError, TypeError):
        return False
    return done == 0


def _read(path) -> str:
    """The file's exact text, line endings included.

    Review 2, 2026-09-15: reading in text mode turned CRLF into LF, and the
    rewrite was written back with LF, so every line of a CRLF file showed up
    in the diff."""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write(path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def _with_eol(text: str, like: str) -> str:
    """`text` (LF) with the line ending most of `like`'s lines use."""
    return text.replace("\n", "\r\n") if like.count("\r\n") * 2 > like.count("\n") else text


def _write_unless_changed(path: Path, old: str, new: str) -> bool:
    """Replace the file's text with `new` only if it still holds `old`.

    Audit follow-up, 2026-09-15: reading the file and then writing it lost an
    edit landing between the two. Claude Code's Write and Edit tools rename a new
    file into place (the inode changes), and that is exactly such an edit. The
    rewrite now goes into a temp file that is swapped with the document in one
    step; what comes out of the swap is whatever was really there at that
    instant, including a file renamed into place or one still being written
    through an open descriptor. If it is not `old`, the swap is undone."""
    target = Path(path).resolve()
    if _read(target) != old:
        return False
    tmp = target.with_name(f".{target.name}.wr-{os.getpid()}.tmp")
    _write(tmp, new)
    try:
        os.chmod(tmp, stat.S_IMODE(target.stat().st_mode))
        if not _swap(tmp, target):
            _write(target, new)
            return True
        if _read(tmp) == old:
            return True
        if not _swap(tmp, target):
            os.replace(tmp, target)
        return False
    finally:
        tmp.unlink(missing_ok=True)


_TEXT_FLOORS = {"commit": (0.25, "the message"), "pr": (0.25, "the description")}


@dataclass
class TextResult:
    text: str
    refused: str = ""
    seconds: float = 0.0
    changed: bool = False


_TRAILER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: \S")
_PREFIX = re.compile(r"^[a-z]+(\([^)]*\))?!?: ")
_FOOTER = "\U0001F916 Generated with"


def _split_commit(message: str) -> tuple[str, str]:
    """The message without its trailer block, and the trailer block verbatim.

    Trailers (Co-Authored-By, Claude-Session, Signed-off-by) are the lines after
    the last blank line when every one of them has the "Key: value" shape."""
    lines = message.rstrip("\n").split("\n")
    blanks = [i for i, line in enumerate(lines) if not line.strip()]
    if blanks and blanks[-1] > 0:
        tail = lines[blanks[-1] + 1:]
        if tail and all(_TRAILER.match(line) for line in tail):
            return "\n".join(lines[:blanks[-1]]).rstrip("\n"), "\n".join(tail) + "\n"
    return message.rstrip("\n"), ""


def _split_pr(body: str) -> tuple[str, str]:
    """The body without the generated-with footer, and the footer verbatim."""
    at = body.find(_FOOTER)
    if at < 0:
        return body.rstrip("\n"), ""
    footer = body[at:]
    return body[:at].rstrip("\n"), footer if footer.endswith("\n") else footer + "\n"


def humanize_text(text: str, *, kind: str = "prose", spawn=None, voice: str = "",
                  model: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> TextResult:
    """Rewrite a piece of text in one call: a commit message, a PR body or prose.

    The parts the model must not touch (commit trailers, the PR footer) are cut
    off before the call and put back verbatim. A refused rewrite returns the
    original text unchanged."""
    from .spawn import Spawn
    if kind == "commit":
        core, tail = _split_commit(text)
    elif kind == "pr":
        core, tail = _split_pr(text)
    else:
        core, tail = text.rstrip("\n"), ""
    spawn = spawn or Spawn()
    started = time.monotonic()
    answer = spawn.run(build_prompt(core + "\n", load_skill(), voice, kind=kind),
                       model=model, timeout=timeout)
    new_core = _unwrap(getattr(answer, "stdout", answer) or "").rstrip("\n")
    result = TextResult(text=text, seconds=time.monotonic() - started)
    if kind == "commit" and new_core:
        lines = new_core.split("\n")
        # A trial rewrite on 2026-09-15 lost the blank line git needs here.
        if len(lines) > 1 and lines[1].strip():
            lines.insert(1, "")
        new_core = "\n".join(lines)
        prefix = _PREFIX.match(core)
        if prefix and not new_core.startswith(prefix.group(0)):
            result.refused = (f"the subject lost its conventional prefix "
                              f"{prefix.group(0).strip()}")
            return result
    # Audit follow-up, 2026-09-15: live, the document floor refused a commit
    # whose body was all filler. On 14 real commit messages no rewrite came out
    # shorter than the original, so a commit or PR may shrink to a quarter.
    floor, unit = _TEXT_FLOORS.get(kind, (GUT_FLOOR, "the text"))
    refused = check(core + "\n", new_core + "\n", floor=floor, unit=unit)
    if refused:
        result.refused = refused
        return result
    rebuilt = new_core + ("\n\n" + tail if tail else "\n")
    # Audit 2026-09-15: a reply equal to the text but for its trailing newline
    # was reported as a rewrite.
    changed = rebuilt.rstrip("\n") != text.rstrip("\n")
    result.text, result.changed = (rebuilt if changed else text), changed
    return result

#!/usr/bin/env python3
"""Pull Claude's own prose, and what the person said about it, out of the
Claude Code transcripts.

Steering Claude's writing starts from how Claude writes before anything steers
it. The transcripts hold three kinds of text that are easy to confuse, so each is
taken by its own discriminator:

- a reply: an `assistant` record's content blocks of type `text` (thinking
  blocks are not prose the user reads, and are dropped);
- a file Claude wrote: an `assistant` record's `tool_use` block for Write,
  Edit or MultiEdit, taking `content`, `new_string`, or every `new_string` of
  a MultiEdit, which is what Claude wrote rather than what the file holds;
- what the person typed: a `user` record whose content is a string, whose
  `promptSource` says a person typed it and whose `origin.kind` is `human`.
  Without that filter the machine's own injected prompts outnumber the
  person's words about twenty to one. A prompt the person accepted from
  Claude's own suggestion carries `suggested: true`: those are Claude's
  words, and must never be quoted as the person's.

Five more kinds are off unless `--kinds` asks for them. They are the places
where the person reacted to what Claude wrote:

- `publish`: a page Claude published with the Artifact tool, with the label
  it gave that version and the url the result returned; `refused` when the
  publish failed;
- `comment`: a comment the owner left on a published page, read from the
  comment blocks an ArtifactComments (or Artifact) call returns. Only rows the
  block itself marks as the owner's are taken. Everything a viewer wrote sits
  on lines opened by the block's nonce, so a viewer who types something that
  looks like an owner row is still read as viewer text;
- `answer`: an AskUserQuestion answer the person typed instead of picking an
  option, and the notes they added to an option;
- `rejection`: the reason the person typed when they rejected a tool call;
- `edit`: an old and a new text, from Edit, MultiEdit, Write, and from the
  Python heredocs that edit files through Bash (read with `ast`, never run).

Every record carries where it came from: `uuid`, `parent`, the session and
message it was first written in (`origin_session`, `origin_uuid`, which a
forked session's copied lines keep pointing at), and `src`, the transcript
file and the byte offset of its line. The same message seen twice, in a fork
or in a resumed session, is written once.

Transcripts are read a line at a time; the largest are over 170 MB.
Read-only, no model calls. Run with --sample to work on part of the corpus.
"""
import argparse
import ast
import datetime
import json
import pathlib
import random
import re
import sys
import tomllib
import warnings

ROOT = pathlib.Path.home() / ".claude" / "projects"
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
TYPED = {"typed", "queued", "suggestion_accepted"}
MARKDOWN = {".md", ".markdown"}
OLD_KINDS = {"reply", "markdown", "file", "human"}
NEW_KINDS = {"publish", "comment", "answer", "rejection", "edit"}
KINDS = OLD_KINDS | NEW_KINDS
DEFAULT_KINDS = "reply,markdown,human"
RESEND_WINDOW = datetime.timedelta(hours=1)

# Counts of things the parser met and could not read, printed with the summary.
STATS = {"bad_lines": 0, "heredoc_unparsed": 0, "comment_blocks": 0, "comment_blocks_unclosed": 0}


def main_kinds_default():
    return set(DEFAULT_KINDS.split(","))


# ---------- selection ----------

def transcripts(root=ROOT):
    """Every transcript, including the subagent ones a level deeper."""
    return sorted(root.glob("*/*.jsonl")) + sorted(root.glob("*/*/subagents/*.jsonl"))


def _session_of(path):
    """The session a transcript belongs to: its own name, or for a subagent
    transcript the session directory it sits under."""
    return path.parent.parent.name if path.parent.name == "subagents" else path.stem


def _project(root, slug, sessions=None):
    folder = pathlib.Path(root) / slug
    files = sorted(folder.glob("*.jsonl")) + sorted(folder.glob("*/subagents/*.jsonl"))
    if sessions:
        files = [f for f in files if any(_session_of(f).startswith(s) for s in sessions)]
    return files


def select(root=ROOT, projects=None, sessions=None, config=None):
    """The transcripts to read. `projects` are directory slugs under the root,
    `sessions` are session ids or their prefixes, and `config` is what
    load_config returned, which names slugs each with its own session filter."""
    root = pathlib.Path(root)
    chosen = []
    if config:
        for entry in config["transcripts"]:
            chosen += _project(root, entry["slug"], entry.get("sessions") or sessions)
    if projects:
        for slug in projects:
            chosen += _project(root, slug, sessions)
    if not config and not projects:
        chosen = transcripts(root)
        if sessions:
            chosen = [f for f in chosen if any(_session_of(f).startswith(s) for s in sessions)]
    seen, unique = set(), []
    for f in chosen:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique


def load_config(path):
    """Read mine.toml: `[[transcripts]]` entries with a `slug` and optional
    `sessions`, and a `[window]` with `since` and `until` (dates, inclusive)."""
    data = tomllib.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    window = data.get("window") or {}
    return {
        "transcripts": [{"slug": t["slug"], "sessions": list(t.get("sessions") or [])}
                        for t in data.get("transcripts") or []],
        "since": str(window.get("since") or ""),
        "until": str(window.get("until") or ""),
        "repositories": data.get("repositories") or [],
    }


# ---------- reading ----------

def _lines(path):
    """(byte offset, parsed row) for every user or assistant line, one line in
    memory at a time. A line that does not parse, such as the half-written last
    line of a live session, is skipped and counted."""
    offset = 0
    with open(path, "rb") as fh:
        for raw in fh:
            start = offset
            offset += len(raw)
            if b'"user"' not in raw and b'"assistant"' not in raw:
                continue
            try:
                row = json.loads(raw)
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                STATS["bad_lines"] += 1
                continue
            if isinstance(row, dict) and row.get("type") in ("user", "assistant"):
                yield start, row


def _base(row, path, offset, session, source):
    forked = row.get("forkedFrom") if isinstance(row.get("forkedFrom"), dict) else {}
    uuid = row.get("uuid") or ""
    return {
        "session": session,
        "ts": row.get("timestamp") or "",
        "source": source,
        "uuid": uuid,
        "parent": row.get("parentUuid") or "",
        "origin_session": forked.get("sessionId") or row.get("sessionId") or session,
        "origin_uuid": forked.get("messageUuid") or uuid,
        "src": {"file": str(path), "offset": offset},
    }


def _record(base, kind, path="", text="", **extra):
    rec = {"session": base["session"], "ts": base["ts"], "kind": kind, "source": base["source"],
           "path": path, "text": text}
    rec.update({k: v for k, v in base.items() if k not in rec})
    rec.update(extra)
    return rec


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text") or "" for b in content if isinstance(b, dict))
    return ""


def _written(block):
    """The text a write tool call was about to put in a file, and its path."""
    data = block.get("input") or {}
    path = data.get("file_path") or ""
    if block.get("name") == "Write":
        return path, data.get("content") or ""
    if block.get("name") == "Edit":
        return path, data.get("new_string") or ""
    if block.get("name") == "MultiEdit":
        return path, "\n\n".join(e.get("new_string") or "" for e in data.get("edits") or [])
    return path, ""


def _edit_pairs(block):
    """(method, path, old, new) for each change an edit tool call made."""
    name, data = block.get("name"), block.get("input") or {}
    path = data.get("file_path") or ""
    if name == "Edit":
        yield "Edit", path, data.get("old_string") or "", data.get("new_string") or ""
    elif name == "MultiEdit":
        for e in data.get("edits") or []:
            yield "MultiEdit", path, e.get("old_string") or "", e.get("new_string") or ""
    elif name == "Write":
        yield "Write", path, "", data.get("content") or ""
    elif name == "Bash":
        yield from heredoc_edits(data.get("command") or "")


# ---------- Python heredocs that edit files ----------

_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
_SHELL_SEP = re.compile(r"&&|\|\||[;|]")


def _feeds_python(command, m):
    """True when this heredoc is the input of a python command: `python - <<EOF`
    in the same shell command, or `cat <<EOF | python -`. A commit message on
    a line that ran python earlier (`python check.py && git commit -F - <<EOF`)
    is not."""
    line_start = command.rfind("\n", 0, m.start()) + 1
    if "python" in _SHELL_SEP.split(command[line_start:m.start()])[-1]:
        return True
    rest = m.group(3)
    return "|" in rest and "python" in rest.split("|", 1)[1]


def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_pair(node):
    return (isinstance(node, ast.Tuple) and len(node.elts) == 2
            and all(_const(e) is not None for e in node.elts))


def _default_path(tree):
    """The first literal handed to Path() or open(): the file the script edits."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and _const(node.args[0]) is not None:
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in ("Path", "open", "PurePath"):
                return _const(node.args[0])
    return ""


def heredoc_edits(command):
    """(method, path, old, new) from every Python heredoc in a shell command.

    Read with ast, never executed. Four shapes are taken: the literal arguments
    of `.replace(a, b)`, the pattern and replacement of `re.sub`, string
    constants assigned to names that differ only by `old` and `new`
    (OLD_INTRO, NEW_INTRO), and lists of two-string tuples in a script that
    replaces with variables, the `for old, new in pairs` idiom."""
    for m in _HEREDOC.finditer(command):
        if not _feeds_python(command, m):
            continue
        try:
            with warnings.catch_warnings():
                # an invalid escape in the script is its author's problem, not a line of our output
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(m.group(4))
        except (SyntaxError, ValueError):
            STATS["heredoc_unparsed"] += 1
            continue
        default = _default_path(tree)
        variable_replace = False
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "replace" and len(node.args) >= 2:
                a, b = _const(node.args[0]), _const(node.args[1])
                if a is not None and b is not None:
                    found.append((node.lineno, node.col_offset, "heredoc-replace", default, a, b))
                else:
                    variable_replace = True
            elif (isinstance(func, ast.Attribute) and func.attr == "sub"
                  and isinstance(func.value, ast.Name) and func.value.id == "re" and len(node.args) >= 2):
                a, b = _const(node.args[0]), _const(node.args[1])
                if a is not None and b is not None:
                    found.append((node.lineno, node.col_offset, "heredoc-re.sub", default, a, b))
        pending = {}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and _const(node.value) is not None):
                continue
            name = node.targets[0].id.lower()
            side = "old" if "old" in name else "new" if "new" in name else None
            if side is None:
                continue
            stem = name.replace(side, "", 1)
            other = pending.pop((stem, "new" if side == "old" else "old"), None)
            if other is None:
                pending[(stem, side)] = (node, _const(node.value))
                continue
            first, value = other
            old, new = (value, _const(node.value)) if side == "new" else (_const(node.value), value)
            found.append((first.lineno, first.col_offset, "heredoc-assign", default, old, new))
        if variable_replace:
            in_call = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    where = _const(node.args[0]) if node.args else None
                    for arg in node.args:
                        if isinstance(arg, (ast.List, ast.Tuple)):
                            in_call[id(arg)] = where or default
            for node in ast.walk(tree):
                if (isinstance(node, (ast.List, ast.Tuple)) and node.elts
                        and all(_is_pair(e) for e in node.elts)):
                    where = in_call.get(id(node), default)
                    for e in node.elts:
                        found.append((e.lineno, e.col_offset, "heredoc-pairs", where,
                                      _const(e.elts[0]), _const(e.elts[1])))
        for *_, method, where, old, new in sorted(found, key=lambda f: (f[0], f[1])):
            yield method, where, old, new


# ---------- comment blocks ----------

_BEGIN = re.compile(r"^=== BEGIN ARTIFACT COMMENTS (\S+) ", re.M)
_AUTHOR = re.compile(r"^\[(?P<who>[^\[\]()]+?) \((?P<role>[^()]*)\)(?P<rest>[^\[\]]*?) — "
                     r"(?P<ts>\d{4}-\d\d-\d\dT[\d:]+)\]$")
_MARKERS = {"[on text]": "on_text", "[location]": "location", "[anchored at]": "anchor",
            "[region of]": "region", "[on page]": "page"}


def parse_comment_block(text):
    """Every comment in the ARTIFACT COMMENTS blocks of one tool result.

    Rows the tool emits start at the left margin or are indented without the
    nonce; everything a person wrote is on a line opened by `<nonce>| `. The
    nonce comes from the block's BEGIN line, and the block ends only at an END
    line with the same nonce that is not itself viewer text."""
    out = []
    for begin in _BEGIN.finditer(text):
        STATS["comment_blocks"] += 1
        nonce = begin.group(1)
        end = re.compile(rf"^=== END ARTIFACT COMMENTS {re.escape(nonce)} ===[ \t]*$", re.M)
        body_start = text.find("\n", begin.end())
        if body_start < 0:
            continue
        stop = end.search(text, body_start)
        if stop is None:
            STATS["comment_blocks_unclosed"] += 1
        body = text[body_start + 1: stop.start() if stop else len(text)]
        prefix = nonce + "|"
        thread, comment, last = None, None, None
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped.startswith(prefix):
                words = stripped[len(prefix):]
                words = words[1:] if words.startswith(" ") else words
                if comment is not None:
                    comment["lines"].append(words)
                elif thread is not None and last == "on_text":
                    thread["on_text"] += "\n" + words
                continue
            if line.startswith("Thread "):
                thread = {"thread": line[len("Thread "):].strip(), "on_text": "", "location": "",
                          "anchor": "", "region": "", "page": "", "status": ""}
                comment, last = None, None
                continue
            if thread is None:
                continue
            marker = next((m for m in _MARKERS if stripped.startswith(m)), None)
            if marker:
                last = _MARKERS[marker]
                thread[last] = stripped[len(marker):].strip()
                comment = None
                continue
            author = _AUTHOR.match(stripped)
            if author:
                comment = {"thread": thread, "who": author.group("who").strip(),
                           "role": author.group("role").strip(), "comment_ts": author.group("ts"),
                           "sent": "sent to you" in author.group("rest"), "lines": [],
                           "truncated": False}
                out.append(comment)
                last = "comment"
                continue
            if stripped.startswith("[") and "size cap" in stripped and comment is not None:
                comment["truncated"] = True
                continue
            if stripped and not stripped.startswith("[") and not thread["status"] and "·" in stripped:
                thread["status"] = stripped.split("·")[0].strip()
    result = []
    for c in out:
        t = c["thread"]
        result.append({"thread": t["thread"], "who": c["who"], "role": c["role"],
                       "comment_ts": c["comment_ts"], "sent": c["sent"], "truncated": c["truncated"],
                       "text": "\n".join(c["lines"]).strip("\n"), "on_text": t["on_text"],
                       "location": t["location"], "anchor": t["anchor"], "region": t["region"],
                       "page": t["page"], "status": t["status"]})
    return result


def _is_owner(c):
    return c["who"] == "the user" and c["role"] == "owner"


# ---------- AskUserQuestion and rejections ----------

_SAID = "the user said:\n"
_CLARIFY = "The user wants to clarify these questions"
_ASKED = re.compile(r'^- "(?P<q>.*?)"\n\s+Answer: (?P<a>.*?)(?=\n- "|\Z)', re.S | re.M)
_PAIRS_TEXT = re.compile(r'"(?P<q>[^"]+)"="(?P<a>[^"]*)"')


def _labels(data):
    out = {}
    for q in (data or {}).get("questions") or []:
        out[q.get("question") or ""] = {o.get("label") or "" for o in q.get("options") or []}
    return out


def _typed(answer, labels):
    """True when the answer is not one of the offered labels, nor a comma list of them."""
    answer = (answer or "").strip()
    if not answer:
        return False
    if answer in labels:
        return False
    parts = [p.strip() for p in answer.split(",")]
    return not all(p in labels for p in parts)


def _answers(data, tur, text):
    """(question, answer, note) the person typed, from one AskUserQuestion result."""
    labels = _labels(data)
    every = set().union(*labels.values()) if labels else set()
    if isinstance(tur, dict) and isinstance(tur.get("answers"), dict):
        for q, a in tur["answers"].items():
            if _typed(a, labels.get(q, every)):
                yield q, a.strip(), False
        for q, notes in (tur.get("annotations") or {}).items():
            note = (notes or {}).get("notes") if isinstance(notes, dict) else None
            if note and note.strip():
                yield q, note.strip(), True
    else:
        for m in _PAIRS_TEXT.finditer(text):
            if _typed(m.group("a"), labels.get(m.group("q"), every)):
                yield m.group("q"), m.group("a").strip(), False


# ---------- one transcript ----------

def records(path, kinds=None):
    """The records of one transcript, in line order.

    `source` says whether the text came from the main conversation or from a
    subagent, whose transcripts live a directory deeper. The session-start
    injection does not reach a subagent, so the two are measured apart."""
    kinds = OLD_KINDS if kinds is None else set(kinds)
    path = pathlib.Path(path)
    session = path.stem
    source = "subagent" if path.parent.name == "subagents" else "main"
    calls = {}  # tool_use id -> (name, input, base of the calling line)
    for offset, row in _lines(path):
        base = _base(row, path, offset, session, source)
        message = row.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if row["type"] == "assistant":
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and (block.get("text") or "").strip():
                    if "reply" in kinds:
                        yield _record(base, "reply", text=block["text"])
                elif block.get("type") == "tool_use":
                    name = block.get("name")
                    data = block.get("input") or {}
                    calls[block.get("id")] = (name, data if name in ("Artifact", "ArtifactComments",
                                                                     "AskUserQuestion") else None, base)
                    if name in WRITE_TOOLS:
                        where, text = _written(block)
                        suffix = pathlib.PurePath(where).suffix.lower()
                        kind = "markdown" if suffix in MARKDOWN else "file"
                        if text.strip() and kind in kinds:
                            yield _record(base, kind, path=where, text=text)
                    if "edit" in kinds and name in ("Edit", "MultiEdit", "Write", "Bash"):
                        for method, where, old, new in _edit_pairs(block):
                            if old != new and (old.strip() or new.strip()):
                                yield _record(base, "edit", path=where, text=new, old=old, new=new,
                                              method=method)
        elif row["type"] == "user" and isinstance(content, str):
            origin = row.get("origin") or {}
            prompt = row.get("promptSource")
            if "human" in kinds and prompt in TYPED and origin.get("kind") == "human":
                extra = {"prompt_source": prompt}
                if prompt == "suggestion_accepted":
                    extra["suggested"] = True
                yield _record(base, "human", text=content, **extra)
        elif row["type"] == "user" and isinstance(content, list):
            for block in content:
                if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                    continue
                name, data, call = calls.pop(block.get("tool_use_id"), (None, None, None))
                text = _text_of(block.get("content"))
                tur = row.get("toolUseResult")
                error = bool(block.get("is_error")) or (isinstance(tur, str) and tur.startswith("Error"))
                if "comment" in kinds and "=== BEGIN ARTIFACT COMMENTS" in text:
                    url = (data or {}).get("url") or ""
                    for c in parse_comment_block(text):
                        if _is_owner(c) and c["text"].strip():
                            yield _record(base, "comment", text=c.pop("text"), url=url, tool=name or "", **c)
                if name == "Artifact" and "publish" in kinds and call is not None:
                    data = data or {}
                    if data.get("action", "publish") == "publish" and not data.get("asset"):
                        info = tur if isinstance(tur, dict) else {}
                        url = info.get("url") or ""
                        if not url and not error:
                            m = re.search(r" at (https://\S+) \(Version", text)
                            url = m.group(1) if m else ""
                        yield _record(call, "publish", text=data.get("label") or "",
                                      artifact_id=info.get("artifact_id") or "", seq=info.get("seq"),
                                      title=info.get("title") or "", url="" if error else url,
                                      label=data.get("label") or "", file_path=data.get("file_path") or "",
                                      description=data.get("description") or "", refused=error,
                                      error=text.strip()[:300] if error else "")
                if name == "AskUserQuestion" and "answer" in kinds and not error:
                    for q, a, note in _answers(data, tur, text):
                        extra = {"note": True} if note else {}
                        yield _record(base, "answer", text=a, question=q, **extra)
                if error and _SAID in text:
                    said = text.split(_SAID, 1)[1].strip()
                    if said.startswith(_CLARIFY):
                        if "answer" in kinds and name == "AskUserQuestion":
                            labels = _labels(data)
                            every = set().union(*labels.values()) if labels else set()
                            for m in _ASKED.finditer(said):
                                a = m.group("a").strip()
                                if _typed(a, labels.get(m.group("q"), every)):
                                    yield _record(base, "answer", text=a, question=m.group("q"),
                                                  clarify=True)
                    elif said and "rejection" in kinds:
                        yield _record(base, "rejection", text=said, tool=name or "")
    for name, data, call in calls.values():
        if name == "Artifact" and "publish" in kinds and call is not None:
            data = data or {}
            if data.get("action", "publish") == "publish" and not data.get("asset"):
                yield _record(call, "publish", text=data.get("label") or "", artifact_id="", seq=None,
                              title="", url="", label=data.get("label") or "",
                              file_path=data.get("file_path") or "",
                              description=data.get("description") or "", refused=None,
                              error="no result in the transcript")


# ---------- many transcripts ----------

def _when(stamp):
    try:
        return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _inside(stamp, since, until):
    if not stamp:
        return True
    if since and stamp[:10] < since:
        return False
    if until and stamp[:10] > until:
        return False
    return True


def extract(files, kinds=None, since="", until=""):
    """Records from many transcripts, each written once.

    A record is identified by the message it was first written in and its
    place among that message's records, so a forked or resumed session's
    copy of a line adds nothing. A typed message sent again with the same text
    in the same origin session within an hour counts once. Owner comments are
    read again every time Claude reads the thread, so they are kept apart,
    merged on thread, author and minute, and written last, each with the
    longest text any read returned (a read over the size cap cuts it short)."""
    kinds = main_kinds_default() if kinds is None else set(kinds)
    seen = set()
    last_sent = {}
    comments = {}
    for path in files:
        part_of = {}
        try:
            for rec in records(path, kinds):
                ident = rec["origin_uuid"] or f'{rec["src"]["file"]}:{rec["src"]["offset"]}'
                n = part_of.get((ident, rec["kind"]), 0)
                part_of[(ident, rec["kind"])] = n + 1
                if rec["kind"] == "comment":
                    _keep_comment(comments, rec)
                    continue
                key = (ident, rec["kind"], n)
                if key in seen:
                    continue
                seen.add(key)
                if not _inside(rec["ts"], since, until):
                    continue
                if rec["kind"] == "human":
                    when = _when(rec["ts"])
                    prev = last_sent.get((rec["origin_session"], rec["text"]))
                    last_sent[(rec["origin_session"], rec["text"])] = when
                    if when and prev and abs(when - prev) <= RESEND_WINDOW:
                        continue
                yield rec
        except OSError:
            continue
    for rec in comments.values():
        if _inside(rec["ts"], since, until):
            yield rec


def _keep_comment(comments, rec):
    key = (rec["url"], rec["thread"], rec["who"], rec["comment_ts"])
    n = 0
    while (key + (n,)) in comments:
        have = comments[key + (n,)]
        a, b = have["text"], rec["text"]
        if a.startswith(b) or b.startswith(a):
            if len(b) > len(a) or (have["truncated"] and not rec["truncated"] and len(b) >= len(a)):
                rec["comment_id"] = have["comment_id"]
                rec["reads"] = have["reads"] + 1
                comments[key + (n,)] = rec
            else:
                have["reads"] += 1
            return
        n += 1
    rec["comment_id"] = f'{rec["thread"]}@{rec["comment_ts"]}#{n}'
    rec["reads"] = 1
    comments[key + (n,)] = rec


# ---------- command line ----------

def _split(value):
    return [v for v in (value or "").split(",") if v]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--sample", type=int, default=0, help="read only this many transcripts")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--kinds", default=DEFAULT_KINDS, help=f"any of {','.join(sorted(KINDS))}")
    ap.add_argument("--projects", default="", help="project directory slugs, comma separated")
    ap.add_argument("--sessions", default="", help="session ids or prefixes, comma separated")
    ap.add_argument("--config", default="", help="a mine.toml naming slugs, sessions and a date window")
    ap.add_argument("--since", default="", help="first day kept, YYYY-MM-DD")
    ap.add_argument("--until", default="", help="last day kept, YYYY-MM-DD, inclusive")
    ap.add_argument("--dry-run", action="store_true", help="list the transcripts and count, write nothing")
    ap.add_argument("--out", default="-")
    args = ap.parse_args(argv)

    config = load_config(args.config) if args.config else None
    files = select(args.root, projects=_split(args.projects), sessions=_split(args.sessions), config=config)
    if args.sample and args.sample < len(files):
        files = random.Random(args.seed).sample(files, args.sample)
    since = args.since or (config or {}).get("since", "")
    until = args.until or (config or {}).get("until", "")
    wanted = set(_split(args.kinds))
    unknown = wanted - KINDS
    if unknown:
        ap.error(f"unknown kinds: {','.join(sorted(unknown))}")

    if args.dry_run:
        total = 0
        for f in files:
            size = f.stat().st_size
            total += size
            print(f"{size:>13,}  {f}", file=sys.stderr)
        print(f"{len(files)} transcripts, {total:,} bytes", file=sys.stderr)
    out = None
    if not args.dry_run:
        out = sys.stdout if args.out == "-" else open(args.out, "w", encoding="utf-8")
    counts, words = {}, {}
    typed_texts, suggested_texts, typed_sessions = set(), set(), set()
    try:
        for rec in extract(files, wanted, since=since, until=until):
            kind = rec["kind"]
            counts[kind] = counts.get(kind, 0) + 1
            words[kind] = words.get(kind, 0) + len(rec["text"].split())
            if kind == "human":
                typed_sessions.add(rec["origin_session"])
                (suggested_texts if rec.get("suggested") else typed_texts).add(rec["text"].strip())
            if out is not None:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    finally:
        if out is not None and out is not sys.stdout:
            out.close()
    for kind in sorted(counts):
        print(f"{kind}: {counts[kind]} records, {words[kind]:,} words", file=sys.stderr)
    if "human" in counts:
        print(f"human: {len(typed_texts)} unique typed texts, {len(suggested_texts)} unique suggested, "
              f"across {len(typed_sessions)} origin sessions", file=sys.stderr)
    print(f"{len(files)} transcripts read", file=sys.stderr)
    print("unreadable: " + ", ".join(f"{k} {v}" for k, v in STATS.items()), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

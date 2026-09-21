"""Which parts of a markdown file changed enough to be rewritten.

On 2026-09-15 the markdown hook still rewrote a whole file after every turn:
three rewrites of one unchanged sentence gave three wordings, so paragraphs Claude never touched drifted, and a
word Claude changed on request could be changed back. The end-of-turn hook now
compares the file with its text before Claude's first edit and rewrites only
the prose that changed.

A passage is rewritten when it adds at least MIN_NEW_WORDS words. Of 5,292 real
Edit changes to markdown files in the session logs that day, the 12% adding 7
words or fewer were tweaks (a renumbered step, a table cell, one word), while
changes adding 8 or more were new content. Tables, code, headings and front
matter are never rewritten."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

MIN_NEW_WORDS = 8

_PROSE = {"paragraph", "list"}
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LIST = re.compile(r"^ {0,3}([-*+]|\d+[.)])\s+")
_ATX = re.compile(r"^ {0,3}#{1,6}(\s|$)")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)\s*$")
_RULE = re.compile(r"^ {0,3}([-*_])(\s*\1){2,}\s*$")
# A tag name must be followed by a space, `/`, `>` or the end of the line, so
# an autolink such as <https://example.com> starts a paragraph, not HTML.
_HTML = re.compile(r"^ {0,3}<(/?[A-Za-z][\w-]*(?=[\s/>]|$)|!--|\?)")
_HTML_UNTIL = ((re.compile(r"^ {0,3}<!--"), "-->"), (re.compile(r"^ {0,3}<\?"), "?>"),
               (re.compile(r"^ {0,3}<(pre|script|style|textarea)(?=[\s>]|$)", re.I), None))
_TABLE_DELIM = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_FRONT_KEY = re.compile(r"^[A-Za-z_][\w-]*\s*:")


@dataclass(frozen=True)
class Block:
    kind: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Passage:
    start: int
    end: int
    text: str
    kind: str = "paragraph"
    keys: tuple = ()


def _is_table_start(lines: list[str], i: int) -> bool:
    if lines[i].lstrip().startswith("|"):
        return True
    return ("|" in lines[i] and i + 1 < len(lines) and "-" in lines[i + 1]
            and bool(_TABLE_DELIM.match(lines[i + 1])))


def _starts_block(lines: list[str], i: int) -> bool:
    """Whether line `i` starts a new block even without a blank line before it."""
    line = lines[i]
    return bool(_FENCE.match(line) or _ATX.match(line) or _RULE.match(line)
                or line.lstrip().startswith(">") or _HTML.match(line)
                or _is_table_start(lines, i))


def blocks(text: str) -> list[Block]:
    """The document's blocks in order, each with its character offsets.

    Review 2, 2026-09-15: the first version misread several shapes as prose, so
    their text could be rewritten. A fence now ends only at a bare fence of the
    same character at least as long, so a four-backtick block can hold example
    markdown with three-backtick blocks. Indented code, blockquotes, HTML
    blocks, tables (with or without leading pipes, even right after a paragraph),
    setext headings and horizontal rules are their own kinds, a list may start
    right after a paragraph, and front matter must hold `key:` lines."""
    lines = text.split("\n")
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    n = len(lines)
    out: list[Block] = []

    def add(kind: str, first: int, last: int) -> None:
        start, end = offsets[first], offsets[last] + len(lines[last])
        out.append(Block(kind, start, end, text[start:end]))

    i = 0
    if n and lines[0].strip() == "---":
        j = 1
        while j < n and lines[j].strip() not in ("---", "..."):
            j += 1
        inner = lines[1:j]
        if j < n and any(_FRONT_KEY.match(x) for x in inner):
            add("other", 0, j)
            i = j + 1
    previous_blank = True
    while i < n:
        line = lines[i]
        if not line.strip():
            i, previous_blank = i + 1, True
            continue
        fence = _FENCE.match(line)
        if fence:
            char, size = fence.group(1)[0], len(fence.group(1))
            closer = re.compile(rf"^ {{0,3}}{re.escape(char)}{{{size},}}\s*$")
            j = i + 1
            while j < n and not closer.match(lines[j]):
                j += 1
            last, kind = min(j, n - 1), "code"
        elif previous_blank and (line.startswith("    ") or line.startswith("\t")):
            j = i
            while j + 1 < n and (lines[j + 1].startswith(("    ", "\t")) or not lines[j + 1].strip()):
                j += 1
            while j > i and not lines[j].strip():
                j -= 1
            last, kind = j, "code"
        elif _ATX.match(line):
            last, kind = i, "heading"
        elif _RULE.match(line):
            last, kind = i, "rule"
        elif line.lstrip().startswith(">"):
            j = i
            while j + 1 < n and lines[j + 1].strip() and not (
                    _starts_block(lines, j + 1) and not lines[j + 1].lstrip().startswith(">")):
                j += 1
            last, kind = j, "quote"
        elif _HTML.match(line):
            j, closing = i, None
            for opener, end in _HTML_UNTIL:
                found = opener.match(line)
                if found:
                    closing = end or f"</{found.group(1).lower()}>"
                    break
            if closing:
                while j < n and closing not in (lines[j].lower() if closing.startswith("</") else lines[j]):
                    j += 1
                j = min(j, n - 1)
            else:
                while j + 1 < n and lines[j + 1].strip():
                    j += 1
            last, kind = j, "html"
        elif _is_table_start(lines, i):
            j = i
            while j + 1 < n and lines[j + 1].strip() and "|" in lines[j + 1]:
                j += 1
            last, kind = j, "table"
        elif _LIST.match(line):
            j = i
            while j + 1 < n and lines[j + 1].strip() and not _starts_block(lines, j + 1):
                j += 1
            last, kind = j, "list"
        else:
            j = i
            while (j + 1 < n and lines[j + 1].strip() and not _starts_block(lines, j + 1)
                   and not _LIST.match(lines[j + 1]) and not _SETEXT.match(lines[j + 1])):
                j += 1
            if j + 1 < n and _SETEXT.match(lines[j + 1]):
                last, kind = j + 1, "heading"
            else:
                last, kind = j, "paragraph"
        add(kind, i, last)
        i, previous_blank = last + 1, False
    return out


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])[\"')\]*_\u2019\u201d]*\s+(?=\S)")
# Review 3, 2026-09-15: splitting only before an ASCII capital kept a sentence
# starting "npm" or "È" glued to the old one before it, while "e.g." split a new
# sentence in two. Any sentence start counts now, except after these, after a
# single initial, or inside inline code.
_ABBREVIATIONS = {"e.g.", "i.e.", "etc.", "vs.", "cf.", "mr.", "mrs.", "ms.", "dr.", "prof.",
                  "fig.", "no.", "approx.", "st.", "jr.", "sr.", "inc.", "ltd.", "p.", "pp."}
_CODE_SPAN = re.compile(r"`+[^`]*`+")
_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
_WORD = re.compile(r"[^\W_][\w'’-]*")
# A changed block is compared with the old blocks this many places either side
# of its position in the region, and a region larger than _ALIGN_LIMIT pairs is
# matched by position instead of by difflib.
_WINDOW = 2
_ALIGN_LIMIT = 40000


@dataclass(frozen=True)
class _Unit:
    start: int
    end: int
    key: str
    words: tuple


def _unit(text: str, start: int, end: int, body: str) -> _Unit:
    return _Unit(start, end, " ".join(body.split()), tuple(_WORD.findall(body)))


def _units(block: Block) -> list[_Unit]:
    """A list block's items, or a paragraph's sentences, with document offsets.

    Review 2, 2026-09-15: the unit of change used to be the whole block, so one
    added sentence or bullet sent the old paragraph or list to the model, which
    could then delete most of it. A list item's key leaves out its marker, so a
    renumbered item is still the same item."""
    out: list[_Unit] = []
    if block.kind == "list":
        lines = block.text.split("\n")
        pos, starts = 0, []
        for line in lines:
            if _ITEM.match(line) or not starts:
                starts.append([pos, pos + len(line)])
            else:
                starts[-1][1] = pos + len(line)
            pos += len(line) + 1
        for first, last in starts:
            text = block.text[first:last]
            marker = _ITEM.match(text)
            body = text[marker.end():] if marker else text
            out.append(_unit(text, block.start + first, block.start + last, body))
        return out
    masked = _CODE_SPAN.sub(lambda m: "`" * len(m.group(0)), block.text)
    breaks = []
    for piece in _SENTENCE_BREAK.finditer(masked):
        before = masked[:piece.start()].rstrip("\"')]*_\u2019\u201d")
        token = before.rsplit(None, 1)[-1].lower() if before.split() else ""
        if token in _ABBREVIATIONS or re.fullmatch(r"[a-z]\.", token):
            continue
        breaks.append(piece)
    pos = 0
    for piece in breaks + [None]:
        end = piece.start() if piece else len(block.text)
        raw = block.text[pos:end]
        lead = len(raw) - len(raw.lstrip())
        text = raw.strip()
        if text:
            s = block.start + pos + lead
            out.append(_unit(text, s, s + len(text), text))
        if piece:
            pos = piece.end()
    return out


def _similar_old(unit: _Unit, nearby: list[_Unit]) -> _Unit | None:
    """The nearby old unit this one is a light edit of (it gained at most two
    words, or under half), or None."""
    for old in nearby:
        matcher = difflib.SequenceMatcher(None, old.words, unit.words, autojunk=False)
        if matcher.real_quick_ratio() < 0.5 or matcher.ratio() < 0.5:
            continue
        added = sum(j2 - j1 for tag, _, _, j1, j2 in matcher.get_opcodes()
                    if tag in ("insert", "replace"))
        if added < max(3, len(unit.words) / 2):
            return old
    return None


def _is_new(unit: _Unit, old_keys: set, nearby: list[_Unit]) -> bool:
    """A unit is new unless its text is anywhere in the baseline, or a similar
    unit near it gained only a few words (at most two, or under half)."""
    if unit.key in old_keys or not unit.words:
        return False
    return _similar_old(unit, nearby) is None


def _regions(old: list[Block], new: list[Block]):
    """The (old start, old end, new start, new end) ranges where blocks differ.

    Review 3, 2026-09-15: aligning every block with difflib took 10.8s on 400
    repeated sections and far longer beyond. The identical start and end of the
    document are trimmed first, so a normal edit aligns only what it touched,
    and a very large middle is treated as one region matched by position."""
    a, b = [x.text for x in old], [x.text for x in new]
    head = 0
    while head < min(len(a), len(b)) and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < min(len(a), len(b)) - head and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]:
        tail += 1
    mid_a, mid_b = a[head:len(a) - tail], b[head:len(b) - tail]
    if not mid_b:
        return []
    if len(mid_a) * len(mid_b) > _ALIGN_LIMIT:
        return [(head, len(a) - tail, head, len(b) - tail)]
    matcher = difflib.SequenceMatcher(None, mid_a, mid_b, autojunk=False)
    return [(head + i1, head + i2, head + j1, head + j2)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag in ("insert", "replace")]


def changed_passages(baseline: str, current: str,
                     min_words: int = MIN_NEW_WORDS) -> list[Passage]:
    """The prose passages of `current` that are new since `baseline`.

    A passage is a run of new sentences inside one paragraph, or of new items
    inside one list, that holds at least `min_words` words; bullets and item
    numbers are not words. Text that appears anywhere in the baseline is old,
    so a moved paragraph is not rewritten, and a sentence that only gained a
    word or two compared with the old sentences at the same place is left as
    it is. A passage never crosses a block, so a rewrite cannot merge or
    restructure paragraphs."""
    old, new = blocks(baseline), blocks(current)
    old_units = {i: _units(b) for i, b in enumerate(old) if b.kind in _PROSE}
    old_keys = {u.key for units in old_units.values() for u in units}
    passages: list[Passage] = []
    for i1, i2, j1, j2 in _regions(old, new):
        for j in range(j1, j2):
            block = new[j]
            if block.kind not in _PROSE:
                continue
            # The old blocks at the same relative place in the region, so a
            # renamed term across many paragraphs still finds each old sentence.
            centre = i1 + ((j - j1) * (i2 - i1)) // max(1, j2 - j1)
            nearby = [u for k in range(max(i1, centre - _WINDOW), min(i2, centre + _WINDOW + 1))
                      for u in old_units.get(k, [])]
            run: list[_Unit] = []
            for unit in _units(block) + [None]:
                if unit is not None and _is_new(unit, old_keys, nearby):
                    run.append(unit)
                    continue
                if run and sum(len(u.words) for u in run) >= min_words:
                    passages.append(Passage(run[0].start, run[-1].end,
                                            current[run[0].start:run[-1].end], block.kind,
                                            tuple(u.key for u in run)))
                run = []
    return passages


def new_unit_keys(before: str, after: str, own: set) -> set:
    """The keys of the sentences and list items one edit added.

    Review 3, 2026-09-15: guessing Claude's text by comparing file snapshots
    across turns failed when turns overlapped or someone typed at the wrong
    moment. The edit hooks call this with the text right before and right
    after one of Claude's own edits, so what it returns is Claude's. A unit
    that is a light edit of one of Claude's earlier units (`own`) is still
    Claude's; a light edit of anyone else's text is not."""
    old, new = blocks(before), blocks(after)
    old_units = {i: _units(b) for i, b in enumerate(old) if b.kind in _PROSE}
    old_keys = {u.key for units in old_units.values() for u in units}
    found: set = set()
    for i1, i2, j1, j2 in _regions(old, new):
        for j in range(j1, j2):
            if new[j].kind not in _PROSE:
                continue
            centre = i1 + ((j - j1) * (i2 - i1)) // max(1, j2 - j1)
            nearby = [u for k in range(max(i1, centre - _WINDOW), min(i2, centre + _WINDOW + 1))
                      for u in old_units.get(k, [])]
            for unit in _units(new[j]):
                if unit.key in old_keys or not unit.words:
                    continue
                similar = _similar_old(unit, nearby)
                if similar is None or similar.key in own:
                    found.add(unit.key)
    return found


def unit_keys(text: str) -> set:
    """The keys of every sentence and list item in `text`."""
    return {u.key for b in blocks(text) if b.kind in _PROSE for u in _units(b)}


def recorded_passages(current: str, keys: set, min_words: int = MIN_NEW_WORDS) -> list[Passage]:
    """Runs of recorded units inside one block that hold at least `min_words`."""
    passages: list[Passage] = []
    for block in blocks(current):
        if block.kind not in _PROSE:
            continue
        run: list[_Unit] = []
        for unit in _units(block) + [None]:
            if unit is not None and unit.key in keys:
                run.append(unit)
                continue
            if run and sum(len(u.words) for u in run) >= min_words:
                passages.append(Passage(run[0].start, run[-1].end,
                                        current[run[0].start:run[-1].end], block.kind,
                                        tuple(u.key for u in run)))
            run = []
    return passages


# The rewrite of the changed passages. The whole document goes into the prompt
# as context with the passages marked, and the model returns only the passages,
# which are spliced back. Text outside them never passes through the model.

# A new passage is prose written in one turn, where cutting filler can honestly
# shrink it the way it shrinks a commit message (see humanize._TEXT_FLOORS).
PASSAGE_FLOOR = 0.25


def _splice(text: str, passages: list[Passage], replacements: list[str]) -> str:
    for passage, new in sorted(zip(passages, replacements), key=lambda x: x[0].start,
                               reverse=True):
        text = text[:passage.start] + new + text[passage.end:]
    return text


def build_passage_prompt(marked: str, count: int, skill: str, voice: str = "",
                         sources: dict[str, str] | None = None, token: str = "wr") -> str:
    """The prompt for the end-of-turn rewrite of Claude's new passages.

    2026-09-15: it changes prose only. It used to
    be told to add background from the files the document names, and a model
    without the repository gets that wrong; Claude wrote these sentences with
    the repository open. `sources` is accepted and ignored."""
    from .humanize import _assemble, _facts
    scope = (f"The document at the end is shown whole so you can read in context. "
             f"{count} passage{'s' if count != 1 else ''} in it "
             f"{'are' if count != 1 else 'is'} new and marked with [[{token} passage N]] "
             f"and [[/{token} passage N]]. Rewrite only those passages; everything "
             "outside the markers is someone's finished text and stays as it is. "
             "Keep each passage the same kind of text: sentences stay sentences in "
             "the same paragraph, and list items stay the same number of list items.")
    keep = ("Follow the humanizer skill below: its section How to work is the "
            "process, its numbered patterns are what to look for, and its section "
            "When not to act says what to leave alone. Use its embedded mode. Keep "
            "code blocks, inline code, commands, paths, link targets, tables of "
            "data and front matter exactly as they are.")
    parts = [
        "Rewrite the marked passages of the document at the end so they read as a "
        "person wrote them.",
        "", " ".join([scope, keep, _facts(None)]), "",
        "Reply with each rewritten passage between the same two markers, numbered "
        "as in the document and in the same order, and nothing else: no text "
        "outside the markers, no preamble, no summary, no code fence.",
    ]
    return _assemble(parts, marked, skill, voice, None)


_PREFIX = re.compile(r"^\s*([-*+]|\d+[.)])")
_TERMINAL = re.compile(r"[.!?\u2026][\"')\]*_\u2019\u201d]*$")
_BLOCK_START = re.compile(r"^ {0,3}(#{1,6}(\s|$)|`{3,}|~{3,}|>|<(/?[A-Za-z][\w-]*(?=[\s/>]|$)|!--)|\||([-*_])(\s*\4){2,}\s*$)")


def _shape_problem(number: int, passage: Passage, new: str) -> str:
    """Why a rewritten passage no longer fits where it came from, or ""."""
    if not new.strip():
        return f"passage {number} came back empty"
    lines = new.split("\n")
    if "" in (line.strip() for line in lines):
        return f"passage {number} came back as more than one block"
    if any(_BLOCK_START.match(line) for line in lines):
        return f"passage {number} came back with a heading, code, quote, table or rule in it"
    if any(_SETEXT.match(line) for line in lines[1:]):
        return f"passage {number} came back with a heading underline in it"
    if passage.kind == "list":
        before = [_PREFIX.match(line).group(0) for line in passage.text.split("\n") if _ITEM.match(line)]
        after = [_PREFIX.match(line).group(0) for line in lines if _ITEM.match(line)]
        if len(after) != len(before) or not _ITEM.match(lines[0]):
            return (f"passage {number} has {len(after)} list item{'s' if len(after) != 1 else ''} "
                    f"instead of {len(before)}")
        if after != before:
            # Review 3: a nested item rewritten with another marker became a
            # top-level item, and "3." came back as "1.".
            return (f"passage {number} changed the prefix of a list item (its indentation, "
                    "marker or number)")
    elif any(_ITEM.match(line) for line in lines):
        return f"passage {number} came back as a list"
    return ""


def _anchors(text: str) -> set:
    """The `#anchors` GitHub gives the document's headings, duplicates numbered.

    Review 3: headings were found with a regex, so a `# comment` inside a code
    block counted and a second "Setup" heading (`#setup-1`) or a setext heading
    did not."""
    from .humanize import _slug
    seen: dict[str, int] = {}
    out = set()
    for block in blocks(text):
        if block.kind != "heading":
            continue
        lines = block.text.split("\n")
        title = (" ".join(lines[:-1]) if len(lines) > 1 and _SETEXT.match(lines[-1])
                 else re.sub(r"^ {0,3}#{1,6}\s*|\s+#+\s*$", "", lines[0]))
        slug = _slug(title)
        count = seen.get(slug, 0)
        out.add(slug if count == 0 else f"{slug}-{count}")
        seen[slug] = count + 1
    return out


def _raw_offsets(raw: str):
    """A function from a position in the LF text to the same position in `raw`."""
    import bisect
    crlf, removed = [], 0
    index = raw.find("\r\n")
    while index != -1:
        crlf.append(index - removed)
        removed += 1
        index = raw.find("\r\n", index + 2)
    return lambda k: k + bisect.bisect_right(crlf, k - 1)


def humanize_passages(path, baseline: str | None = None, *, keys: set | None = None,
                      spawn=None, voice: str = "",
                      model: str | None = None, timeout: int | None = None,
                      write: bool = True, min_words: int = MIN_NEW_WORDS):
    """Rewrite only the passages of `path` that are new since `baseline`.

    One model call for all passages. Review 2, 2026-09-15: the markers carry a
    fresh random token, so text that mentions markers cannot cut a passage
    short; the reply must hold each passage once, in order, and nothing else,
    and a reply that does not is marked for retry; every passage is checked on
    its own (not empty, same shape, the length floor, and the usual checks with
    the whole document as sources and its headings as link targets). The write
    is the atomic swap. With nothing new, no call is made."""
    import secrets
    import time
    from pathlib import Path

    from .humanize import (DEFAULT_TIMEOUT, Result, _lf, _read, _unwrap, _write,
                           _write_unless_changed, check, load_skill)
    from .spawn import Spawn

    path = Path(path)
    raw = _read(path)
    current = _lf(raw)
    if keys is not None:
        found = recorded_passages(current, keys, min_words)
    else:
        found = changed_passages(_lf(baseline or ""), current, min_words)
    result = Result(path=path, text=current, passages=len(found))
    result.sent_keys = {k for p in found for k in p.keys}
    if not found:
        return result
    token = f"wr-{secrets.token_hex(4)}"
    marked = _splice(current, found, [f"[[{token} passage {i}]]\n{p.text}\n[[/{token} passage {i}]]"
                                      for i, p in enumerate(found, 1)])
    sources = {}
    spawn = spawn or Spawn()
    started = time.monotonic()
    answer = spawn.run(build_passage_prompt(marked, len(found), load_skill(), voice, sources, token),
                       model=model, timeout=timeout or DEFAULT_TIMEOUT)
    reply = _unwrap(getattr(answer, "stdout", answer) or "")
    result.seconds, result.sources = time.monotonic() - started, list(sources)
    pattern = re.compile(rf"\[\[{token} passage (\d+)\]\]\n?(.*?)\n?\[\[/{token} passage \1\]\]", re.S)
    matches = list(pattern.finditer(reply))
    between = [reply[a:b] for a, b in zip([0] + [m.end() for m in matches],
                                          [m.start() for m in matches] + [len(reply)])]
    if [int(m.group(1)) for m in matches] != list(range(1, len(found) + 1)):
        result.refused = (f"the reply did not return each of the {len(found)} marked "
                          f"passage{'s' if len(found) != 1 else ''} once and in order")
        result.retry = True
        return result
    if any(gap.strip() for gap in between):
        result.refused = "the reply had text outside the marked passages"
        result.retry = True
        return result
    rewritten = [m.group(2).strip("\n") for m in matches]
    from .changes import changes_between
    # What the rewrite did, for the note that asks Claude to check it: the old
    # and new text of every passage it changed, and the sentence-level list.
    result.pairs = [(p.text, t) for p, t in zip(found, rewritten) if " ".join(p.text.split()) != " ".join(t.split())]
    result.changes = [c for p, t in zip(found, rewritten) for c in changes_between(p.text, t) if c.kind != "same"]
    new = _splice(current, found, rewritten)
    result.text = new
    context = "\n".join([current, *sources.values()])
    anchors = _anchors(new)
    for number, (passage, text) in enumerate(zip(found, rewritten), 1):
        reason = _shape_problem(number, passage, text)
        if not reason and passage.kind == "paragraph" and _TERMINAL.search(passage.text):
            after = current[passage.end:current.find("\n", passage.end) if "\n" in current[passage.end:] else len(current)]
            if after.strip() and not _TERMINAL.search(text):
                # Review 3: without its full stop, the sentence ran into the next.
                reason = (f"passage {number} lost the full stop that separates it from the "
                          "sentence after it")
        reason = reason or check(passage.text + "\n", text + "\n", context,
                                 base=path.resolve().parent, floor=PASSAGE_FLOOR,
                                 unit=f"passage {number}", anchors=anchors)
        if reason:
            result.refused = reason if f"passage {number}" in reason else f"passage {number}: {reason}"
            break
    # The passages go back into the raw text, each with the line ending of the
    # text it replaces, so a file mixing endings keeps every other byte (review 3).
    to_raw = _raw_offsets(raw)
    raw_new = raw
    for passage, text in sorted(zip(found, rewritten), key=lambda x: x[0].start, reverse=True):
        start, end = to_raw(passage.start), to_raw(passage.end)
        piece = text.replace("\n", "\r\n") if "\r\n" in raw[start:end] else text
        raw_new = raw_new[:start] + piece + raw_new[end:]
    if write and not result.refused and new != current:
        if _write_unless_changed(path, raw, raw_new):
            result.written = True
        else:
            result.conflict = True
            result.refused = ("the file changed while it was being rewritten, so the "
                              "rewrite was not written over those changes")
    if write and result.refused and new != current:
        result.kept = path.with_name(f"{path.stem}.refused{path.suffix}")
        _write(result.kept, raw_new)
    return result

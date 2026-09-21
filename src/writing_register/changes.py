"""What a rewrite changed, sentence by sentence.

2026-09-15. After `wr humanize` rewrites a document, three things
need the same list: the checker that verifies added and changed sentences
against the code, the report a person reads, and the note that hands Claude
each passage the end-of-turn hook changed. `changes_between` builds it from
the blocks and units `passages.py` already parses. `revert_blocks` puts text
back when the checker rejects a sentence.

Review of 2026-09-15: reverting one new block at a time
duplicated a paragraph the rewrite had split, lost one of two paragraphs it had
merged, and deleted a paragraph it had moved and reworded; and pairing counted
stop words, so an unrelated sentence passed as a rewording. Now sentences pair
on content words, identical sentences are matched across the whole document so
a moved one is found, a revert puts back every block connected to the rejected
sentence, a moved and reworded paragraph gets its original back where the
rewrite put it, and a shape that cannot be undone in part raises RevertUnsafe."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, replace

from .passages import _PROSE, _Unit, _units, blocks

# Two sentences are one sentence reworded when this share of their content
# words (stop words left out) lines up; below it they are an addition and a
# removal.
# 0.45: the review's unrelated pair ("stored in the folder of the team" and "sent
# to the manager of the team every week") scores 0.40 and must not pair, and
# 0.5 turned 43 real rewordings in the replay fixtures into additions.
_PAIR_RATIO = 0.45
# Aligning blocks with difflib is quadratic; a middle larger than this many
# block pairs is treated as one replaced region.
_ALIGN_LIMIT = 40000
_STOPWORDS = set("""a an and are as at be been but by can could did do does for from had has have if in
into is it its may might must of on or our should so than that the their them then there these
they this those to was we were what when where which while who will with would you your""".split())
_WORD = re.compile(r"[^\W_][\w'’-]*")


@dataclass(frozen=True)
class Change:
    kind: str            # "added" | "changed" | "removed" | "same"
    old: str             # the original wording; "" when added
    new: str             # the rewrite's wording; "" when removed
    old_start: int = -1
    old_end: int = -1
    new_start: int = -1
    new_end: int = -1
    block: str = "paragraph"   # kind of the block the sentence sits in
    new_block: int = -1        # index in blocks(new); -1 when removed
    old_block: int = -1        # index in blocks(old) of the first original; -1 when added
    number: int = 0            # 1-based over added and changed, in document order
    old_blocks: tuple = ()     # every block of blocks(old) the sentence came from


class RevertUnsafe(Exception):
    """The rewrite reshaped the text around a rejected sentence in a way that a
    partial revert cannot undo without repeating or losing text."""


def _block_opcodes(old_blocks, new_blocks):
    """difflib opcodes over block texts, with the identical head and tail
    trimmed first (the same idea as passages._regions)."""
    a, b = [x.text for x in old_blocks], [x.text for x in new_blocks]
    head = 0
    while head < min(len(a), len(b)) and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < min(len(a), len(b)) - head and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]:
        tail += 1
    ops = [("equal", 0, head, 0, head)] if head else []
    mid_a, mid_b = a[head:len(a) - tail], b[head:len(b) - tail]
    if mid_a or mid_b:
        if len(mid_a) * len(mid_b) > _ALIGN_LIMIT:
            ops.append(("replace", head, len(a) - tail, head, len(b) - tail))
        else:
            matcher = difflib.SequenceMatcher(None, mid_a, mid_b, autojunk=False)
            ops += [(tag, head + i1, head + i2, head + j1, head + j2)
                    for tag, i1, i2, j1, j2 in matcher.get_opcodes()]
    if tail:
        ops.append(("equal", len(a) - tail, len(a), len(b) - tail, len(b)))
    return ops


def _prose_units(block_list, first, last):
    out = []
    for index in range(first, last):
        if block_list[index].kind in _PROSE:
            out += [(index, u) for u in _units(block_list[index])]
    return out


def _content(words) -> tuple:
    lowered = tuple(w.lower() for w in words)
    content = tuple(w for w in lowered if w not in _STOPWORDS)
    return content or lowered


def _ratio(a: _Unit, b: _Unit) -> float:
    return _text_ratio_words(_content(a.words), _content(b.words))


def _text_ratio_words(a: tuple, b: tuple) -> float:
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if matcher.real_quick_ratio() < _PAIR_RATIO or matcher.quick_ratio() < _PAIR_RATIO:
        return 0.0
    return matcher.ratio()


def _text_ratio(a: str, b: str) -> float:
    return _text_ratio_words(_content(_WORD.findall(a)), _content(_WORD.findall(b)))


def _pair(old_units, new_units, old_order):
    """Pair reworded sentences inside a replaced region.

    Greedy by content-word overlap. An old sentence left over right after one
    already paired, and next to it in the original, folds into that pair, so the
    sentences the rewrite merged into one all come back when the pair is
    reverted, even from two paragraphs."""
    pairs, used_old, used_new = {}, set(), set()
    candidates = sorted(((_ratio(o, n), i, j) for i, o in enumerate(old_units)
                         for j, n in enumerate(new_units)), key=lambda x: -x[0])
    for ratio, i, j in candidates:
        if ratio < _PAIR_RATIO:
            break
        if i in used_old or j in used_new:
            continue
        pairs[j] = [i]
        used_old.add(i)
        used_new.add(j)
    for i in range(1, len(old_units)):
        if i in used_old or old_order[i] != old_order[i - 1] + 1:
            continue
        owner = next((j for j, olds in pairs.items() if i - 1 in olds), None)
        if owner is not None:
            pairs[owner].append(i)
            used_old.add(i)
    return pairs


def changes_between(old: str, new: str) -> list[Change]:
    """Every sentence and list item of `new` and where it came from, plus the
    sentences of `old` that are gone. Added and changed ones are numbered in
    document order. Whitespace-only differences count as the same sentence."""
    old_blocks, new_blocks = blocks(old), blocks(new)
    ops = _block_opcodes(old_blocks, new_blocks)
    out: list[Change] = []
    regions = [(r, op) for r, op in enumerate(ops) if op[0] != "equal"]
    region_old = {r: _prose_units(old_blocks, op[1], op[2]) for r, op in regions}
    region_new = {r: _prose_units(new_blocks, op[3], op[4]) for r, op in regions}
    taken = {r: set() for r, _ in regions}
    exact = {r: {} for r, _ in regions}
    # Identical sentences in the same region first, then anywhere in the
    # document, so a paragraph the rewrite only moved is the same paragraph.
    for r, _ in regions:
        keys: dict = {}
        for k, (_, u) in enumerate(region_old[r]):
            keys.setdefault(u.key, []).append(k)
        for j, (_, nu) in enumerate(region_new[r]):
            for k in keys.get(nu.key, []):
                if k not in taken[r]:
                    exact[r][j] = (r, k)
                    taken[r].add(k)
                    break
    anywhere: dict = {}
    for r, _ in regions:
        for k, (_, u) in enumerate(region_old[r]):
            if k not in taken[r]:
                anywhere.setdefault(u.key, []).append((r, k))
    for r, _ in regions:
        for j, (_, nu) in enumerate(region_new[r]):
            if j in exact[r]:
                continue
            for r2, k in anywhere.get(nu.key, []):
                if k not in taken[r2]:
                    exact[r][j] = (r2, k)
                    taken[r2].add(k)
                    break
    pairs_by_region, rest_by_region = {}, {}
    for r, _ in regions:
        rest_old = [(k, ou) for k, (_, ou) in enumerate(region_old[r]) if k not in taken[r]]
        rest_new = [(j, nu) for j, (_, nu) in enumerate(region_new[r]) if j not in exact[r]]
        pairs_by_region[r] = _pair([ou for _, ou in rest_old], [nu for _, nu in rest_new],
                                   [k for k, _ in rest_old])
        rest_by_region[r] = (rest_old, rest_new)
    for r, op in enumerate(ops):
        tag, i1, i2, j1, j2 = op
        if tag == "equal":
            for offset in range(j2 - j1):
                ob, nb = old_blocks[i1 + offset], new_blocks[j1 + offset]
                if nb.kind not in _PROSE:
                    continue
                for ou, nu in zip(_units(ob), _units(nb)):
                    out.append(Change("same", old[ou.start:ou.end], new[nu.start:nu.end],
                                      ou.start, ou.end, nu.start, nu.end, nb.kind,
                                      j1 + offset, i1 + offset, 0, (i1 + offset,)))
            continue
        rest_old, rest_new = rest_by_region[r]
        pairs = pairs_by_region[r]
        local_of = {jj: idx for idx, (jj, _) in enumerate(rest_new)}
        paired_old = {rest_old[i][0] for olds in pairs.values() for i in olds}
        for j, (nb_index, nu) in enumerate(region_new[r]):
            nb = new_blocks[nb_index]
            if j in exact[r]:
                r2, k = exact[r][j]
                ob_index, ou = region_old[r2][k]
                out.append(Change("same", old[ou.start:ou.end], new[nu.start:nu.end],
                                  ou.start, ou.end, nu.start, nu.end, nb.kind, nb_index,
                                  ob_index, 0, (ob_index,)))
                continue
            local = local_of.get(j)
            if local is not None and local in pairs:
                olds = [rest_old[i] for i in sorted(pairs[local])]
                text = " ".join(old[ou.start:ou.end] for _, ou in olds)
                obs = tuple(sorted({region_old[r][k][0] for k, _ in olds}))
                out.append(Change("changed", text, new[nu.start:nu.end],
                                  olds[0][1].start, olds[-1][1].end, nu.start, nu.end,
                                  nb.kind, nb_index, obs[0], 0, obs))
            else:
                out.append(Change("added", "", new[nu.start:nu.end], -1, -1, nu.start, nu.end,
                                  nb.kind, nb_index, -1, 0, ()))
        for k, (ob_index, ou) in enumerate(region_old[r]):
            if k not in taken[r] and k not in paired_old:
                out.append(Change("removed", old[ou.start:ou.end], "", ou.start, ou.end, -1, -1,
                                  old_blocks[ob_index].kind, -1, ob_index, 0, (ob_index,)))
    out.sort(key=lambda c: (c.new_start if c.new_start >= 0 else c.old_start, c.kind == "removed"))
    number, numbered = 0, []
    for c in out:
        if c.kind in ("added", "changed"):
            number += 1
            c = replace(c, number=number)
        numbered.append(c)
    return numbered


def claims(changes: list[Change]) -> list[Change]:
    """The added and changed sentences, in document order: what a checker judges."""
    return [c for c in changes if c.kind in ("added", "changed")]


def _groups(changes: list[Change]) -> dict:
    """Each block node, ("n", i) in the rewrite or ("o", k) in the original,
    mapped to the set of nodes connected to it through its sentences."""
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for c in changes:
        if c.new_block < 0:
            continue
        a = find(("n", c.new_block))
        for k in c.old_blocks:
            b = find(("o", k))
            if a != b:
                parent[b] = a
    members: dict = {}
    for node in list(parent):
        members.setdefault(find(node), set()).add(node)
    return {node: members[find(node)] for node in parent}


def revert_plan(changes: list[Change], failing: set[int]) -> list[tuple[list, list]]:
    """For each group of blocks holding a rejected rewording: the rewrite's block
    indices to replace and the original's block indices to put back. Rejected
    additions are not in the plan; `revert_blocks` removes them one by one."""
    group_of = _groups(changes)
    plan, seen = [], set()
    for c in changes:
        if c.number not in failing or c.new_block < 0 or c.kind != "changed":
            continue
        group = frozenset(group_of[("n", c.new_block)])
        if group in seen:
            continue
        seen.add(group)
        plan.append((sorted(i for t, i in group if t == "n"), sorted(i for t, i in group if t == "o")))
    return plan


def blocks_to_revert(changes: list[Change], failing: set[int]) -> set[int]:
    """The indices in blocks(new) a revert of `failing` touches."""
    touched = {i for new_blocks, _ in revert_plan(changes, failing) for i in new_blocks}
    touched |= {c.new_block for c in changes if c.number in failing and c.kind == "added" and c.new_block >= 0}
    return touched


def _flat(text: str) -> str:
    return " ".join(text.split())


def _cut(text: str, start: int, end: int) -> str:
    """`text` without [start:end] and the space or line break that joined it."""
    if text[end:end + 1] == " ":
        end += 1
    elif text[start - 1:start] == " ":
        start -= 1
    elif text[end:end + 1] == "\n" and (start == 0 or text[start - 1:start] == "\n"):
        end += 1
    return text[:start] + text[end:]


def revert_blocks(new: str, old: str, changes: list[Change], failing: set[int]) -> str:
    """`new` with the rejected sentences taken back.

    A rejected addition is removed on its own, which can neither repeat nor lose
    the original's text; when it resembles an original sentence the rewrite
    removed, it was moved and reworded, and that original takes its place. A
    rejected rewording puts back its whole group of blocks, because a rewording
    can merge or split sentences. Raises RevertUnsafe when a group is not a
    contiguous run of blocks, or when putting it back would repeat text."""
    old_blocks, new_blocks = blocks(old), blocks(new)
    flat_new = _flat(new)
    removed = [c for c in changes if c.kind == "removed"]
    edits, restored, used = [], [], set()
    covered = []
    for new_idx, old_idx in revert_plan(changes, failing):
        if new_idx != list(range(new_idx[0], new_idx[-1] + 1)):
            raise RevertUnsafe("the rejected sentence's paragraph was split around text that stayed")
        if old_idx and old_idx != list(range(old_idx[0], old_idx[-1] + 1)):
            raise RevertUnsafe("the original of the rejected sentence is spread across paragraphs that stayed")
        start, end = new_blocks[new_idx[0]].start, new_blocks[new_idx[-1]].end
        text = "\n\n".join(old_blocks[k].text for k in old_idx)
        edits.append((start, end, text))
        covered.append((start, end))
        restored += [_flat(old_blocks[k].text) for k in old_idx]
    for c in changes:
        if c.number not in failing or c.kind != "added":
            continue
        if any(s <= c.new_start and c.new_end <= e for s, e in covered):
            continue
        best = None
        for r in removed:
            key = (r.old_start, r.old_end)
            if key in used or _flat(r.old) in flat_new:
                continue
            score = _text_ratio(r.old, c.new)
            if score >= _PAIR_RATIO and (best is None or score > best[0]):
                best = (score, key, r.old)
        whole = new_blocks[c.new_block]
        if best is not None:
            used.add(best[1])
            edits.append((c.new_start, c.new_end, best[2]))
            restored.append(_flat(best[2]))
        elif whole.start == c.new_start and whole.end == c.new_end:
            # The addition is a paragraph of its own: remove the block and one
            # of the blank lines around it.
            edits.append((c.new_start, c.new_end, ""))
        else:
            edits.append((c.new_start, c.new_end, None))
    result = new
    for start, end, text in sorted(edits, key=lambda e: e[0], reverse=True):
        if text is None:
            result = _cut(result, start, end)
        elif text:
            result = result[:start] + text + result[end:]
        else:
            if result[end:end + 2] == "\n\n":
                end += 2
            elif result[start - 2:start] == "\n\n":
                start -= 2
            elif result[end:end + 1] == "\n":
                end += 1
            result = result[:start] + result[end:]
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"(?m)^[ \t]+$", "", result)
    trailing = len(new) - len(new.rstrip("\n"))
    result = result.rstrip("\n") + "\n" * trailing
    flat_old, flat_result = _flat(old), _flat(result)
    for piece in restored:
        if piece and flat_result.count(piece) > flat_old.count(piece):
            raise RevertUnsafe("putting the original back would repeat text the rewrite kept elsewhere")
    return result

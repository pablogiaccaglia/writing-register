#!/usr/bin/env python3
"""Cut the mined transcript records into episodes.

An episode is one input from the person, followed until their next input in
the same origin session. The inputs are a message they typed (or queued while
Claude was working, or accepted from Claude's suggestion), a comment they left
as owner of a published page, and an answer they typed to a question. Each
episode records three things:

- `seen`, what the person was looking at when they wrote. For a typed message
  it is the reply text on the path from their previous input to this one,
  found by walking each line's parent back through the transcript, so a branch
  they rewound past is not counted. A queued message is spliced into the chain
  wherever Claude happened to be, so it is paired by time instead. A comment
  saw the passage it was left on, and an answer saw its question;
- `response`, what changed before the next input: the edits (subagents write
  under the same origin session, so their edits join by time) and the
  publishes with their labels;
- `pairs`, before-and-after candidates, best first, each marked with how it
  was found (`pair_quality`): `exact` from an edit tool, `literal` from a
  Python heredoc, `commit` from a commit that followed, `final` from the most
  similar paragraph of the committed document, and `label-only` when only a
  version label names the fix.

Text the person pasted from what they were looking at is a "before", not their
own words, so it is cut out of `words` and kept in `pasted`. A prompt accepted
from Claude's suggestion stays in the timeline with `suggested: true` and must
never be quoted as the person's.

Standard library only, no model calls. Reads the corpus written by
claude_prose.py and, optionally, the revisions written by mine_revisions.py.
"""
import argparse
import bisect
import datetime
import difflib
import importlib.util
import json
import pathlib
import re
import sys
import time

SEEN_WORDS = 1500
PASTE_RUN = 6
MAX_PAIRS = 20
FINAL_MIN_RATIO = 0.3
COMMIT_SLACK = datetime.timedelta(minutes=30)
PROSE_SUFFIXES = {".md", ".markdown", ".html", ".htm", ".txt", ".rst"}
LITERAL_METHODS = {"heredoc-replace", "heredoc-re.sub", "heredoc-assign", "heredoc-pairs"}

_ARTIFACT_LINK = re.compile(r"https?://[\w.-]+/(?:code/)?artifact/([\w-]+)")
_DOC_PATH = re.compile(r"(?<![\w/.-])((?:[\w.~-]+/)*[\w.-]+\.(?:md|markdown|html?|pdf|txt|rst))\b")
_DOC_WORDS = re.compile(r"\b(readme|page|pages|results?|report|figures?|sections?|tables?|captions?|legends?|"
                        r"docs?|documents?|artifacts?|plots?|charts?)\b", re.I)
_NONCE_LINE = re.compile(r"^[0-9a-f]{8}\|\s?", re.M)
_TOKEN = re.compile(r"\S+")
_EDGE = "\"'`’‘“”()[]{}<>.,;:!?*_-—–"


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def when(stamp):
    """A timestamp as an aware datetime; None when it does not parse."""
    if not stamp:
        return None
    try:
        value = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value


def is_prose(path):
    return pathlib.PurePath(path or "").suffix.lower() in PROSE_SUFFIXES


# ---------- words ----------

def _norm_tokens(text):
    """Words split on whitespace, lowercased, with the punctuation around them
    dropped, as (word, start, end). A path or a hyphenated word stays one word,
    so a shared file name does not count as a run of six."""
    out = []
    for m in _TOKEN.finditer(text or ""):
        word = m.group(0)
        core = word.strip(_EDGE)
        if not core:
            continue
        start = m.start() + word.index(core)
        out.append((core.lower().replace("’", "'"), start, start + len(core)))
    return out


def shingles(text, n=PASTE_RUN):
    """The runs of n normalised words in a text."""
    words = [t[0] for t in _norm_tokens(text)]
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def pasted_spans(message, seen, n=PASTE_RUN):
    """The spans of a message that repeat at least n consecutive normalised
    words of what the person was looking at, as they appear in the message."""
    if not message or not seen:
        return []
    grams = shingles(seen, n)
    if not grams:
        return []
    tokens = _norm_tokens(message)
    marked = [False] * len(tokens)
    for i in range(len(tokens) - n + 1):
        if tuple(t[0] for t in tokens[i:i + n]) in grams:
            for k in range(i, i + n):
                marked[k] = True
    spans, start = [], None
    for i, on in enumerate(marked + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            spans.append(message[tokens[start][1]:tokens[i - 1][2]])
            start = None
    return spans


def _cut(message, spans):
    for span in spans:
        message = message.replace(span, "[pasted]", 1)
    return message


def _cap_words(text, cap=SEEN_WORDS):
    """The last `cap` words of a text, keeping its line breaks."""
    words = text.split()
    if len(words) <= cap:
        return text.strip()
    keep = words[-cap:]
    # find where the kept words start in the original, to keep its layout
    pos = len(text)
    for w in reversed(keep):
        pos = text.rfind(w, 0, pos)
    return text[pos:].strip() if pos >= 0 else " ".join(keep)


def similarity(a, b):
    """How much two passages share, 0 to 1, by the words they have in common."""
    wa, wb = [t[0] for t in _norm_tokens(a)], [t[0] for t in _norm_tokens(b)]
    if not wa or not wb:
        return 0.0
    sa, sb = set(wa), set(wb)
    return len(sa & sb) / max(1, min(len(sa), len(sb))) * (min(len(sa), len(sb)) / max(len(sa), len(sb))) ** 0.5


# ---------- transcripts ----------

def parent_map(files):
    """For each transcript file, every line's parent uuid. A compaction
    boundary has no parent but names the line it logically follows."""
    out = {}
    for path in files:
        links = {}
        try:
            with open(path, "rb") as fh:
                for raw in fh:
                    if b'"uuid"' not in raw:
                        continue
                    try:
                        row = json.loads(raw)
                    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(row, dict) and row.get("uuid"):
                        links[row["uuid"]] = row.get("parentUuid") or row.get("logicalParentUuid") or ""
        except OSError:
            continue
        out[str(path)] = links
    return out


# ---------- inputs ----------

def _channel(rec):
    if rec["kind"] == "human":
        return "queued" if rec.get("prompt_source") == "queued" else "typed"
    return rec["kind"]


def _clean_on_text(text):
    return _NONCE_LINE.sub("", text or "").strip()


def _episode_id(rec, n):
    ident = rec.get("comment_id") or rec.get("origin_uuid") or rec.get("uuid") or ""
    return f'{rec["origin_session"][:8]}:{rec["kind"]}:{ident}:{n}'


class _Session:
    """Everything recorded under one origin session, sorted by time."""

    def __init__(self, recs):
        self.recs = sorted(recs, key=lambda r: (r["_t"], r["_i"]))
        self.times = [r["_t"] for r in self.recs]

    def between(self, start, stop, kinds, source=None):
        lo = bisect.bisect_left(self.times, start)
        hi = len(self.recs) if stop is None else bisect.bisect_left(self.times, stop)
        return [r for r in self.recs[lo:hi] if r["kind"] in kinds and (source is None or r["source"] == source)]

    def before(self, stop, kinds):
        hi = bisect.bisect_left(self.times, stop)
        return [r for r in self.recs[:hi] if r["kind"] in kinds]


def _replies_on_path(rec, parents, stop_uuids, replies_by_file):
    """The reply texts on the parent chain from this input back to the
    previous one, in order; None when the chain cannot be read."""
    file = rec["src"]["file"]
    links = parents.get(file) if parents else None
    if not links or rec["uuid"] not in links:
        return None
    chain, u, guard = set(), links.get(rec["uuid"]), 0
    while u and u not in chain and guard < 1_000_000:
        if u in stop_uuids:
            break
        chain.add(u)
        u = links.get(u)
        guard += 1
    offset = rec["src"]["offset"]
    return [r["text"] for r in replies_by_file.get(file, ())
            if r["uuid"] in chain and r["src"]["offset"] < offset]


# ---------- targets ----------

def _artifact_key(url_or_id):
    m = _ARTIFACT_LINK.search(url_or_id or "")
    return m.group(1) if m else (url_or_id or "")


def _live_version(publishes, key, at):
    """The last publish of an artifact that succeeded before a moment."""
    live = None
    for p in publishes:
        if p["_t"] > at or p.get("refused") is not False:
            continue
        if key in (_artifact_key(p.get("url")), p.get("artifact_id")):
            live = p
    return live


def _artifact_target(publishes, key, at, url=""):
    live = _live_version(publishes, key, at)
    target = {"kind": "artifact", "url": url or (live or {}).get("url", ""),
              "artifact_id": (live or {}).get("artifact_id", "") or key}
    if live:
        target.update(seq=live.get("seq"), label=live.get("label", ""), title=live.get("title", ""),
                      published=live["ts"])
    else:
        target.update(seq=None, label="", title="", published="")
    return target


def find_target(rec, channel, seen, session, publishes):
    """What the person's input was about: a document path or an artifact the
    reply they saw linked or named, the latest document of the session when
    their own words name one, or else the reply itself."""
    at = rec["_t"]
    if channel == "comment":
        return _artifact_target(publishes, _artifact_key(rec.get("url")), at, rec.get("url", ""))
    links = [(m.start(), "artifact", m.group(1), m.group(0)) for m in _ARTIFACT_LINK.finditer(seen or "")]
    links += [(m.start(), "document", m.group(1), "") for m in _DOC_PATH.finditer(seen or "")]
    if links:
        _, kind, value, url = max(links)
        if kind == "artifact":
            return _artifact_target(publishes, value, at, url)
        return {"kind": "document", "path": value}
    if _DOC_WORDS.search(rec.get("text") or ""):
        latest = None
        for r in session.before(at, {"publish", "edit"}):
            if r["kind"] == "publish" and r.get("refused") is False:
                latest = r
            elif r["kind"] == "edit" and is_prose(r["path"]):
                latest = r
        if latest is not None and latest["kind"] == "publish":
            return _artifact_target(publishes, _artifact_key(latest.get("url")) or latest.get("artifact_id"), at,
                                    latest.get("url", ""))
        if latest is not None:
            return {"kind": "document", "path": latest["path"]}
    return {"kind": "reply"}


# ---------- pairs ----------

def _edit_view(r):
    return {"ts": r["ts"], "path": r["path"], "old": r.get("old", ""), "new": r.get("new", ""),
            "method": r.get("method", ""), "source": r["source"]}


def _publish_view(r):
    return {"ts": r["ts"], "label": r.get("label", ""), "artifact_id": r.get("artifact_id", ""),
            "seq": r.get("seq"), "url": r.get("url", ""), "refused": r.get("refused")}


def choose_before(channel, rec, pasted, edits, seen_replies):
    if channel == "comment" and _clean_on_text(rec.get("on_text")):
        return {"source": "comment", "text": _clean_on_text(rec.get("on_text"))}
    if pasted:
        return {"source": "pasted", "text": max(pasted, key=len)}
    olds = [e for e in edits if e["old"].strip()]
    olds.sort(key=lambda e: not is_prose(e["path"]))
    if olds:
        return {"source": "edit", "text": olds[0]["old"]}
    if seen_replies:
        return {"source": "reply", "text": _cap_words(seen_replies[-1])}
    return {"source": "none", "text": ""}


def _final_pair(before, probes, paths, finals):
    """The paragraph of a committed document most like the before text, or
    like what an edit made of it, compared word by word with difflib."""
    if not before or not finals:
        return None
    probe_words = [[t[0] for t in _norm_tokens(p)][:600] for p in probes if p]
    best = None
    matcher = difflib.SequenceMatcher(autojunk=False)
    for path in paths:
        for doc in _finals_for(path, finals):
            for para in doc.get("paragraphs") or []:
                words = [t[0] for t in _norm_tokens(para)][:600]
                if not words:
                    continue
                matcher.set_seq1(words)
                for probe in probe_words:
                    matcher.set_seq2(probe)
                    if matcher.real_quick_ratio() < FINAL_MIN_RATIO or matcher.quick_ratio() < FINAL_MIN_RATIO:
                        continue
                    ratio = matcher.ratio()
                    if ratio >= FINAL_MIN_RATIO and (best is None or ratio > best[0]):
                        best = (ratio, para, doc)
    if best is None:
        return None
    ratio, para, doc = best
    return {"pair_quality": "final", "before": before, "after": para, "path": doc["path"],
            "repo": doc.get("repo", ""), "commit": doc.get("commit", ""), "version": "final",
            "ratio": round(ratio, 3)}


def _finals_for(path, finals):
    if not path:
        return []
    p = str(path)
    out = []
    for doc in finals:
        absolute = doc.get("abs_path") or ""
        if p == absolute or (not p.startswith("/") and absolute.endswith("/" + p.lstrip("./"))):
            out.append(doc)
    return out


def build_pairs(before, edits, publishes, commits, finals, target):
    pairs = []
    for e in edits:
        if not e["old"].strip() or e["old"] == e["new"]:
            continue
        quality = "literal" if e["method"] in LITERAL_METHODS else "exact"
        pairs.append({"pair_quality": quality, "before": e["old"], "after": e["new"], "path": e["path"],
                      "ts": e["ts"], "method": e["method"], "_score": similarity(before["text"], e["old"])})
    for c in commits:
        if c["status"] != "confirmed":
            continue
        pairs.append({"pair_quality": "commit", "before": "\n\n".join(c["removed"]),
                      "after": "\n\n".join(c["added"]), "path": c["path"], "repo": c["repo"],
                      "commit": c["commit"], "subject": c["subject"], "ts": c["ts"],
                      "_score": similarity(before["text"], "\n\n".join(c["removed"]))})
    if before["source"] in ("comment", "pasted", "edit"):
        paths = [target.get("path")] if target.get("kind") == "document" else []
        paths += [e["path"] for e in edits if is_prose(e["path"])]
        paths += [c["abs_path"] for c in commits if c["status"] == "confirmed"]
        probes = [before["text"]] + [e["new"] for e in edits
                                     if e["old"].strip() and before["text"] and
                                     (e["old"] in before["text"] or before["text"] in e["old"])]
        final = _final_pair(before["text"], probes, list(dict.fromkeys(p for p in paths if p)), finals)
        if final:
            pairs.append(dict(final, _score=final["ratio"]))
    labelled = [p for p in publishes if p["label"] and p["refused"] is False]
    if labelled and not any(p["pair_quality"] in ("exact", "literal", "commit") for p in pairs):
        for p in labelled:
            pairs.append({"pair_quality": "label-only", "before": before["text"], "after": None,
                          "label": p["label"], "ts": p["ts"], "_score": 0.0})
    tier = {"exact": 0, "literal": 1, "commit": 2, "final": 3, "label-only": 4}
    pairs.sort(key=lambda p: (tier[p["pair_quality"]], not is_prose(p.get("path")), -p["_score"]))
    for p in pairs:
        p["score"] = round(p.pop("_score"), 3)
    return pairs


# ---------- commits ----------

def _commit_view(c, status):
    return {"commit": c["commit"], "repo": c["repo"], "path": c["path"], "abs_path": c.get("abs_path", ""),
            "subject": c["subject"], "ts": c["ts"], "removed": c["removed"], "added": c["added"],
            "status": status}


def commits_for(start, stop, commits, commit_times, session, revisions_module):
    """The commits between an input and the next input plus the slack, each
    confirmed when its added text overlaps this session's own edits over the
    same stretch, and marked concurrent_unconfirmed otherwise."""
    end = (stop if stop else session.times[-1]) + COMMIT_SLACK
    lo = bisect.bisect_left(commit_times, start)
    hi = bisect.bisect_left(commit_times, end)
    if lo >= hi:
        return []
    own = [r.get("new") or "" for r in session.between(start, end, {"edit"})]
    return [_commit_view(c, "confirmed" if revisions_module.overlaps(c["added"], own) else "concurrent_unconfirmed")
            for c in commits[lo:hi]]


# ---------- episodes ----------

def build_episodes(records, parents=None, revisions=None, unanchored=None):
    """Episodes from the corpus records, in time order within each origin
    session. `parents` is parent_map() over the transcripts; without it every
    input is paired by time. `revisions` is what mine_revisions.py wrote.
    Publishes that come before a session's first input are appended to
    `unanchored` when a list is given."""
    revisions = revisions or []
    rv = _sibling("mine_revisions") if revisions else None
    commits = sorted((dict(c, _t=when(c["ts"])) for c in revisions if c.get("kind") == "commit" and when(c["ts"])),
                     key=lambda c: c["_t"])
    commit_times = [c["_t"] for c in commits]
    finals = [d for d in revisions if d.get("kind") == "final"]

    sessions, replies_by_file, input_uuids = {}, {}, {}
    far_past = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    for i, r in enumerate(records):
        r = dict(r, _i=i, _t=when(r.get("ts")) or far_past)
        sessions.setdefault(r["origin_session"], []).append(r)
        if r["kind"] == "reply" and r["source"] == "main":
            replies_by_file.setdefault(r["src"]["file"], []).append(r)
        if r["kind"] in ("human", "comment", "answer"):
            input_uuids.setdefault(r["src"]["file"], set()).add(r["uuid"])
    for recs in replies_by_file.values():
        recs.sort(key=lambda r: r["src"]["offset"])

    episodes = []
    for origin, recs in sessions.items():
        session = _Session(recs)
        publishes = [r for r in session.recs if r["kind"] == "publish"]
        inputs = [r for r in session.recs if r["kind"] in ("human", "comment", "answer")
                  and (r["kind"] != "comment" or r.get("role") == "owner")]
        if unanchored is not None:
            first = inputs[0]["_t"] if inputs else None
            unanchored.extend(_publish_view(p) | {"origin_session": origin} for p in publishes
                              if first is None or p["_t"] < first)
        counter = {}
        for n, rec in enumerate(inputs):
            start = rec["_t"]
            later = [x["_t"] for x in inputs[n + 1:] if x["_t"] > start]
            stop = later[0] if later else None
            earlier = [x["_t"] for x in inputs[:n] if x["_t"] < start]
            prev = earlier[-1] if earlier else far_past
            channel = _channel(rec)

            seen_replies = None
            if channel == "comment":
                seen = _clean_on_text(rec.get("on_text"))
                seen_replies = []
            elif channel == "answer":
                seen = rec.get("question") or ""
                seen_replies = []
            else:
                if channel == "typed":
                    seen_replies = _replies_on_path(rec, parents, input_uuids.get(rec["src"]["file"], set()),
                                                    replies_by_file)
                if seen_replies is None:
                    seen_replies = [r["text"] for r in session.between(prev, start, {"reply"}, source="main")]
                seen = "\n\n".join(seen_replies)
            text = rec.get("text") or ""
            pasted = [] if channel == "comment" or rec.get("suggested") else pasted_spans(text, seen)
            edits = [_edit_view(r) for r in session.between(start, stop, {"edit"})]
            pubs = [_publish_view(r) for r in session.between(start, stop, {"publish"})]
            target = find_target(rec, channel, seen, session, publishes)
            linked = commits_for(start, stop, commits, commit_times, session, rv) if commits else []
            before = choose_before(channel, rec, pasted, edits, seen_replies)
            pairs = build_pairs(before, edits, pubs, linked, finals, target)
            labels = [{"label": p["label"], "source": "publish"} for p in pubs if p["label"] and p["refused"] is False]
            labels += [{"label": c["subject"], "source": "commit"} for c in linked if c["status"] == "confirmed"]
            key = _episode_id(rec, 0)
            counter[key] = counter.get(key, 0) + 1
            episode = {
                "id": _episode_id(rec, counter[key] - 1),
                "origin_session": origin,
                "session": rec["session"],
                "ts": rec["ts"],
                "until": stop.isoformat().replace("+00:00", "Z") if stop else "",
                "channel": channel,
                "suggested": bool(rec.get("suggested")),
                "text": text,
                "words": _cut(text, pasted),
                "pasted": pasted,
                "seen": _cap_words(seen),
                "seen_words": len(seen.split()),
                "target": target,
                "response": {"edits": edits, "publishes": pubs, "commits": linked},
                "fix_labels": labels,
                "before": before,
                "pairs": pairs[:MAX_PAIRS],
                "pairs_total": len(pairs),
                "src": rec["src"],
            }
            for field in ("comment_ts", "on_text", "location", "anchor", "url", "question"):
                if rec.get(field):
                    episode[field] = _clean_on_text(rec[field]) if field == "on_text" else rec[field]
            episodes.append(episode)
    episodes.sort(key=lambda e: (when(e["ts"]) or far_past, e["id"]))
    return episodes


# ---------- command line ----------

def _read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, help="the jsonl claude_prose.py wrote, with every kind")
    ap.add_argument("--revisions", default="", help="the jsonl mine_revisions.py wrote")
    ap.add_argument("--out", required=True, help="where the episodes go, one per line")
    ap.add_argument("--unanchored", default="", help="where publishes made before any input go")
    ap.add_argument("--no-transcripts", action="store_true",
                    help="pair every input by time instead of reading parent links from the transcripts")
    args = ap.parse_args(argv)

    started = time.time()
    records = _read_jsonl(args.corpus)
    parents = None
    if not args.no_transcripts:
        files = sorted({r["src"]["file"] for r in records if r["kind"] in ("human", "answer", "reply")})
        parents = parent_map(files)
        print(f"parent links read from {len(files)} transcripts in {time.time() - started:.0f}s", file=sys.stderr)
    revisions = _read_jsonl(args.revisions) if args.revisions else []
    unanchored = []
    episodes = build_episodes(records, parents=parents, revisions=revisions, unanchored=unanchored)
    with open(args.out, "w", encoding="utf-8") as out:
        for e in episodes:
            out.write(json.dumps(e, ensure_ascii=False) + "\n")
    if args.unanchored:
        with open(args.unanchored, "w", encoding="utf-8") as out:
            for p in unanchored:
                out.write(json.dumps(p, ensure_ascii=False) + "\n")
    counts = {}
    for e in episodes:
        counts[e["channel"]] = counts.get(e["channel"], 0) + 1
    print(f"{len(episodes)} episodes: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())), file=sys.stderr)
    print(f"{sum(1 for e in episodes if e['suggested'])} suggested, {len(unanchored)} publishes before any input, "
          f"{time.time() - started:.0f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

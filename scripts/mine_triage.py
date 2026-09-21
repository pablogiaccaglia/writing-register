#!/usr/bin/env python3
"""Pick the episodes worth reading, by cheap signals and without a model.

Each episode gets eight features, all read from its own record:

- `response_edits_prose`: the response edits markdown or HTML, or publishes;
- `quote_overlap`: the person pasted part of what they were looking at, or
  put a phrase of it in quotation marks;
- `term_question`: a question whose words include a term from what they were
  looking at that they had not used themselves before;
- `evaluative`: words of judgement about writing, including approvals, and
  "details" and "misleading", the complaints the voice's evidence repeats;
- `document_noun`: a word naming part of a document (README, legend, axis,
  image, report...);
- `repetition`: the message repeats an earlier one, in any session (character
  5-gram Jaccard of at least 0.3);
- `label_link`, `commit_link`: a labelled publish, or a confirmed commit to a
  followed document, came in the same episode.

Speech-to-text slips (slips.toml) are applied before matching, so a dictated
"Redmi" matches README, but the words are always quoted as they were typed.

An episode is a candidate when it has one strong signal (`quote_overlap`,
`term_question`, or a label or commit link together with evaluative wording or
a document noun), or any two weak ones. Owner comments and typed answers are
candidates by construction, and a prompt accepted from Claude's suggestion
never is.

The report measures recall against three gold sets: the earlier keyword
filter, the dated quotes in the voice's evidence, and the quotes in a page
standards document. It also lists every labelled publish that no request from
the person explains, which are Claude's own fixes and never the person's
evidence.
"""
import argparse
import json
import pathlib
import random
import re
import sys
import time
import tomllib

KEYWORDS = ["wording", "explain", "unclear", "confusing", "rewrite", "simpler", "math", "formula", "equation",
            "step", "derivation", "notation", "symbol", "define", "clarify", "reader", "document", "artifact",
            "too dense", "too long", "jargon", "not clear", "i dont understand", "why", "what does"]

_QUESTION = re.compile(r"\bwhat\s+is\b|\bwhat's\b|\bwhats\b|\bwhat\s+are\b|\bwhich\b|\?\?\?|"
                       r"\bi\s+don'?t\s+understand\b|\bi\s+do\s+not\s+understand\b|\bwhat\s+does\b|"
                       r"\bwhat\s+do\s+you\s+mean\b|\bwhy\b|\bmeaning\s+of\b")
_EVALUATIVE = re.compile(
    r"\b(unclear|confusing|confused|confuses|rewrite|rewritten|re-write|simpler|simplify|explain|explained|"
    r"explaining|define|defined|clarify|clarified|jargon|too\s+dense|too\s+long|not\s+clear|isn'?t\s+clear|"
    r"readable|unreadable|readability|wording|reword|phrasing|out\s+of\s+the\s+blue|robotic|verbose|fluff|"
    r"blob|ai\s+slop|slop|details?|misleading|hard\s+to\s+(?:read|follow|understand)|i\s+don'?t\s+understand|"
    r"much\s+better|good|very\s+good|i\s+approve|looks\s+good|perfect|nice|well\s+done|love\s+it|great)\b")
_DOC_NOUN = re.compile(
    r"\b(readme|pages?|sections?|tables?|figures?|legends?|captions?|formulas?|formulae|symbols?|steps?|"
    r"derivations?|examples?|layouts?|links?|maths?|notations?|plots?|axis|axes|scales?|documents?|docs?|"
    r"artifacts?|paragraphs?|sentences?|headings?|titles?|charts?|images?|reports?)\b")
_WRITING_CONTEXT = re.compile(r"\b(text|texts|write|written|writing|wording|prose|sentence|sentences|paragraph|"
                              r"doc|docs|document|readme|page|style|reads|tone|llm|ai|generated|copy|card)\b")
_QUOTED = re.compile(r'["“”]([^"“”\n]{3,200})["“”]')
_TERM = re.compile(r"[A-Za-z0-9][\w.\-]*\w|\w")
STOPWORDS = set("""a about above after again against all also am an and any are aren't as at be because been before
being below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't dont down
during each few for from further had hadn't has hasn't have haven't having he her here hers herself him himself his
how i i'd i'll i'm i've if in into is isn't it it's its itself just let's lets me more most mustn't my myself no nor
not now of off on once only or other ought our ours ourselves out over own same she should shouldn't so some such
than that that's the their theirs them themselves then there there's these they this those through to too under
until up very was wasn't we were weren't what what's whats when where which while who whom why with won't would
wouldn't you your yours yourself yourselves ok okay yes yeah please thanks thank go on get got make made see seen
one two use used using like well also still now then here there thing things something anything way ways mean
means meant know think want need does done doing say says said tell really much many some any every via""".split())
PROSE_SUFFIXES = {".md", ".markdown", ".html", ".htm", ".txt", ".rst"}
REPEAT_JACCARD = 0.3
MIN_GRAMS = 20
WEAK = ["response_edits_prose", "evaluative", "document_noun", "repetition", "label_link", "commit_link"]


# ---------- slips ----------

def load_slips(path):
    data = tomllib.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return [{"heard": s["heard"], "meant": s["meant"], "context": s.get("context", "")} for s in data.get("slip", [])]


def apply_slips(text, slips):
    """The text with each dictation slip replaced by what was meant. A slip
    with a context applies only when the message is about written text."""
    for slip in slips:
        if slip.get("context") and not _WRITING_CONTEXT.search(text.lower()):
            continue
        text = re.sub(rf"(?<!\w){re.escape(slip['heard'])}(?!\w)", slip["meant"], text, flags=re.I)
    return text


# ---------- features ----------

def _terms(text):
    return [m.group(0).lower() for m in _TERM.finditer(text or "")]


def _content(term, seen_upper):
    if term in STOPWORDS:
        return False
    return len(term) >= 3 or any(ch.isdigit() for ch in term) or term in seen_upper


def _grams(text):
    norm = " ".join((text or "").lower().split())
    return {norm[i:i + 5] for i in range(len(norm) - 4)}


def _is_prose(path):
    return pathlib.PurePath(path or "").suffix.lower() in PROSE_SUFFIXES


def _norm_ws(text):
    return " ".join((text or "").split())


def _quote_overlap(ep):
    if ep.get("pasted"):
        return True
    seen = _norm_ws(ep.get("seen")).lower()
    if not seen:
        return False
    for m in _QUOTED.finditer(ep.get("words") or ""):
        phrase = _norm_ws(m.group(1)).lower().strip(" .,;:")
        if len(phrase.split()) >= 2 and phrase in seen:
            return True
    return False


def triage(episodes, slips):
    """Features, the candidate decision and its reason for each episode, in
    the order given. Terms and repetition are judged against the person's
    earlier messages, taken in time order across every session."""
    order = sorted(range(len(episodes)), key=lambda i: (episodes[i].get("ts") or "", i))
    vocabulary = set()
    history = []  # (grams, size) of earlier messages
    results = [None] * len(episodes)
    for i in order:
        ep = episodes[i]
        words = ep.get("words") or ""
        matched = apply_slips(words, slips)
        low = matched.lower()
        seen = ep.get("seen") or ""
        seen_terms = set(_terms(seen))
        seen_upper = {t.lower() for t in _TERM.findall(seen) if any(c.isupper() for c in t)}
        own_terms = [t for t in _terms(matched) if _content(t, seen_upper)]
        new_terms = sorted({t for t in own_terms if t in seen_terms and t not in vocabulary})
        response = ep.get("response") or {}
        labels = ep.get("fix_labels") or []
        grams = _grams(matched)
        repeated = False
        if len(grams) >= MIN_GRAMS and not ep.get("suggested"):
            for other, size in history:
                small, big = min(size, len(grams)), max(size, len(grams))
                if small / big < REPEAT_JACCARD:
                    continue
                inter = len(grams & other)
                if inter / (size + len(grams) - inter) >= REPEAT_JACCARD:
                    repeated = True
                    break
        features = {
            "response_edits_prose": any(_is_prose(e.get("path")) for e in response.get("edits") or [])
            or any(p.get("refused") is False for p in response.get("publishes") or []),
            "quote_overlap": _quote_overlap(ep),
            "term_question": bool(_QUESTION.search(low)) and bool(new_terms),
            "evaluative": bool(_EVALUATIVE.search(low)),
            "document_noun": bool(_DOC_NOUN.search(low)),
            "repetition": repeated,
            "label_link": any(x.get("source") == "publish" for x in labels),
            "commit_link": any(x.get("source") == "commit" for x in labels),
        }
        candidate, reason = decide(ep, features)
        results[i] = {"id": ep["id"], "channel": ep["channel"], "suggested": bool(ep.get("suggested")),
                      "features": features, "candidate": candidate, "reason": reason,
                      "new_terms": new_terms if features["term_question"] else [],
                      "words": words, "matched_text": matched}
        if not ep.get("suggested"):
            vocabulary.update(_terms(matched))
            if len(grams) >= MIN_GRAMS:
                history.append((grams, len(grams)))
    return results


def decide(ep, f):
    if ep.get("suggested"):
        return False, "suggested prompt"
    if ep["channel"] in ("comment", "answer"):
        return True, f"{ep['channel']} (by construction)"
    strong = [k for k in ("quote_overlap", "term_question") if f[k]]
    if (f["label_link"] or f["commit_link"]) and (f["evaluative"] or f["document_noun"]):
        strong.append("link with " + ("evaluative wording" if f["evaluative"] else "a document noun"))
    if strong:
        return True, "strong: " + ", ".join(strong)
    weak = [k for k in WEAK if f[k]]
    if len(weak) >= 2:
        return True, "two weak signals: " + ", ".join(weak)
    if weak:
        return False, "one weak signal: " + weak[0]
    return False, "no signal"


# ---------- gold sets ----------

def _split_joined(content):
    """`"a" and "b"` into its quotes, but only where both sides keep their own
    quotation marks balanced, so a quote that itself quotes stays whole."""
    parts = content.split('" and "')
    out, buf = [], ""
    for part in parts:
        buf = part if not buf else buf + '" and "' + part
        if buf.count('"') % 2 == 0:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def about_quotes(text):
    """The dated verbatim quotes of a voice's evidence: blockquote lines that
    open with a quotation mark, and quoted spans followed by a date."""
    quotes = []
    for line in text.splitlines():
        if line.startswith('> "'):
            m = re.search(r"\s*\(\d{4}-\d\d-\d\d", line)
            body = line[2:m.start()].rstrip() if m else line[2:].rstrip()
            if body.startswith('"') and body.endswith('"'):
                quotes.extend(q.strip() for q in _split_joined(body[1:-1]))
            continue
        for m in re.finditer(r'"([^"\n]+)"\s*\(\d{4}-\d\d-\d\d', line):
            quotes.append(m.group(1).strip())
    return [q for q in dict.fromkeys(quotes) if q]


def standards_quotes(text):
    """Quotes of the person in a standards document: the quoted spans inside
    a parenthesis that opens with a quotation mark."""
    quotes = []
    for m in re.finditer(r'\((["“][^()]*)\)', text):
        quotes += [q.strip() for q in re.findall(r'["“]([^"“”]+)["”]', m.group(1))]
    return [q for q in dict.fromkeys(quotes) if q]


def _pieces(quote):
    return [p.strip() for p in re.split(r"\[[^\]]*\]", _norm_ws(quote)) if p.strip()]


def _plain(text):
    """Lowercased, whitespace normalised, apostrophes dropped: a document that
    quotes the person sometimes restores one they did not type."""
    return re.sub(r"['’]", "", _norm_ws(text).lower())


def find_quote(quote, episodes):
    """The episodes whose text holds the quote, whitespace normalised, case and
    apostrophes ignored, a bracketed gap such as [...] matching anything."""
    pieces = [_plain(p) for p in _pieces(quote)]
    if not pieces:
        return []
    hits = []
    for ep in episodes:
        if ep.get("suggested"):
            continue
        text = _plain(ep.get("text"))
        pos = 0
        for piece in pieces:
            pos = text.find(piece, pos)
            if pos < 0:
                break
            pos += len(piece)
        else:
            hits.append(ep["id"])
    return hits


def keyword_gold(episodes):
    """The earlier keyword filter: typed messages matching the keyword list."""
    pattern = re.compile(r"\b(" + "|".join(re.escape(k).replace(r"\ ", r"\s+") for k in KEYWORDS) + r")\b")
    return [ep for ep in episodes if ep["channel"] in ("typed", "queued") and not ep.get("suggested")
            and pattern.search((ep.get("text") or "").lower())]


def _keywords_in(text):
    low = (text or "").lower()
    return [k for k in KEYWORDS if re.search(r"\b" + re.escape(k).replace(r"\ ", r"\s+") + r"\b", low)]


# ---------- self-initiated labels ----------

def _stems(text):
    return {t[:5] for t in _terms(text) if len(t) >= 4 and t not in STOPWORDS}


def self_initiated(episodes, results, unanchored=()):
    """Labelled publishes that no input from the person explains."""
    out = []
    for p in unanchored:
        if p.get("label") and p.get("refused") is False:
            out.append({"label": p["label"], "ts": p.get("ts", ""), "origin_session": p.get("origin_session", ""),
                        "episode": "", "why": "before any input in its session"})
    by_id = {r["id"]: r for r in results}
    for ep in episodes:
        for label in ep.get("fix_labels") or []:
            if label.get("source") != "publish":
                continue
            r = by_id[ep["id"]]
            if ep.get("suggested"):
                why = "follows a suggested prompt"
            elif r["candidate"] or _stems(label["label"]) & _stems(ep.get("words")):
                continue
            else:
                why = "the input asks for nothing about the text: " + r["reason"]
            out.append({"label": label["label"], "ts": ep.get("ts", ""), "origin_session": ep["origin_session"],
                        "episode": ep["id"], "why": why})
    return out


# ---------- report ----------

def _quote_block(text, limit=None):
    text = text if limit is None or len(text) <= limit else text[:limit] + " [...]"
    return "\n".join("> " + line if line else ">" for line in text.splitlines() or [""])


def _gold_section(title, quotes, episodes, by_id, source):
    lines = [f"## {title}", "", f"Source: `{source}`.", ""]
    usable = [q for q in quotes if len(_norm_ws(" ".join(_pieces(q))).split()) >= 3]
    short = [q for q in quotes if q not in usable]
    found, missed, absent = [], [], []
    for q in usable:
        hits = find_quote(q, episodes)
        if not hits:
            absent.append(q)
            continue
        found.append(q)
        misses = [h for h in hits if not by_id[h]["candidate"]]
        if misses:
            missed.append((q, misses))
    recall_n = len(found) - len(missed)
    lines.append(f"{len(quotes)} quotes read, {len(short)} skipped as shorter than three words, "
                 f"{len(found)} found verbatim in the corpus, {recall_n} of those in candidate episodes "
                 f"(recall {recall_n}/{len(found)}" + (f" = {recall_n / len(found):.0%})." if found else ")."))
    lines.append("")
    if missed:
        lines.append("Misses:")
        lines.append("")
        for q, ids in missed:
            for h in ids:
                lines.append(f"- \"{q}\" in `{h}`, excluded: {by_id[h]['reason']}")
        lines.append("")
    else:
        lines += ["No misses.", ""]
    if short:
        lines.append("Skipped as too short to search: " + "; ".join(f'"{q}"' for q in short) + ".")
        lines.append("")
    if absent:
        lines.append(f"Not in this corpus ({len(absent)}), most from outside its date window or projects:")
        lines.append("")
        lines += [f"- \"{q[:160]}\"" for q in absent]
        lines.append("")
    return lines, {"found": len(found), "recall": recall_n, "missed": missed, "absent": len(absent)}


def report(episodes, results, selfs, about_text="", about_path="", standards_text="", standards_path="",
           seed=0, runtime=0.0):
    by_id = {r["id"]: r for r in results}
    eps_by_id = {e["id"]: e for e in episodes}
    cands = [r for r in results if r["candidate"]]
    channels = {}
    for e in episodes:
        channels[e["channel"]] = channels.get(e["channel"], 0) + 1
    lines = ["# Triage of the episodes", "",
             f"{len(episodes)} episodes ({', '.join(f'{k} {v}' for k, v in sorted(channels.items()))}), of which "
             f"{sum(1 for e in episodes if e.get('suggested'))} are suggested prompts. "
             f"{len(cands)} are candidates. Runtime {runtime:.0f}s.", ""]
    lines += ["## Signals", "", "| signal | episodes | among candidates |", "|---|---:|---:|"]
    for k in ["quote_overlap", "term_question"] + WEAK:
        lines.append(f"| {k} | {sum(r['features'][k] for r in results)} | {sum(r['features'][k] for r in cands)} |")
    lines.append("")
    reasons = {}
    for r in results:
        key = r["reason"].split(":")[0]
        reasons[key] = reasons.get(key, 0) + 1
    lines += ["Decisions: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])) + ".", ""]

    best, every = {}, {}
    for r in cands:
        pairs = eps_by_id[r["id"]].get("pairs") or []
        q = pairs[0]["pair_quality"] if pairs else "none"
        best[q] = best.get(q, 0) + 1
        for p in pairs:
            every[p["pair_quality"]] = every.get(p["pair_quality"], 0) + 1
    order = ["exact", "literal", "commit", "final", "label-only", "none"]
    lines += ["## Pairs among the candidates", "", "| pair_quality | best pair | all pairs |", "|---|---:|---:|"]
    lines += [f"| {q} | {best.get(q, 0)} | {every.get(q, 0) if q != 'none' else ''} |" for q in order]
    lines.append("")

    gold = keyword_gold(episodes)
    missed = [e for e in gold if not by_id[e["id"]]["candidate"]]
    lines += ["## Gold set 1: the keyword filter", "",
              f"{len(gold)} typed messages match the keyword list; {len(gold) - len(missed)} are candidates "
              f"(recall {(len(gold) - len(missed)) / max(1, len(gold)):.0%}). Each miss follows with its "
              "keywords and why it was excluded.", ""]
    for e in missed:
        r = by_id[e["id"]]
        lines.append(f"- `{e['id']}` ({', '.join(_keywords_in(e['text']))}; {r['reason']}): "
                     + _norm_ws(e["text"])[:400])
    lines.append("")
    summary = {"gold1": (len(gold), len(gold) - len(missed))}
    if about_text:
        sec, summary["gold2"] = _gold_section("Gold set 2: quotes in the voice's evidence", about_quotes(about_text),
                                              episodes, by_id, about_path)
        lines += sec
    if standards_text:
        sec, summary["gold3"] = _gold_section("Gold set 3: quotes in the page standards",
                                              standards_quotes(standards_text), episodes, by_id, standards_path)
        lines += sec

    lines += ["## Self-initiated labels", "",
              f"{len(selfs)} labelled publishes trace back to no request from the person. A label traces back "
              "when its episode's input is a candidate, or shares a word stem with the label; a label after a "
              "suggested prompt, or before any input, never does. These are Claude's own fixes and are never "
              "used as the person's evidence.", ""]
    lines += [f"- {s['ts'][:16]} `{s['origin_session'][:8]}` \"{s['label']}\": {s['why']}" for s in selfs]
    lines.append("")

    excluded = [e for e in episodes if not by_id[e["id"]]["candidate"] and not e.get("suggested")
                and e["channel"] in ("typed", "queued")]
    sample = random.Random(seed).sample(excluded, min(20, len(excluded)))
    lines += ["## Twenty excluded messages, for a manual check", "",
              f"Drawn at random (seed {seed}) from the {len(excluded)} typed messages that are not candidates.", ""]
    for e in sample:
        lines += [f"**`{e['id']}`**, {by_id[e['id']]['reason']}", "", _quote_block(e["text"]), ""]
    summary["selfs"] = len(selfs)
    summary["best"] = best
    return "\n".join(lines) + "\n", summary


# ---------- command line ----------

def _read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episodes", required=True)
    ap.add_argument("--unanchored", default="")
    ap.add_argument("--slips", default="")
    ap.add_argument("--about", action="append", default=[],
                    help="a voice's about.md, or a directory of its evidence files, for gold set 2; repeatable")
    ap.add_argument("--standards", default="", help="a page standards document, for gold set 3")
    ap.add_argument("--report", required=True, help="where triage.md goes")
    ap.add_argument("--out", default="", help="where the per-episode triage goes, one line each")
    ap.add_argument("--candidates", default="", help="where the candidate episodes go, whole")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    started = time.time()
    episodes = _read_jsonl(args.episodes)
    unanchored = _read_jsonl(args.unanchored) if args.unanchored else []
    slips = load_slips(args.slips) if args.slips else []
    results = triage(episodes, slips)
    selfs = self_initiated(episodes, results, unanchored)
    about_files = []
    for item in args.about:
        path = pathlib.Path(item)
        about_files += sorted(path.rglob("*.md")) if path.is_dir() else [path]
    about = "\n".join(f.read_text(encoding="utf-8") for f in about_files)
    standards = pathlib.Path(args.standards).read_text(encoding="utf-8") if args.standards else ""
    text, summary = report(episodes, results, selfs, about, ", ".join(args.about), standards, args.standards,
                           seed=args.seed, runtime=time.time() - started)
    pathlib.Path(args.report).write_text(text, encoding="utf-8")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    if args.candidates:
        by_id = {r["id"]: r for r in results}
        with open(args.candidates, "w", encoding="utf-8") as fh:
            for e in episodes:
                if by_id[e["id"]]["candidate"]:
                    fh.write(json.dumps(dict(e, triage=by_id[e["id"]]), ensure_ascii=False) + "\n")
    print(f"{len(episodes)} episodes, {sum(r['candidate'] for r in results)} candidates, "
          f"{len(selfs)} self-initiated labels, {time.time() - started:.0f}s", file=sys.stderr)
    print(json.dumps({k: v for k, v in summary.items() if k != "gold2" and k != "gold3"}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

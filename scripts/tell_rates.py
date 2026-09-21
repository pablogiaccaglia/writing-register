#!/usr/bin/env python3
"""Score candidate machine-writing patterns on text that already exists.

No model calls. The question each pattern has to answer before it may ship:
does it fire more on machine-written prose than on prose a person has fixed?
Four corpora answer it:

- `replay-old` and `replay-new`: documents before and after `wr humanize`,
  kept as `*.old.md` and `*.new.md` pairs under `--pairs`. A real tell falls
  between the two.
- `verdict-old` and `verdict-new`: the sentence pairs inside saved checker
  verdicts, the JSON files under `--verdicts`.
- `reply`, `markdown`: Claude's own prose from the transcripts, extracted by
  scripts/claude_prose.py, which is the text the layer is meant to change.
- `human`: what the person typed. A pattern that fires here is measuring
  English, not machine writing.

A corpus whose option is not given is left out of the table.
"""
import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))

_FENCE = re.compile(r"(?ms)^```.*?^```\s*?$")
_CODE = re.compile(r"`[^`\n]+`")
_LINK = re.compile(r"\]\([^)\s]+\)")

# (id, name, strength, pattern). Sentence-initial patterns carry (?m)^.
RULES = [
    ("s1", "not X but Y", "strong", r"\bnot (?:just|only|merely|simply)\b[^.!?\n]{0,70}?,\s*but\b"),
    ("s1b", "it's not X, it's Y", "strong", r"\b(?:it's|it is|this isn't|these aren't)\b[^.!?\n]{0,50},\s*(?:it's|it is|they're)\b"),
    ("s2", "one-line closer", "strong", r"(?i)\b(?:that is the real win|read that again|let that sink in|and that'?s the point|that'?s the whole point|therein lies)\b"),
    ("s3", "saying that sounds deep", "strong", r"(?i)\b(?:at its core|the beauty of|the magic of|speaks volumes|lies at the heart)\b"),
    ("s4", "staged run-up", "strong", r"(?im)^\s*(?:here'?s the thing|let'?s be clear|make no mistake|the truth is|what'?s really)\b"),
    ("s5", "arguing with no one", "strong", r"(?im)^\s*(?:it'?s not that|this isn'?t about|nobody is saying|no one is claiming)\b"),
    ("s6", "forced triad", "weak", r"\b\w+, \w+,? and \w+\b"),
    ("s8", "em or en dash", "weak", r"[—–]"),
    ("s9", "stacked qualifier", "weak", r"(?i)\b(?:quite possibly|arguably one of|perhaps the most|one of the most)\b"),
    ("s12", "stock AI word", "strong", r"(?i)\b(?:delve|tapestry|testament|meticulous(?:ly)?|pivotal|showcase[sd]?|vibrant|interplay|intricate|intricacies|garner(?:ed|s)?|fostering|bolster(?:ed|s)?|deep dive|realm of)\b"),
    ("s13", "inflated significance", "strong", r"(?i)\b(?:plays? a (?:crucial|vital|key|pivotal) role|game.changer|stands? as a testament|cannot be overstated)\b"),
    ("s15", "shallow -ing rider", "strong", r",\s+(?:highlighting|underscoring|emphasi[sz]ing|reflecting|symboli[sz]ing|showcasing|fostering|cultivating|encompassing)\b"),
    ("s16", "sales language", "strong", r"(?i)\b(?:unlock(?:s|ing)? the|supercharge|effortless(?:ly)?|seamless(?:ly)?|next level|best-in-class|cutting.edge)\b"),
    ("s17", "borrowed authority", "strong", r"(?i)\b(?:experts? (?:say|argue|agree)|studies show|industry reports|it is widely (?:known|believed)|observers have)\b"),
    ("s18", "avoiding is and has", "weak", r"(?i)\b(?:serves as|stands as|functions as|boasts)\b"),
    ("s21", "curly quotes", "weak", r"[“”‘’]"),
    ("s22", "chatbot residue", "strong", r"(?i)\b(?:i hope this helps|great question|certainly!|feel free to|let me know if you)\b"),
    ("s23", "knowledge-limit disclaimer", "strong", r"(?i)\b(?:as of my (?:last|knowledge)|in the provided sources|it is believed that|while specific details)\b"),
]
# A second set, from what separated edited text from unedited text in earlier
# measurements: named against vague references (16.4 per 1,000 words in a
# document its author corrected over many rounds, 0.50 in plans nobody edited),
# six signals from an earlier before-and-after comparison, and the hedging and
# process vocabulary a voice commonly bans.
RULES += [
    ("v1", "vague reference", "strong", r"(?i)\b(?:see (?:below|above)|as (?:described|discussed|mentioned|noted) (?:above|below|earlier|previously)|the (?:above|following)\b)"),
    ("v2", "named reference", "strong", r"(?i)\b(?:section|step|table|figure|line)s?\s+\d"),
    ("v3", "back-referring opener", "weak", r"(?m)^\s*(?:This|That|These|Those|It)\s+(?:is|was|means|gives|makes|shows|allows)\b"),
    ("v4", "additive connective", "weak", r"(?i)\b(?:also|additionally|moreover|furthermore|in addition)\b"),
    ("v5", "journey words", "strong", r"(?i)\b(?:we (?:found|fixed|solved|discovered)|turned out|it turns out|initially|previously|as it happens)\b"),
    ("v6", "hedge", "weak", r"(?i)\b(?:might|could potentially|perhaps|it seems|appears to be|likely that)\b"),
    ("v7", "note that", "strong", r"(?i)\b(?:note that|it'?s worth noting|importantly|keep in mind that)\b"),
    ("v8", "parenthetical gloss", "weak", r"\([^)]{12,}\)"),
    ("v9", "process vocabulary", "strong", r"(?i)\b(?:leverage[sd]?|utilize[sd]?|facilitate[sd]?|robust(?:ness)?|comprehensive|holistic|streamlin\w+)\b"),
    ("v10", "bold label opener", "weak", r"(?m)^\s*[-*]\s+\*\*[^*]{1,40}[.:]\*\*"),
]
COMPILED = [(i, n, s, re.compile(p)) for i, n, s, p in RULES]


def prose(text: str) -> str:
    """The text with code fences, inline code and link targets blanked out."""
    text = _FENCE.sub("\n", text)
    text = _CODE.sub("`x`", text)
    return _LINK.sub("](x)", text)


def count(text: str) -> dict:
    clean = prose(text)
    return {rid: len(rx.findall(clean)) for rid, _, _, rx in COMPILED}


def corpus_rates(texts) -> tuple[dict, int]:
    total, words = {rid: 0 for rid, *_ in COMPILED}, 0
    for text in texts:
        words += len(text.split())
        for rid, hits in count(text).items():
            total[rid] += hits
    return total, words


def replay_texts(root, side: str):
    if not root:
        return
    for pair in sorted(pathlib.Path(root).rglob(f"*.{side}.md")):
        yield pair.read_text(encoding="utf-8", errors="replace")


def verdict_texts(root, side: str):
    if not root:
        return
    for f in sorted(pathlib.Path(root).expanduser().rglob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for v in rec.get("verdicts") or []:
            text = v.get(side) or ""
            if text.strip():
                yield text


def prose_texts(path, kind, since="", until="", source=""):
    """Claude's prose of one kind, optionally inside a date window. The voice
    has been injected into every session since 2026-09-14, so the window is how
    the corpus answers whether that steering moved anything."""
    if not path:
        return
    for line in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") != kind:
            continue
        if source and rec.get("source") != source:
            continue
        stamp = rec.get("ts") or ""
        if since and stamp < since:
            continue
        if until and stamp >= until:
            continue
        yield rec.get("text") or ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prose", default="", help="the jsonl written by claude_prose.py")
    ap.add_argument("--pairs", default="", help="a folder of *.old.md and *.new.md rewrite pairs")
    ap.add_argument("--verdicts", default="", help="a folder of saved checker verdicts (JSON)")
    ap.add_argument("--voice-from", default="2026-09-14",
                    help="the date the voice began reaching every session")
    args = ap.parse_args(argv)

    corpora = {
        "replay-old": replay_texts(args.pairs, "old"),
        "replay-new": replay_texts(args.pairs, "new"),
        "verdict-old": verdict_texts(args.verdicts, "old"),
        "verdict-new": verdict_texts(args.verdicts, "new"),
        "main-before": prose_texts(args.prose, "reply", until=args.voice_from, source="main"),
        "main-after": prose_texts(args.prose, "reply", since=args.voice_from, source="main"),
        "sub-before": prose_texts(args.prose, "reply", until=args.voice_from, source="subagent"),
        "sub-after": prose_texts(args.prose, "reply", since=args.voice_from, source="subagent"),
        "md-after": prose_texts(args.prose, "markdown", since=args.voice_from),
        "human": prose_texts(args.prose, "human"),
    }
    table, sizes = {}, {}
    for name, texts in corpora.items():
        total, words = corpus_rates(texts)
        sizes[name] = words
        table[name] = {rid: (1000 * n / words if words else 0.0) for rid, n in total.items()}

    names = list(corpora)
    print("rate per 1,000 words\n")
    print(f"{'rule':34}" + "".join(f"{n:>13}" for n in names))
    print(f"{'words':34}" + "".join(f"{sizes[n]:>13,}" for n in names))
    for rid, name, strength, _ in COMPILED:
        label = f"{rid} {name[:24]} ({strength[0]})"
        print(f"{label:34}" + "".join(f"{table[n][rid]:>13.2f}" for n in names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

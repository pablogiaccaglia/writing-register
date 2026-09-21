#!/usr/bin/env python3
"""Reading packets for the candidates triage kept, and the check on readers.

The episodes worth reading (scripts/mine_triage.py) are read by model readers,
each given a share of packets small enough to read with care. A packet holds
the person's words verbatim, the end of what they were looking at, at most
three before-and-after pairs named by id, the labels of the fixes that
followed, and the three existing voice rules closest to what they said.

A reader returns one JSON line per point it finds and never retypes evidence:
it quotes the person and names pairs by id. `verify` then checks each quote
against the person's own words, refuses a quote found only in what they were
looking at (text they pasted or reacted to is not something they said), and
refuses a pair id the packet does not have.

    build   candidates.jsonl  -> packets.json and P01.md ... Pnn.md
    verify  packets.json + readers' JSONL -> verified.jsonl and rejected.jsonl
    agree   first reads + a blind second read of a sample -> agreement figures

Standard library only; no model is called here. The readers are subagents run
by whoever orchestrates the mining.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

SEEN_WORDS = 250
WORDS_MAX = 600
PAIR_WORDS = 120
PAIRS_MAX = 3
RULES_SHOWN = 3
_QUALITY = {"exact": 0, "literal": 1, "commit": 2, "final": 3, "label-only": 4}


def _norm(text: str) -> str:
    """Lowercase, apostrophes dropped, whitespace collapsed: for matching only."""
    text = text.lower().replace("’", "'").replace("'", "")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _last_words(text: str, n: int) -> str:
    words = (text or "").split()
    return " ".join(words[-n:])


def _first_words(text: str, n: int) -> str:
    words = (text or "").split()
    return " ".join(words[:n]) + (" [...]" if len(words) > n else "")


def _closest(words: str, rules: list[tuple[str, str]], n: int = RULES_SHOWN) -> list[tuple[str, str]]:
    target = set(_norm(words).split())

    def score(rule):
        vocab = set(_norm(rule[1]).split())
        overlap = len(target & vocab) / (len(vocab) or 1)
        return overlap + difflib.SequenceMatcher(None, _norm(words), _norm(rule[1])).ratio() / 4

    return sorted(rules, key=score, reverse=True)[:n]


def _label(item) -> str:
    """A fix label as text: a version label or commit subject, with where it came from."""
    if isinstance(item, dict):
        text = item.get("label") or ""
        return f"{text} ({item['source']})" if item.get("source") else text
    return str(item)


def build(candidates, rules: list[tuple[str, str]]) -> list[dict]:
    """Packets from candidate episodes, suggested prompts dropped and repeated
    messages read once (the repeats are listed on the packet)."""
    packets: list[dict] = []
    by_text: dict[str, dict] = {}
    for c in candidates:
        if c.get("suggested"):
            continue
        key = _norm(c.get("words") or "")
        if not key:
            continue
        if key in by_text:
            by_text[key]["repeats"].append(c["id"])
            continue
        pairs = sorted(c.get("pairs") or [], key=lambda p: _QUALITY.get(p.get("pair_quality"), 9))
        packet = {
            "id": c["id"],
            "session": c.get("origin_session", ""),
            "date": (c.get("ts") or "")[:10],
            "channel": c.get("channel", ""),
            "words": _first_words(c.get("words") or "", WORDS_MAX) if len((c.get("words") or "").split()) > WORDS_MAX
                     else c.get("words") or "",
            "seen": _last_words(c.get("seen") or "", SEEN_WORDS),
            "pasted": list(c.get("pasted") or []),
            "pairs": [{"id": f"{c['id']}#p{i}", "quality": p.get("pair_quality", ""),
                       "before": _first_words(p.get("before") or "", PAIR_WORDS),
                       "after": _first_words(p.get("after") or "", PAIR_WORDS)}
                      for i, p in enumerate(pairs[:PAIRS_MAX], 1)],
            "labels": [_label(x) for x in c.get("fix_labels") or []],
            "commits": [x.get("subject", "") for x in (c.get("response") or {}).get("commits") or []][:3],
            "rules": _closest(c.get("words") or "", rules),
            "repeats": [],
        }
        by_text[key] = packet
        packets.append(packet)
    return packets


def render(packet: dict) -> str:
    """One packet as markdown for a reader."""
    lines = [f"## {packet['id']}", f"date {packet['date']}, channel {packet['channel']}, "
             f"session {packet['session'][:8]}", "", "The person's words:", "", "> " +
             packet["words"].replace("\n", "\n> "), ""]
    if packet["seen"]:
        lines += ["What they were looking at (its end):", "", "```", packet["seen"], "```", ""]
    for p in packet["pairs"]:
        lines += [f"Pair {p['id']} ({p['quality']}):", "", "before:", "```", p["before"], "```",
                  "after:", "```", p["after"], "```", ""]
    if packet["labels"]:
        lines += ["Fix labels that followed: " + "; ".join(packet["labels"]), ""]
    if packet["commits"]:
        lines += ["Commits that followed: " + "; ".join(packet["commits"]), ""]
    lines += ["Closest existing rules:", ""] + [f"- {rid}: {text}" for rid, text in packet["rules"]] + [""]
    return "\n".join(lines)


def write_packets(packets: list[dict], out: Path, readers: int = 6) -> list[Path]:
    """Split packets evenly into one markdown file per reader, and save the index."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "packets.json").write_text(json.dumps(packets, ensure_ascii=False))
    files = []
    for r in range(readers):
        share = packets[r::readers]
        path = out / f"P{r + 1:02d}.md"
        path.write_text("\n".join(render(p) for p in share))
        files.append(path)
    return files


def verify(packets: list[dict], points: list[dict]) -> tuple[list[dict], list[dict]]:
    """Points whose quote is the person's own and whose pair ids exist, and the rest
    with the reason each was refused."""
    by_id = {p["id"]: p for p in packets}
    ok, bad = [], []
    for point in points:
        packet = by_id.get(point.get("episode", ""))
        if packet is None:
            bad.append({**point, "why": f"episode {point.get('episode')} is not a packet"})
            continue
        quote = _norm(point.get("quote") or "")
        words = _norm(packet["words"])
        seen = _norm(packet["seen"]) + " " + " ".join(_norm(x) for x in packet["pasted"])
        pair_ids = {p["id"] for p in packet["pairs"]}
        if not quote or quote not in words:
            bad.append({**point, "why": "the quote is not in the person's words"})
        elif quote in seen:
            bad.append({**point, "why": "the quote is text the person saw or pasted, not text they wrote"})
        elif any(ref and ref not in pair_ids for ref in (point.get("before_ref"), point.get("after_ref"))):
            missing = [r for r in (point.get("before_ref"), point.get("after_ref")) if r and r not in pair_ids]
            bad.append({**point, "why": f"pair {', '.join(missing)} is not in the packet"})
        else:
            ok.append({**point, "date": packet["date"], "session": packet["session"]})
    return ok, bad


def agreement(first: list[dict], second: list[dict], ids: list[str]) -> dict:
    """How far two blind reads of the same packets agree: whether each packet
    holds a writing point at all, and, where both found one, how much their
    entities and keys overlap (shared over the union, averaged per packet)."""
    def by_episode(points):
        out: dict[str, list[dict]] = {}
        for p in points:
            out.setdefault(p["episode"], []).append(p)
        return out

    a, b = by_episode(first), by_episode(second)
    found_agree, both, entity, key = 0, 0, 0.0, 0.0
    for i in ids:
        if (i in a) == (i in b):
            found_agree += 1
        if i in a and i in b:
            both += 1
            ea, eb = {p["entity"] for p in a[i]}, {p["entity"] for p in b[i]}
            ka, kb = {p["key"] for p in a[i]}, {p["key"] for p in b[i]}
            entity += len(ea & eb) / len(ea | eb)
            key += len(ka & kb) / len(ka | kb)
    return {"packets": len(ids), "found_agree": found_agree, "both_found": both,
            "entity_overlap": round(entity / both, 3) if both else None,
            "key_overlap": round(key / both, 3) if both else None,
            "only_first": [i for i in ids if i in a and i not in b],
            "only_second": [i for i in ids if i in b and i not in a]}


def _voice_rules(voice: Path) -> list[tuple[str, str]]:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from writing_register import voice as v
    from writing_register import voice_tools as vt
    rules = []
    for rel in v.load(voice).order:
        rules += [(r.id, r.text) for r in vt._rules_in(voice, rel, 0, [])]
    return rules


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--candidates", required=True)
    b.add_argument("--voice", required=True, help="a voice directory, for the closest rules")
    b.add_argument("--out", required=True)
    b.add_argument("--readers", type=int, default=6)
    vv = sub.add_parser("verify")
    vv.add_argument("--packets", required=True, help="packets.json written by build")
    vv.add_argument("--points", required=True, nargs="+", help="the readers' JSONL files")
    vv.add_argument("--out", required=True)
    ag = sub.add_parser("agree", help="agreement between the first reads and a blind second read")
    ag.add_argument("--first", required=True, nargs="+", help="the first readers' JSONL files")
    ag.add_argument("--second", required=True, help="the second reader's JSONL file")
    ag.add_argument("--ids", required=True, help="JSON list of the packet ids read twice")
    args = ap.parse_args(argv)

    def _read(paths):
        return [json.loads(line) for path in paths for line in open(path, encoding="utf-8") if line.strip()]

    if args.command == "agree":
        ids = json.loads(Path(args.ids).read_text())
        print(json.dumps(agreement(_read(args.first), _read([args.second]), ids), indent=1))
        return 0

    if args.command == "build":
        rules = _voice_rules(Path(args.voice))
        candidates = (json.loads(line) for line in open(args.candidates, encoding="utf-8"))
        packets = build(candidates, rules)
        files = write_packets(packets, Path(args.out), args.readers)
        print(f"{len(packets)} packets in {len(files)} files under {args.out}", file=sys.stderr)
        return 0

    packets = json.loads(Path(args.packets).read_text())
    points = []
    for path in args.points:
        for line in open(path, encoding="utf-8"):
            if line.strip():
                points.append(json.loads(line))
    ok, bad = verify(packets, points)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "verified.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in ok))
    (out / "rejected.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in bad))
    print(f"{len(ok)} points verified, {len(bad)} refused", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

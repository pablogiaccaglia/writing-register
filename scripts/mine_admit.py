#!/usr/bin/env python3
"""Which mined rules enter the voice, computed from the verified points.

Readers return points, each tied to a key: an existing rule id, a seed key, or
a new slug. Synthesis then writes candidate rules, each naming the keys it
gathers. This script decides each candidate from the raw points alone:

    admit    the person's words back it in two or more separate sessions,
             nothing they said conflicts with it, and its text passes the lint
    ledger   seen in one session only, or contradicted: the person decides
    refuse   the text breaks the lint (a dash, a name, a date, an identifier,
             a number from the work), whatever the evidence

A reader's key is one reading of a point. Two assigners can each read every
point again against the final list of rules (`--assignments`); a point then
counts for a rule when two of the three readings agree, which corrects keys
readers filed under different names for the same idea.

An admitted rule carries flags when its sessions are weakly independent: all
in one project, or all on one day. Points about existing rules are reported
apart, with breaches dated after the voice existed listed separately, since
those call for an example or a check rather than more text. Points no
candidate and no existing rule claims are listed, so nothing read is lost.

Standard library only; no model is called here.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

MIN_SESSIONS = 2
_WRITING = {"yes", "partly"}
_DASH = re.compile(r"[\u2013\u2014]|(?<!-)--(?!-)")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_CODE = re.compile(r"`[^`]*`|\b[a-z]+_[a-z0-9_]+\b|\b[a-z]+[A-Z][A-Za-z0-9]*\b")
_NUMBER = re.compile(r"\d")


def _writing(points):
    return [p for p in points if p.get("is_writing") in _WRITING]


def _pid(point) -> str:
    return f"{point['episode']}#{point['point']}"


def with_votes(points, first, second) -> list[dict]:
    """Points carrying the rule ids two independent assigners said each supports.
    With votes, a point counts for a rule when two of three readings agree: the
    reader's key (as synthesis merged it) and each assigner."""
    a = {x["id"]: set(x.get("supports") or []) for x in first}
    b = {x["id"]: set(x.get("supports") or []) for x in second}
    out = []
    for p in points:
        votes: dict[str, int] = {}
        for ids in (a.get(_pid(p), set()), b.get(_pid(p), set())):
            for rid in ids:
                votes[rid] = votes.get(rid, 0) + 1
        out.append({**p, "votes": votes})
    return out


def _counts(point, rule_id: str, keys) -> bool:
    by_key = point["key"] in keys
    if "votes" not in point:
        return by_key
    return point["votes"].get(rule_id, 0) + by_key >= 2


def assigner_agreement(first, second) -> dict:
    """How often two assigners gave a point the same rules, and their mean overlap."""
    b = {x["id"]: set(x.get("supports") or []) for x in second}
    same, overlap = 0, 0.0
    for x in first:
        sa, sb = set(x.get("supports") or []), b.get(x["id"], set())
        same += sa == sb
        overlap += len(sa & sb) / len(sa | sb) if sa | sb else 1.0
    return {"points": len(first), "identical": same, "overlap": round(overlap / len(first), 3) if first else None}


def lint(text: str, names: set[str]) -> list[str]:
    """What stops a rule line from being public: each problem as a short phrase."""
    problems = []
    if _DASH.search(text):
        problems.append("an em or en dash")
    words = set(re.findall(r"[A-Za-z][A-Za-z0-9-]*", text))
    lowered = {w.lower() for w in words}
    flat = " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "
    hit = sorted(n for n in names if (n.lower() in lowered if " " not in n.strip()
                                      else " " + " ".join(re.findall(r"[a-z0-9]+", n.lower())) + " " in flat))
    if hit:
        problems.append("a private name (" + ", ".join(hit) + ")")
    if _CODE.search(text):
        problems.append("a code identifier")
    if _DATE.search(text):
        problems.append("a date")
    if _NUMBER.search(_DATE.sub("", _CODE.sub("", text))):
        problems.append("a number from the work")
    return problems


def decide(candidates, points, projects: dict[str, str], names: set[str] = frozenset()) -> list[dict]:
    """One decision per candidate, in the candidates' order."""
    writing = _writing(points)
    out = []
    for c in candidates:
        keys = set(c["keys"])
        mine = [p for p in writing if _counts(p, c["id"], keys)]
        sessions = sorted({p["session"] for p in mine})
        conflicts = [p for p in mine if p.get("relation") == "conflict"]
        problems = lint(c["text"], names)
        flags = []
        if len(sessions) >= MIN_SESSIONS:
            if len({projects.get(s, s) for s in sessions}) == 1:
                flags.append("one project")
            if len({p["date"] for p in mine}) == 1:
                flags.append("one day")
        if problems:
            verdict, why = "refuse", "the text has " + "; ".join(problems)
        elif conflicts:
            verdict, why = "ledger", f"{len(conflicts)} conflict with what the person asked elsewhere"
        elif len(sessions) < MIN_SESSIONS:
            verdict = "ledger"
            why = "seen in one session" if sessions else "no verified point"
        else:
            verdict, why = "admit", f"{len(sessions)} sessions"
        out.append({**c, "verdict": verdict, "why": why, "sessions": sessions, "flags": flags,
                    "episodes": sorted({p["episode"] for p in mine}), "points": len(mine)})
    return out


def existing(points, rule_ids: set[str], since: str) -> dict[str, dict]:
    """Fresh evidence for rules the voice already has, per rule."""
    report: dict[str, dict] = {}
    for p in _writing(points):
        for rid in sorted(rule_ids):
            # A point that asks for the opposite of a rule is never support for
            # it, so a conflict is reported on the reader's key alone.
            conflict = p["key"] == rid and p.get("relation") == "conflict"
            if not (conflict or _counts(p, rid, {rid})):
                continue
            r = report.setdefault(rid, {"sessions": set(), "points": [], "breached_after": [], "extensions": [],
                                         "conflicts": []})
            if conflict:
                r["conflicts"].append(p)
                continue
            r["sessions"].add(p["session"])
            r["points"].append(p)
            # The reader's relation describes the point against the reader's own
            # key, so it says nothing about a rule only the assigners chose.
            if p["key"] != rid:
                continue
            if p.get("relation") == "violation" and p.get("date", "") >= since:
                r["breached_after"].append(p)
            elif p.get("relation") == "extension":
                r["extensions"].append(p)
    for r in report.values():
        r["sessions"] = sorted(r["sessions"])
    return report


def unclaimed(points, candidates, existing_ids: set[str]) -> list[dict]:
    """Writing points whose key no candidate gathers and no existing rule has."""
    def claimed(p):
        return (any(_counts(p, c["id"], set(c["keys"])) for c in candidates)
                or any(_counts(p, rid, {rid}) for rid in existing_ids))
    return [p for p in _writing(points) if not claimed(p)]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


ACCEPT_POINTS = 3


def recommend(decision) -> str:
    """What to suggest for a ledger item: reject a text that failed the lint,
    let the person decide a conflict, accept a rule one session backs with
    several of their own points, and hold the rest as evidence only."""
    if decision["verdict"] == "refuse":
        return "reject"
    if "conflict" in decision["why"]:
        return "decide"
    return "accept" if decision["points"] >= ACCEPT_POINTS else "hold"


def render_ledger(decisions, points) -> str:
    """The items for the person to decide: a summary table, then each item's quotes."""
    items = [d for d in decisions if d["verdict"] != "admit"]
    by_episode = defaultdict(list)
    for p in _writing(points):
        by_episode[p["episode"]].append(p)
    lines = ["# Ledger of mined rules", "",
             "| Item | Rule | Entity | Recommendation | Why | Sessions |", "|---|---|---|---|---|---|"]
    for n, d in enumerate(items, 1):
        rec = recommend(d)
        lines.append(f"| L{n} | {_cell(d['text'])} | {d['entity']} | {rec} | {_cell(d['why'])} | "
                     f"{len(d['sessions'])} |")
    for n, d in enumerate(items, 1):
        lines += ["", f"## L{n}. {d['id']}", "", d["text"], "", f"Why it is here: {d['why']}.", ""]
        keys = set(d["keys"])
        for ep in d["episodes"]:
            for p in by_episode.get(ep, []):
                if _counts(p, d["id"], keys):
                    lines.append(f"- {p['date']}, session {p['session'][:8]}, {p['relation']}: "
                                 f"\"{p['quote']}\"")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--points", required=True, help="verified.jsonl from mine_packets.py verify")
    ap.add_argument("--candidates", required=True, help="candidate rules written by synthesis")
    ap.add_argument("--projects", required=True, help="JSON map of session to project")
    ap.add_argument("--names", help="private names, one per line, that no rule may contain")
    ap.add_argument("--rules", nargs="*", default=[], help="ids of the rules the voice already has")
    ap.add_argument("--rules-from", help="a file listing existing rules as '- id: text' lines")
    ap.add_argument("--assignments", nargs=2, metavar=("A", "B"),
                    help="two assigners' JSONL files: {id, supports} per point")
    ap.add_argument("--since", default="2026-09-14", help="the date the voice began reaching sessions")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    points = [json.loads(line) for line in open(args.points, encoding="utf-8") if line.strip()]
    candidates = json.loads(Path(args.candidates).read_text())
    projects = json.loads(Path(args.projects).read_text())
    names = set()
    if args.names:
        names = {line.strip() for line in open(args.names, encoding="utf-8")
                 if line.strip() and not line.startswith("#")}
    rules = set(args.rules)
    if args.rules_from:
        rules |= set(re.findall(r"^- ([a-z0-9][a-z0-9./-]*):", Path(args.rules_from).read_text(), re.M))
    agreement = None
    if args.assignments:
        first, second = ([json.loads(line) for line in open(f, encoding="utf-8") if line.strip()]
                         for f in args.assignments)
        agreement = assigner_agreement(first, second)
        points = with_votes(points, first, second)
    decisions = decide(candidates, points, projects, names)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "decisions.json").write_text(json.dumps(decisions, ensure_ascii=False, indent=1))
    (out / "ledger.md").write_text(render_ledger(decisions, points))
    report = existing(points, rules, args.since)
    (out / "existing.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    left = unclaimed(points, candidates, rules)
    (out / "unclaimed.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in left))
    if agreement:
        (out / "assigners.json").write_text(json.dumps(agreement))
        print(f"assigners: {agreement['identical']} of {agreement['points']} points identical, "
              f"mean overlap {agreement['overlap']}")
    counts = defaultdict(int)
    for d in decisions:
        counts[d["verdict"]] += 1
    print(f"{counts['admit']} admitted, {counts['ledger']} to the ledger, {counts['refuse']} refused; "
          f"{len(report)} existing rules with fresh evidence; {len(left)} points unclaimed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

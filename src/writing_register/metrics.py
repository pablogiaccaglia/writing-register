"""What the automatic rewrites had to change, kept after the turn ends.

2026-09-16. Steering Claude's writing is only worth what it measurably
changes, and nothing was measurable: the text before an edit, the sentences
recorded for the rewrite and the note handed to Claude are each deleted within
a turn or two, so the share of Claude's own prose the rewriter still has to
change could not be computed from anything on disk. This module keeps one JSON
line per rewrite, in the same place the hooks already keep their state.

It never raises. A meter that breaks a rewrite is worse than no meter.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

DIR_ENV = "WR_STATE_DIR"


def _dir() -> Path:
    base = os.environ.get(DIR_ENV) or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache"),
        "writing-register", "auto")
    return Path(base) / "metrics"


def append(record: dict) -> None:
    """Add one record, stamped with the time, to this month's file."""
    try:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        row = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), **record}
        with open(d / f"{dt.date.today():%Y-%m}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        return


def records(since: str = "") -> list[dict]:
    """Every record kept, oldest first, optionally from a date onwards."""
    out: list[dict] = []
    try:
        files = sorted(_dir().glob("*.jsonl"))
    except OSError:
        return out
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and (not since or str(row.get("at", "")) >= since):
                out.append(row)
    return sorted(out, key=lambda r: str(r.get("at", "")))


def _share(rows, key="words_changed", of="words_sent") -> float:
    sent = sum(int(r.get(of) or 0) for r in rows)
    changed = sum(int(r.get(key) or 0) for r in rows)
    return 100 * changed / sent if sent else 0.0


def report(since: str = "") -> str:
    """What the rewrites did, in a few lines: the number that should fall is the
    share of Claude's own words the rewriter still changes."""
    rows = records(since)
    if not rows:
        return "wr has recorded no rewrites yet."
    passages = [r for r in rows if r.get("event") == "passages"]
    messages = [r for r in rows if r.get("event") == "message"]
    refused = [r for r in rows if r.get("refused")]
    lines = [f"{len(rows)} rewrites recorded, the first on {str(rows[0].get('at', ''))[:10]}."]
    if passages:
        lines.append(f"{len(passages)} passage rewrites of what Claude wrote: "
                     f"{_share(passages):.0f}% of the words it sent were changed, "
                     f"{sum(int(r.get('passages') or 0) for r in passages)} passages in all.")
    if messages:
        lines.append(f"{len(messages)} commit messages and pull request descriptions: "
                     f"{_share(messages):.0f}% of the words changed.")
    if refused:
        lines.append(f"{len(refused)} were refused and kept beside their file.")
    return "\n".join(lines)

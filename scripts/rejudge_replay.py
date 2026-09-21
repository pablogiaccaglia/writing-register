#!/usr/bin/env python3
"""Apply the checker's rules again to saved replay records, without a model call.

2026-09-15. Each replay record from checker v4 on keeps the checker's
raw reply per claim. This script rebuilds the claims from the corpus the
records came from, exports the repository at the same commit, runs
`verify.rejudge`, and prints the same summary as replay_check.py, so a change
to a rule is measured on every saved run in seconds.

    .venv/bin/python scripts/rejudge_replay.py --corpus tests/fixtures/sample RECORD.json ...

With no record named, every record in --out is read (by default
$XDG_STATE_HOME/writing-register/replay).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(HERE / "scripts"))

from replay_corpus import Checkouts, CorpusError, default_out, load  # noqa: E402
from writing_register.changes import changes_between, claims  # noqa: E402
from writing_register.verify import rejudge  # noqa: E402


def _flat(text):
    return " ".join(text.split())


def raw_by_claim(record: dict, cl: list) -> tuple[dict, list]:
    """The saved raw verdicts keyed by the current claim numbers, matched by the
    claim's text, and the claims that have one.

    Claim numbers depend on how changes.py pairs sentences, so a change there
    renumbers claims; matching by text keeps old replies usable."""
    by_text = {}
    for v in record["verdicts"]:
        raw = record["raw"].get(str(v["number"]))
        if raw is not None:
            by_text[_flat(v["new"])] = raw
    matched, raw = [], {}
    for c in cl:
        item = by_text.get(_flat(c.new))
        if item is not None:
            raw[c.number] = {**item, "id": c.number}
            matched.append(c)
    return raw, matched


def arguments(description: str, argv=None):
    """The options rejudge_replay.py and agreement.py share: the corpus, and the
    records to read."""
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--corpus", required=True, help="a directory holding corpus.toml")
    ap.add_argument("records", nargs="*", help="replay records; every *.json in --out when none")
    ap.add_argument("--out", help="where the records are (default $XDG_STATE_HOME/writing-register/replay)")
    args = ap.parse_args(argv)
    try:
        corpus = load(args.corpus)
    except CorpusError as e:
        ap.error(str(e))
    paths = args.records or sorted(str(p) for p in (Path(args.out) if args.out else default_out()).glob("*.json"))
    return corpus, paths


def main(argv=None):
    corpus, paths = arguments("Apply the checker's rules again to saved replay records.", argv)
    total_other, total_reverted, set_a, set_c = 0, 0, [], []
    with Checkouts(corpus) as checkouts:
        for path in paths:
            record = json.loads(Path(path).read_text())
            if "raw" not in record:
                print(f"{path}: no raw reply saved (checker before v4), skipped")
                continue
            try:
                doc = corpus.find(record["corpus"], record["doc"])
            except CorpusError as e:
                print(f"{path}: {e}, skipped")
                continue
            old, new = doc.texts()
            cl = claims(changes_between(old, new))
            export = checkouts.export(doc, new)
            try:
                raw, cl = raw_by_claim(record, cl)
                result = rejudge(raw, cl, export, original=old)
            finally:
                shutil.rmtree(export, ignore_errors=True)
            wrong, true = corpus.labels_for(doc)
            by_new = {_flat(c.new): c for c in cl}
            labelled = set()
            lines = []
            for a in wrong:
                c = next((c for k, c in by_new.items() if _flat(a["rewrite_sentence"])[:60] in k), None)
                if c is None or c.number in labelled:
                    continue
                labelled.add(c.number)
                v = result.by_number.get(c.number)
                ok = v is not None and v.effective in ("FALSE", "UNVERIFIABLE")
                set_a.append(ok)
                lines.append(f"  set A {'PASS' if ok else 'MISS'} {v.effective if v else '-'}: {c.new[:80]}")
            for s in true:
                c = next((c for k, c in by_new.items() if _flat(s["sentence"])[:50] in k), None)
                if c is None or c.number in labelled:
                    continue
                labelled.add(c.number)
                v = result.by_number.get(c.number)
                ok = v is not None and v.effective == "TRUE"
                set_c.append(ok)
                if not ok:
                    lines.append(f"  set C MISS {v.effective if v else '-'}: {c.new[:80]}\n"
                                 f"        {v.reason[:160] if v else ''}")
            others = [v for v in result.verdicts if v.number not in labelled]
            reverted = [v for v in others if v.effective in ("FALSE", "UNVERIFIABLE")]
            total_other += len(others)
            total_reverted += len(reverted)
            s = result.stats
            print(f"{doc.path} run {record['run']}: TRUE {s.true}, FALSE {s.false}, SAME {s.same}, "
                  f"ORIGINAL {s.original}, NOT_A_FACT {s.not_a_fact}, UNVERIFIABLE {s.unverifiable}, "
                  f"omitted {s.omitted}; other reverted {len(reverted)} of {len(others)}"
                  + (f"; DID NOT CHECK: {result.why}" if result.did_not_check else ""))
            if lines:
                print("\n".join(lines))
    print(f"\nTOTAL set A caught {sum(set_a)} of {len(set_a)}; set C confirmed {sum(set_c)} of {len(set_c)}; "
          f"other reverted {total_reverted} of {total_other}"
          f" ({100 * total_reverted / max(1, total_other):.1f}%)")


if __name__ == "__main__":
    main()

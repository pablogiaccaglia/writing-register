#!/usr/bin/env python3
"""How stable the checker's verdicts are across runs (2026-09-15).

The checker is a model call, so the same document checked twice can come back
different. This groups saved replay records by document, applies the current
rules to each (no model call), and reports per document: whether each wrong
sentence was caught in every run, how many other sentences were reverted in
each run, and how many were reverted in all runs, in most, or in only one.
A sentence reverted in only one run of several is noise; one reverted in every
run is either a real error or a systematic false revert worth reading.

    .venv/bin/python scripts/agreement.py --corpus tests/fixtures/sample RECORD.json ...

With no record named, every record in --out is read (by default
$XDG_STATE_HOME/writing-register/replay).
"""
from __future__ import annotations

import collections
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(HERE / "scripts"))

from rejudge_replay import arguments, raw_by_claim  # noqa: E402
from replay_corpus import Checkouts, CorpusError  # noqa: E402
from writing_register.changes import changes_between, claims  # noqa: E402
from writing_register.verify import rejudge  # noqa: E402

REVERT = ("FALSE", "UNVERIFIABLE")


def _flat(text):
    return " ".join(text.split())


def main(argv=None):
    corpus, paths = arguments("How stable the checker's verdicts are across saved runs.", argv)
    groups = collections.defaultdict(list)
    for path in paths:
        record = json.loads(Path(path).read_text())
        if "raw" in record:
            groups[(record["corpus"], record["doc"])].append(record)
    totals = collections.Counter()
    with Checkouts(corpus) as checkouts:
        for (repo, rel), records in sorted(groups.items()):
            try:
                doc = corpus.find(repo, rel)
            except CorpusError as e:
                print(f"{repo} {rel}: {e}, skipped")
                continue
            old, new = doc.texts()
            cl = claims(changes_between(old, new))
            by_number = {c.number: c for c in cl}
            set_a, set_c = corpus.labels_for(doc)
            wrong = {c.number for c in cl for a in set_a if _flat(a["rewrite_sentence"])[:60] in _flat(c.new)}
            true = {c.number for c in cl for s in set_c if _flat(s["sentence"])[:50] in _flat(c.new)}
            export = checkouts.export(doc, new)
            verdicts = []
            try:
                for record in records:
                    raw, matched = raw_by_claim(record, cl)
                    result = rejudge(raw, matched, export, original=old)
                    verdicts.append({n: v.effective for n, v in result.by_number.items()})
                    if len(matched) < len(cl):
                        print(f"  (run with {len(cl) - len(matched)} claims not in the saved reply)")
            finally:
                shutil.rmtree(export, ignore_errors=True)
            runs = len(verdicts)
            others = [n for n in by_number if n not in wrong and n not in true]
            per_run = [sum(1 for n in others if v.get(n) in REVERT) for v in verdicts]
            times = collections.Counter(sum(1 for v in verdicts if v.get(n) in REVERT) for n in others)
            print(f"\n{repo} {rel}: {runs} run{'s' if runs != 1 else ''}, {len(cl)} claims")
            for n in sorted(wrong):
                caught = sum(1 for v in verdicts if v.get(n) in REVERT)
                print(f"  wrong  caught in {caught} of {runs}: {_flat(by_number[n].new)[:90]}")
                totals["wrong_runs"] += runs
                totals["wrong_caught"] += caught
            for n in sorted(true):
                kept = sum(1 for v in verdicts if v.get(n) == "TRUE")
                not_reverted = sum(1 for v in verdicts if v.get(n) not in REVERT)
                print(f"  true   confirmed in {kept} of {runs}, kept in {not_reverted}: {_flat(by_number[n].new)[:90]}")
                totals["true_runs"] += runs
                totals["true_kept"] += kept
                totals["true_not_reverted"] += not_reverted
            print(f"  other sentences reverted per run: {per_run} of {len(others)}")
            print(f"  reverted in all {runs}: {times[runs] if runs > 1 else times[1]}; "
                  f"in some but not all: {sum(c for k, c in times.items() if 0 < k < runs)}; never: {times[0]}")
            for n in others:
                k = sum(1 for v in verdicts if v.get(n) in REVERT)
                if runs > 1 and k == runs:
                    print(f"    every run: #{n} {_flat(by_number[n].new)[:100]}")
            totals["other_run_slots"] += len(others) * runs
            totals["other_reverted_slots"] += sum(per_run)
            totals["other_claims"] += len(others)
            totals["other_always"] += times[runs] if runs > 1 else 0
            totals["other_multi_run_claims"] += len(others) if runs > 1 else 0
    print(f"\nTOTAL wrong sentences caught {totals['wrong_caught']} of {totals['wrong_runs']} run-slots; "
          f"true sentences confirmed {totals['true_kept']} and kept {totals['true_not_reverted']} of {totals['true_runs']}; "
          f"other sentences reverted {totals['other_reverted_slots']} of {totals['other_run_slots']} run-slots "
          f"({100 * totals['other_reverted_slots'] / max(1, totals['other_run_slots']):.1f}%); "
          f"reverted in every run {totals['other_always']} of {totals['other_multi_run_claims']} claims with several runs")


if __name__ == "__main__":
    main()

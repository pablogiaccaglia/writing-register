#!/usr/bin/env python3
"""Run the checker on a replay corpus and measure it (2026-09-15).

Makes real model calls. For each document: the change list between the text
before and after the rewrite, an export of the repository at the commit the
rewrite was made for with the rewritten document laid over it, one checker
call, and a JSON record of every verdict. The summary shows what happened to
the labelled sentences: set A (known wrong) must come back FALSE or
UNVERIFIABLE, set C (verified true) must not, and every other changed sentence
reverted counts against precision.

The corpus is a directory with a corpus.toml (see replay_corpus.py). Name the
documents to check, or none for all of them:

    .venv/bin/python scripts/replay_check.py --corpus tests/fixtures/sample
    .venv/bin/python scripts/replay_check.py --corpus tests/fixtures/sample OPERATIONS --runs 3

Records go to --out, by default $XDG_STATE_HOME/writing-register/replay.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(HERE / "scripts"))

from replay_corpus import Checkouts, CorpusError, default_out, load  # noqa: E402
from writing_register.changes import changes_between, claims  # noqa: E402
from writing_register.verify import verify_claims  # noqa: E402


def _flat(text):
    return " ".join(text.split())


def check_document(corpus, checkouts, doc, args, out: Path):
    old, new = doc.texts()
    set_a, set_c = corpus.labels_for(doc)
    want_false = [_flat(a["rewrite_sentence"]) for a in set_a]
    want_true = [_flat(c["sentence"]) for c in set_c]
    cl = claims(changes_between(old, new))
    repo, commit = checkouts.repository(doc)
    print(f"{doc.repo} {doc.path} at {commit}: {len(cl)} claims "
          f"({sum(c.kind == 'added' for c in cl)} added, {sum(c.kind == 'changed' for c in cl)} changed); "
          f"{len(want_false)} labelled wrong, {len(want_true)} labelled true", flush=True)

    for run in range(1, args.runs + 1):
        export = checkouts.export(doc, new)
        try:
            result = verify_claims(new, cl, export, model=args.model, timeout=args.timeout, original=old,
                                   effort=args.effort)
        finally:
            shutil.rmtree(export, ignore_errors=True)

        def verdict_for(sentence):
            for c in cl:
                if sentence[:60] in _flat(c.new):
                    return c, result.by_number.get(c.number)
            return None, None

        s = result.stats
        print(f"run {run}: {result.seconds:.0f}s, {s.turns} turns, ${s.cost:.2f}; sent {s.sent}: "
              f"TRUE {s.true}, FALSE {s.false}, SAME {s.same}, ORIGINAL {s.original}, "
              f"NOT_A_FACT {s.not_a_fact}, UNVERIFIABLE {s.unverifiable}, "
              f"omitted {s.omitted}" + (f"; DID NOT CHECK: {result.why}" if result.did_not_check else ""),
              flush=True)
        # A claim is a whole sentence or list item; two labelled sentences can
        # fall in one claim, which is then reported and counted once.
        labelled = set()
        for sentence in want_false:
            c, v = verdict_for(sentence)
            if c and c.number in labelled:
                continue
            if c:
                labelled.add(c.number)
            ok = v is not None and v.effective in ("FALSE", "UNVERIFIABLE")
            print(f"  set A {'PASS' if ok else 'MISS'} {v.effective if v else 'not a claim'}: {sentence[:90]}"
                  + (f"\n        {v.cited or v.reason[:200]}" if v else ""))
        for sentence in want_true:
            c, v = verdict_for(sentence)
            if c and c.number in labelled:
                continue
            if c:
                labelled.add(c.number)
            ok = v is not None and v.effective == "TRUE"
            print(f"  set C {'PASS' if ok else 'MISS'} {v.effective if v else 'not a claim'}: {sentence[:90]}"
                  + (f"\n        {v.cited or v.reason[:200]}" if v and not ok else ""))
        others = [v for v in result.verdicts if v.number not in labelled]
        reverted = [v for v in others if v.effective in ("FALSE", "UNVERIFIABLE")]
        print(f"  other changed sentences: {len(others)}, reverted {len(reverted)} "
              f"({100 * len(reverted) / max(1, len(others)):.0f}%)")
        by_number = {c.number: c for c in cl}
        for v in reverted[:40]:
            print(f"    {v.effective:12} #{v.number} {_flat(by_number[v.number].new)[:100]}\n"
                  f"                 {v.cited or v.reason[:160]}")
        record = {"corpus": doc.repo, "corpus_name": corpus.name, "document": doc.name, "doc": doc.path,
                  "commit": commit, "run": run, "seconds": result.seconds,
                  "did_not_check": result.did_not_check, "why": result.why,
                  "stats": s.__dict__, "raw": {str(k): v for k, v in result.raw.items()},
                  "probe_replies": result.probe_replies,
                  "verdicts": [{**v.__dict__, "kind": by_number[v.number].kind,
                                "new": by_number[v.number].new, "old": by_number[v.number].old}
                               for v in result.verdicts]}
        name = f"{corpus.name}-{doc.repo}-{doc.path.replace('/', '_')}-{int(time.time())}-run{run}.json"
        (out / name).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        print(f"  saved {out / name}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the checker on a replay corpus (makes model calls).")
    ap.add_argument("--corpus", required=True, help="a directory holding corpus.toml")
    ap.add_argument("docs", nargs="*", help="documents to check, by name or path; all when none")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--out", help="where to save the records (default $XDG_STATE_HOME/writing-register/replay)")
    args = ap.parse_args(argv)
    try:
        corpus = load(args.corpus)
        docs = corpus.select(args.docs)
    except CorpusError as e:
        ap.error(str(e))
    out = Path(args.out) if args.out else default_out()
    out.mkdir(parents=True, exist_ok=True)
    with Checkouts(corpus) as checkouts:
        for doc in docs:
            check_document(corpus, checkouts, doc, args, out)


if __name__ == "__main__":
    main()

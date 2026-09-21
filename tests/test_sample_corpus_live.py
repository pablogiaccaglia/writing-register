"""The real checker on the invented corpus. Costs one model call per document.

    WR_LIVE=1 .venv/bin/python -m pytest tests/test_sample_corpus_live.py

Each document of tests/fixtures/sample is checked the way replay_check.py
checks it: its claims, an export of the repository with the rewrite laid over
it, one checker call. A wrong sentence (set A) must come back FALSE or
UNVERIFIABLE. A true one (set C) that comes back otherwise is printed, not
failed: the checker is a model, and one run is not a measure of it."""
import os
import shutil
import sys
from pathlib import Path

import pytest

from writing_register.changes import changes_between, claims
from writing_register.verify import verify_claims

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "scripts"))

import replay_corpus  # noqa: E402

live = pytest.mark.skipif(os.environ.get("WR_LIVE") != "1", reason="real model calls; set WR_LIVE=1")
CORPUS = replay_corpus.load(HERE / "tests" / "fixtures" / "sample")


def _flat(text):
    return " ".join(text.split())


@live
@pytest.mark.parametrize("name", [d.name for d in CORPUS.documents])
def test_the_checker_catches_the_planted_sentences(name):
    doc = next(d for d in CORPUS.documents if d.name == name)
    old, new = doc.texts()
    cl = claims(changes_between(old, new))
    set_a, set_c = CORPUS.labels_for(doc)
    with replay_corpus.Checkouts(CORPUS) as checkouts:
        export = checkouts.export(doc, new)
        try:
            result = verify_claims(new, cl, export, original=old, effort="high")
        finally:
            shutil.rmtree(export, ignore_errors=True)
    assert not result.did_not_check, result.why

    def verdict(sentence):
        c = next(c for c in cl if _flat(sentence) in _flat(c.new))
        return result.by_number[c.number]

    missed = [a["rewrite_sentence"] for a in set_a if verdict(a["rewrite_sentence"]).effective not in ("FALSE", "UNVERIFIABLE")]
    for c in set_c:
        v = verdict(c["sentence"])
        if v.effective != "TRUE":
            print(f"set C {v.effective}: {c['sentence']}\n    {v.cited or v.reason[:200]}")
    assert not missed, missed

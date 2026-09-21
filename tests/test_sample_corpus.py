"""The invented corpus in tests/fixtures/sample, and a scripted run of it.

stationlog is a weather-station pipeline that exists only in this directory:
a few Python modules, a push script and three documents rewritten with
background taken from the code. labels.json plants one wrong sentence per
class of error the checker must catch (set A) and lists the true sentences the
rewrites added or changed (set C), each with the line of code that decides it.
A fourth document is rewritten with more wrong sentences than it may put back.

The end-to-end tests make no model call: a fake answers the rewrite with the
`.new.md` side and the checker from the labels, so what is tested is everything
wr does around the two calls."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from writing_register.changes import changes_between, claims
from writing_register.humanize import humanize
from writing_register.spawn import Answer
from writing_register.verify import _span_at

HERE = Path(__file__).resolve().parent.parent
SAMPLE = HERE / "tests" / "fixtures" / "sample"
sys.path.insert(0, str(HERE / "scripts"))

import replay_corpus  # noqa: E402

CORPUS = replay_corpus.load(SAMPLE)
LABELS = CORPUS.labels
MARKER = "IGNORED-READINGS-MARKER"


def _flat(text):
    return " ".join(text.split())


def _doc(name):
    return next(d for d in CORPUS.documents if d.name == name)


# Structure


def test_the_corpus_names_four_documents_with_both_sides():
    assert [d.name for d in CORPUS.documents] == ["README", "ARCHITECTURE", "OPERATIONS", "ALERTS"]
    for d in CORPUS.documents:
        old, new = d.texts()
        assert old != new
        assert (SAMPLE / "repo" / d.path).read_text() == old, "the repository holds the original side"


def test_set_a_plants_one_sentence_per_class_of_error():
    classes = [a["class"] for a in LABELS["set_a"]]
    assert 6 <= len(classes) <= 8 and len(set(classes)) == len(classes)
    assert set(classes) >= {"wrong-component", "unimplemented-lifecycle", "guard-dropped",
                            "number-from-elsewhere", "through-a-helper", "flipped-negation"}
    assert 15 <= len(LABELS["set_c"]) <= 25


def _all_labels():
    over = LABELS["over_cap"]
    return ([(a["file"], a["rewrite_sentence"], a["contradicted_by"]) for a in LABELS["set_a"] + over["set_a"]]
            + [(c["file"], c["sentence"], c["confirmed_by"]) for c in LABELS["set_c"] + over["set_c"]])


def test_every_label_points_at_a_sentence_of_the_rewrite():
    for file, sentence, _ in _all_labels():
        doc = CORPUS.find("sample", file)
        assert _flat(sentence) in _flat(doc.texts()[1]), sentence


def test_every_citation_sits_within_three_lines_of_its_line():
    for _, sentence, cite in _all_labels():
        assert _span_at(SAMPLE / "repo" / cite["path"], cite["line"], cite["span"]), (sentence, cite)


def test_every_added_or_changed_sentence_is_labelled():
    """The fake checker answers from the labels, so a sentence the labels miss
    would have no answer."""
    for doc in CORPUS.documents:
        set_a, set_c = CORPUS.labels_for(doc)
        known = [_flat(a["rewrite_sentence"]) for a in set_a] + [_flat(c["sentence"]) for c in set_c]
        for claim in claims(changes_between(*doc.texts())):
            assert any(k in _flat(claim.new) for k in known), (doc.name, claim.new)


# End to end, with no model call


def _verdict(number, verdict, cite, delta="", negative=()):
    return {"id": number, "verdict": verdict, "claim_type": "mechanism", "effect": cite, "condition": None,
            "negative": list(negative), "reason": "judged", "reason_code": None, "delta": delta,
            "original": [], "actor": None}


class Model:
    """The rewrite call returns the `.new.md` side; checker calls answer from the labels."""

    def __init__(self, doc, shift=""):
        self.rewrite = doc.texts()[1]
        self.set_a, self.set_c = CORPUS.labels_for(doc)
        self.shift, self.prompts, self.exports = shift, [], []

    def run(self, prompt, **kw):
        self.prompts.append(prompt)
        if kw.get("read_root") is None:
            return Answer(stdout=self.rewrite, command=("claude",), duration_seconds=0.1)
        root = Path(kw["read_root"])
        self.exports.append({"has_out": (root / "out").exists(),
                             "files": sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())})
        verdicts = [self._judge(root, json.loads(line)) for line in prompt.splitlines() if line.startswith('{"id"')]
        out = {"structured_output": {"verdicts": verdicts}, "num_turns": 12, "total_cost_usd": 0.0}
        return Answer(stdout=json.dumps(out), command=("claude",), duration_seconds=0.1)

    def _judge(self, root, item):
        probe = re.match(r"`([^`]+)` defines `([^`]+)`", item["new"])
        if probe:
            lines = (root / probe.group(1)).read_text().splitlines()
            number = next(i for i, line in enumerate(lines, 1) if probe.group(2) in line)
            return _verdict(item["id"], "TRUE", {"path": probe.group(1), "line": number, "span": lines[number - 1].strip()})
        new = _flat(item["new"])
        for a in self.set_a:
            if _flat(a["rewrite_sentence"]) in new:
                return _verdict(item["id"], "FALSE", a["contradicted_by"], delta=item["new"],
                                negative=a.get("negative", ()))
        for c in self.set_c:
            if _flat(c["sentence"]) in new:
                cite = dict(c["confirmed_by"])
                if self.shift and self.shift in c["sentence"]:
                    cite["line"] += 5
                return _verdict(item["id"], "TRUE", cite)
        raise AssertionError(f"no label for {new}")


def _repository(tmp_path, doc):
    root = tmp_path / "stationlog"
    shutil.copytree(SAMPLE / "repo", root)
    (root / doc.path).write_text(doc.texts()[0])
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start"], cwd=root, check=True)
    return root


def _verdict_for(result, sentence):
    for change, verdict in result.reverted:
        if _flat(sentence) in _flat(change.new):
            return verdict
    return None


@pytest.mark.parametrize("name", ["README", "ARCHITECTURE", "OPERATIONS"])
def test_every_wrong_sentence_goes_back_and_every_true_one_stays(tmp_path, name):
    doc = _doc(name)
    root = _repository(tmp_path, doc)
    model = Model(doc)
    r = humanize(root / doc.path, spawn=model, root=root)
    assert r.written and r.checked and not r.refused, r.refused
    text = _flat((root / doc.path).read_text())
    set_a, set_c = CORPUS.labels_for(doc)
    assert set_a, "each labelled document plants at least one wrong sentence"
    for a in set_a:
        assert _flat(a["rewrite_sentence"]) not in text, a["rewrite_sentence"]
        verdict = _verdict_for(r, a["rewrite_sentence"])
        assert verdict is not None and verdict.effective == "FALSE", a["rewrite_sentence"]
        assert verdict.cited.startswith(f"{a['contradicted_by']['path']}:{a['contradicted_by']['line']} ")
    for c in set_c:
        assert _flat(c["sentence"]) in text, c["sentence"]
    assert len(r.reverted) == len(set_a)


def test_ignored_data_never_reaches_the_sources_or_the_checker(tmp_path):
    doc = _doc("README")
    root = _repository(tmp_path, doc)
    assert (root / "out" / "readings.txt").exists()
    model = Model(doc)
    r = humanize(root / doc.path, spawn=model, root=root)
    assert "stationlog/config.py" in r.sources and "out/readings.txt" not in r.sources
    assert model.exports and not any(e["has_out"] for e in model.exports)
    assert not any(MARKER in prompt for prompt in model.prompts)


def test_a_rewrite_with_more_rejections_than_its_cap_is_refused_and_kept(tmp_path):
    doc = _doc("ALERTS")
    root = _repository(tmp_path, doc)
    old, new = doc.texts()
    r = humanize(root / doc.path, spawn=Model(doc), root=root)
    assert not r.written and "blocks" in r.refused, r.refused
    assert (root / doc.path).read_text() == old
    kept = root / "docs" / "ALERTS.refused.md"
    assert r.kept == kept and kept.read_text() == new
    ignored = subprocess.run(["git", "check-ignore", "-q", "docs/ALERTS.refused.md"], cwd=root)
    assert ignored.returncode == 0


def test_a_citation_five_lines_away_is_unverifiable_and_its_sentence_goes_back(tmp_path):
    doc = _doc("OPERATIONS")
    sentence = "The number of days to keep readings defaults to 400."
    root = _repository(tmp_path, doc)
    r = humanize(root / doc.path, spawn=Model(doc, shift=sentence), root=root)
    assert r.written, r.refused
    verdict = _verdict_for(r, sentence)
    assert verdict is not None and verdict.verdict == "TRUE" and verdict.effective == "UNVERIFIABLE"
    text = _flat((root / doc.path).read_text())
    assert sentence not in text
    assert "When the file is missing, stationlog starts with no stations at all." in text


# The replay scripts read the corpus


def _records(tmp_path):
    """Replay records built from the scripted runs, as replay_check.py saves them."""
    paths = []
    for doc in CORPUS.documents:
        root = _repository(tmp_path / doc.name, doc)
        r = humanize(root / doc.path, spawn=Model(doc), root=root, write=False)
        v = r.verification
        record = {"corpus": doc.repo, "doc": doc.path, "run": 1, "raw": {str(k): x for k, x in v.raw.items()},
                  "verdicts": [{"number": x.number, "new": c.new}
                               for c in claims(r.changes) for x in [v.by_number[c.number]]]}
        path = tmp_path / f"{doc.name}.json"
        path.write_text(json.dumps(record))
        paths.append(str(path))
    return paths


def _script(name, *args):
    return subprocess.run([sys.executable, str(HERE / "scripts" / name), *args],
                          capture_output=True, text=True, cwd=HERE)


def test_rejudge_and_agreement_run_on_the_sample_corpus(tmp_path):
    records = _records(tmp_path)
    wrong = len(LABELS["set_a"]) + len(LABELS["over_cap"]["set_a"])
    true = len(LABELS["set_c"]) + len(LABELS["over_cap"]["set_c"])
    r = _script("rejudge_replay.py", "--corpus", str(SAMPLE), *records)
    assert r.returncode == 0, r.stderr
    assert f"TOTAL set A caught {wrong} of {wrong}; set C confirmed {true} of {true}" in r.stdout, r.stdout
    r = _script("agreement.py", "--corpus", str(SAMPLE), *records)
    assert r.returncode == 0, r.stderr
    assert f"wrong sentences caught {wrong} of {wrong} run-slots" in r.stdout, r.stdout
    assert f"true sentences confirmed {true} and kept {true} of {true}" in r.stdout


def test_the_record_directory_defaults_to_the_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert replay_corpus.default_out() == tmp_path / "state" / "writing-register" / "replay"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert replay_corpus.default_out() == Path.home() / ".local" / "state" / "writing-register" / "replay"


def test_replay_check_needs_a_corpus():
    r = _script("replay_check.py")
    assert r.returncode == 2 and "--corpus" in r.stderr

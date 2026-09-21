"""Admission of mined rules, computed from the verified points (2026-09-21).

A candidate rule enters the voice when the person's own words back it in at
least two separate sessions and nothing they said contradicts it. Everything
else goes to a ledger the person decides. Admission is recomputed from the raw
points every time, so nobody's summary of the evidence can stand in for it.
"""
import importlib.util
import json
import sys
from pathlib import Path


def _load(name):
    path = Path(__file__).resolve().parent.parent / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ma = _load("mine_admit")


def _pt(key, session, date="2026-09-10", relation="none", quote="define the gain", kind="correction",
        is_writing="yes", episode=None):
    return {"episode": episode or f"ep-{session}-{key}", "point": 1, "is_writing": is_writing, "kind": kind,
            "quote": quote, "principle": "Define it.", "entity": "math", "key": key, "relation": relation,
            "before_ref": "", "after_ref": "", "confidence": "high", "date": date, "session": session}


def _cand(cid="math.symbol-at-first-use", keys=("math.symbol-at-first-use",), text=None):
    return {"id": cid, "entity": "math", "keys": list(keys),
            "text": text or "Each symbol in a formula is defined in words where it first appears."}


PROJECTS = {"s1": "stations", "s2": "stations", "s3": "gauges"}


def test_two_sessions_admit_a_rule():
    points = [_pt("math.symbol-at-first-use", "s1"), _pt("math.symbol-at-first-use", "s3", date="2026-09-12")]
    [d] = ma.decide([_cand()], points, PROJECTS)
    assert d["verdict"] == "admit"
    assert d["sessions"] == ["s1", "s3"]
    assert d["flags"] == []


def test_one_session_goes_to_the_ledger_however_many_times_it_was_said():
    points = [_pt("math.symbol-at-first-use", "s1", episode=f"e{i}") for i in range(5)]
    [d] = ma.decide([_cand()], points, PROJECTS)
    assert d["verdict"] == "ledger"
    assert "one session" in d["why"]


def test_a_conflict_goes_to_the_ledger_even_with_many_sessions():
    points = [_pt("math.symbol-at-first-use", s) for s in ("s1", "s2", "s3")]
    points.append(_pt("math.symbol-at-first-use", "s2", relation="conflict", episode="ec"))
    [d] = ma.decide([_cand()], points, PROJECTS)
    assert d["verdict"] == "ledger"
    assert "conflict" in d["why"]


def test_weak_independence_is_flagged_but_does_not_block():
    points = [_pt("math.symbol-at-first-use", "s1"), _pt("math.symbol-at-first-use", "s2")]
    [d] = ma.decide([_cand()], points, PROJECTS)
    assert d["verdict"] == "admit"
    assert "one project" in d["flags"]
    points = [_pt("math.symbol-at-first-use", "s1", date="2026-09-10"),
              _pt("math.symbol-at-first-use", "s3", date="2026-09-10")]
    [d] = ma.decide([_cand()], points, PROJECTS)
    assert "one day" in d["flags"]


def test_points_that_are_not_about_writing_do_not_count():
    points = [_pt("math.symbol-at-first-use", "s1"), _pt("math.symbol-at-first-use", "s3", is_writing="no")]
    [d] = ma.decide([_cand()], points, PROJECTS)
    assert d["verdict"] == "ledger"


def test_a_candidate_gathers_every_key_synthesis_merged_into_it():
    points = [_pt("new:define-symbols", "s1"), _pt("math.symbol-at-first-use", "s3")]
    [d] = ma.decide([_cand(keys=("math.symbol-at-first-use", "new:define-symbols"))], points, PROJECTS)
    assert d["verdict"] == "admit"


def test_the_rule_text_is_linted_for_a_public_voice():
    names = {"Omar", "stationlog"}
    assert ma.lint("Each symbol is defined where it first appears.", names) == []
    problems = ma.lint("Ask Omar — then cite `run_gain_v2` from 2026-09-10 with 45 samples.", names)
    joined = " ".join(problems)
    for word in ("dash", "name", "identifier", "date", "number"):
        assert word in joined, word


def test_a_name_of_several_words_matches_as_a_phrase():
    names = {"North Ridge Labs"}
    assert ma.lint("Name the lab, North Ridge labs, by role.", names)
    assert ma.lint("A ridge in the north of the plot is labelled.", names) == []


def test_a_rule_that_fails_the_lint_is_refused_whatever_its_evidence():
    points = [_pt("math.symbol-at-first-use", s) for s in ("s1", "s3")]
    [d] = ma.decide([_cand(text="Define every symbol — always.")], points, PROJECTS)
    assert d["verdict"] == "refuse"
    assert "dash" in d["why"]


def test_evidence_for_existing_rules_is_reported_and_late_breaches_are_separate():
    points = [_pt("claims.provenance", "s1", date="2026-08-01", relation="violation"),
              _pt("claims.provenance", "s3", date="2026-09-18", relation="violation"),
              _pt("claims.provenance", "s2", date="2026-09-19", relation="extension")]
    report = ma.existing(points, {"claims.provenance"}, since="2026-09-14")
    r = report["claims.provenance"]
    assert r["sessions"] == ["s1", "s2", "s3"]
    assert [p["session"] for p in r["breached_after"]] == ["s3"]
    assert [p["session"] for p in r["extensions"]] == ["s2"]


def test_points_nobody_claimed_are_listed_so_nothing_is_lost():
    points = [_pt("new:orphan", "s1"), _pt("math.symbol-at-first-use", "s1")]
    assert [p["key"] for p in ma.unclaimed(points, [_cand()], existing_ids=set())] == ["new:orphan"]


def test_the_ledger_opens_with_a_summary_table_and_quotes_the_person():
    points = [_pt("math.symbol-at-first-use", "s1", quote="what is k here???")]
    decisions = ma.decide([_cand()], points, PROJECTS)
    text = ma.render_ledger(decisions, points)
    first_table = text.split("\n\n")[1]
    assert first_table.startswith("| Item |")
    assert "L1" in first_table
    assert "what is k here???" in text
    assert "2026-09-10" in text


def test_the_command_writes_decisions_and_ledger(tmp_path):
    (tmp_path / "verified.jsonl").write_text("".join(
        json.dumps(p) + "\n" for p in [_pt("math.symbol-at-first-use", "s1"), _pt("math.symbol-at-first-use", "s3")]))
    (tmp_path / "candidates.json").write_text(json.dumps([_cand()]))
    (tmp_path / "projects.json").write_text(json.dumps(PROJECTS))
    (tmp_path / "names.txt").write_text("Omar\n")
    rc = ma.main(["--points", str(tmp_path / "verified.jsonl"), "--candidates", str(tmp_path / "candidates.json"),
                  "--projects", str(tmp_path / "projects.json"), "--names", str(tmp_path / "names.txt"),
                  "--out", str(tmp_path / "out")])
    assert rc == 0
    decisions = json.loads((tmp_path / "out" / "decisions.json").read_text())
    assert decisions[0]["verdict"] == "admit"
    assert (tmp_path / "out" / "ledger.md").exists()


def _assign(point, *ids):
    return {"id": f"{point['episode']}#{point['point']}", "supports": list(ids)}


def test_a_point_counts_for_a_rule_when_two_of_three_readings_agree():
    # The reader filed both points under an existing rule; both assigners say they
    # support the candidate, so the candidate gains two sessions.
    p1 = _pt("introduce.first-use", "s1", date="2026-09-10")
    p3 = _pt("introduce.first-use", "s3", date="2026-09-12")
    a = [_assign(p1, "math.symbol-at-first-use"), _assign(p3, "math.symbol-at-first-use")]
    b = [_assign(p1, "math.symbol-at-first-use", "introduce.first-use"), _assign(p3, "math.symbol-at-first-use")]
    points = ma.with_votes([p1, p3], a, b)
    [d] = ma.decide([_cand()], points, PROJECTS)
    assert d["verdict"] == "admit"
    # introduce.first-use: reader key plus assigner B for p1 (two votes), reader key alone for p3.
    report = ma.existing(points, {"introduce.first-use"}, since="2026-09-14")
    assert report["introduce.first-use"]["sessions"] == ["s1"]


def test_one_assigner_alone_does_not_make_a_point_count():
    p1 = _pt("new:other", "s1")
    p3 = _pt("new:other", "s3", date="2026-09-12")
    a = [_assign(p1, "math.symbol-at-first-use"), _assign(p3, "math.symbol-at-first-use")]
    b = [_assign(p1), _assign(p3)]
    points = ma.with_votes([p1, p3], a, b)
    [d] = ma.decide([_cand()], points, PROJECTS)
    assert d["verdict"] == "ledger"


def test_the_reader_key_and_one_assigner_are_enough():
    p1 = _pt("math.symbol-at-first-use", "s1")
    p3 = _pt("math.symbol-at-first-use", "s3", date="2026-09-12")
    points = ma.with_votes([p1, p3], [_assign(p1, "math.symbol-at-first-use"), _assign(p3)],
                           [_assign(p1), _assign(p3, "math.symbol-at-first-use")])
    [d] = ma.decide([_cand()], points, PROJECTS)
    assert d["verdict"] == "admit"


def test_assigner_agreement_is_reported():
    p1, p3 = _pt("k", "s1"), _pt("k", "s3")
    stats = ma.assigner_agreement([_assign(p1, "x", "y"), _assign(p3)], [_assign(p1, "x"), _assign(p3)])
    assert stats == {"points": 2, "identical": 1, "overlap": 0.75}


def test_the_ledger_quotes_points_the_assigners_attached_to_an_item():
    p1 = _pt("introduce.first-use", "s1", quote="what is k here???")
    points = ma.with_votes([p1], [_assign(p1, "math.symbol-at-first-use")],
                           [_assign(p1, "math.symbol-at-first-use")])
    text = ma.render_ledger(ma.decide([_cand()], points, PROJECTS), points)
    assert "what is k here???" in text


def test_a_conflict_with_an_existing_rule_is_reported_for_that_rule():
    points = [_pt("documentation.results-first", "s1", relation="conflict", quote="method first")]
    r = ma.existing(points, {"documentation.results-first"}, since="2026-09-14")["documentation.results-first"]
    assert [p["quote"] for p in r["conflicts"]] == ["method first"]


def test_a_conflict_is_reported_even_when_no_assigner_calls_it_support():
    p = _pt("documentation.results-first", "s1", relation="conflict", quote="method first")
    points = ma.with_votes([p], [_assign(p)], [_assign(p)])
    report = ma.existing(points, {"documentation.results-first"}, since="2026-09-14")
    assert [x["quote"] for x in report["documentation.results-first"]["conflicts"]] == ["method first"]
    assert report["documentation.results-first"]["sessions"] == []


def test_the_ledger_recommends_from_the_weight_of_evidence():
    many = [_pt("math.symbol-at-first-use", "s1", episode=f"e{i}") for i in range(3)]
    one = [_pt("new:x", "s1")]
    cands = [_cand(), _cand(cid="figures.x", keys=("new:x",), text="Label every panel.")]
    text = ma.render_ledger(ma.decide(cands, many + one, PROJECTS), many + one)
    rows = [l for l in text.splitlines() if l.startswith("| L")]
    assert "| accept |" in rows[0] and "| hold |" in rows[1]

"""Reading packets and the mechanical check on what readers return (2026-09-21).

The candidates triage keeps are read by model readers, in packets small
enough to read carefully. A reader never retypes evidence: it quotes the
person, names pairs by id, and the script checks every quote against the
person's own words. A quote found only in what the person was looking at is
text they pasted or reacted to, not something they said, and is refused.
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


mp = _load("mine_packets")


def _candidate(n, words, seen="", pasted=(), pairs=(), suggested=False, labels=(), ts="2026-09-10T10:00:00Z"):
    return {"id": f"ep{n}", "origin_session": f"s{n % 3}", "ts": ts, "channel": "typed",
            "suggested": suggested, "words": words, "seen": seen, "pasted": list(pasted),
            "pairs": [{"pair_quality": q, "before": b, "after": a} for q, b, a in pairs],
            "fix_labels": list(labels), "response": {"commits": []}}


RULES = [("claims.provenance", "A number that stays says where it comes from."),
         ("introduce.first-use", "Every term gets half a line saying what it is where it first appears.")]


def test_a_packet_keeps_the_person_s_words_and_trims_what_they_saw():
    long_seen = " ".join(f"word{i}" for i in range(2000))
    packets = mp.build([_candidate(1, "what is the station gain here???", seen=long_seen)], RULES)
    p = packets[0]
    assert p["words"] == "what is the station gain here???"
    assert len(p["seen"].split()) <= mp.SEEN_WORDS
    assert p["seen"].split()[-1] == "word1999", "the end of the reply is what the person reacted to"


def test_suggested_prompts_are_not_read_and_repeats_are_read_once():
    packets = mp.build([_candidate(1, "go on", suggested=True),
                        _candidate(2, "explain the gain  properly"),
                        _candidate(3, "explain the gain properly")], RULES)
    assert [p["id"] for p in packets] == ["ep2"]
    assert packets[0]["repeats"] == ["ep3"]


def test_at_most_three_pairs_each_with_an_id_best_first():
    pairs = [("exact", f"old {i}", f"new {i}") for i in range(5)]
    p = mp.build([_candidate(1, "rewrite this, it is unclear", pairs=pairs)], RULES)[0]
    assert [x["id"] for x in p["pairs"]] == ["ep1#p1", "ep1#p2", "ep1#p3"]


def test_the_closest_existing_rules_travel_with_the_packet():
    p = mp.build([_candidate(1, "say where the number comes from, which measurement")], RULES)[0]
    assert p["rules"][0][0] == "claims.provenance"


def _point(**kw):
    base = {"episode": "ep1", "point": 1, "is_writing": "yes", "kind": "correction",
            "quote": "the gain is not defined", "principle": "Define a quantity where it first appears.",
            "entity": "introduce", "key": "introduce.first-use", "relation": "violation",
            "before_ref": "ep1#p1", "after_ref": "ep1#p1", "confidence": "high"}
    base.update(kw)
    return base


def test_a_quote_from_the_person_s_words_is_verified():
    packets = mp.build([_candidate(1, "the gain is not defined anywhere",
                                   seen="The station gain rises.", pairs=[("exact", "a", "b")])], RULES)
    ok, bad = mp.verify(packets, [_point()])
    assert len(ok) == 1 and not bad


def test_a_quote_the_person_only_saw_or_pasted_is_refused():
    packets = mp.build([_candidate(1, "fix this: The station gain rises. please",
                                   seen="The station gain rises.", pasted=["The station gain rises."],
                                   pairs=[("exact", "a", "b")])], RULES)
    ok, bad = mp.verify(packets, [_point(quote="The station gain rises.")])
    assert not ok and "saw" in bad[0]["why"]


def test_an_invented_quote_or_an_unknown_pair_is_refused():
    packets = mp.build([_candidate(1, "the gain is not defined anywhere", pairs=[("exact", "a", "b")])], RULES)
    _, bad = mp.verify(packets, [_point(quote="nothing like this was said")])
    assert "not in the person's words" in bad[0]["why"]
    _, bad = mp.verify(packets, [_point(before_ref="ep1#p9")])
    assert "ep1#p9" in bad[0]["why"]


def test_quotes_match_across_whitespace_and_apostrophes():
    packets = mp.build([_candidate(1, "it's   not clear at all", pairs=[("exact", "a", "b")])], RULES)
    ok, _ = mp.verify(packets, [_point(quote="its not clear at all", before_ref="", after_ref="")])
    assert ok


def test_packets_split_into_even_files_for_the_readers(tmp_path):
    packets = mp.build([_candidate(i, f"unclear wording number {i}") for i in range(10)], RULES)
    files = mp.write_packets(packets, tmp_path, readers=3)
    assert len(files) == 3
    text = "".join(f.read_text() for f in files)
    assert all(f"ep{i}" in text for i in range(10))
    assert json.loads((tmp_path / "packets.json").read_text())[0]["id"] == "ep0"


def test_fix_labels_may_arrive_as_records_with_their_source():
    c = _candidate(1, "the layout is messy, fix it")
    c["fix_labels"] = [{"label": "Method first, collapsible sections", "source": "publish"},
                       {"label": "docs: rewrite the setup guide", "source": "commit"}]
    p = mp.build([c], RULES)[0]
    assert p["labels"] == ["Method first, collapsible sections (publish)",
                           "docs: rewrite the setup guide (commit)"]
    assert "Method first, collapsible sections (publish)" in mp.render(p)


def test_agreement_between_two_blind_reads_of_the_same_packets():
    first = [_point(episode="ep1", entity="math", key="math.symbol-at-first-use"),
             _point(episode="ep1", point=2, entity="figures", key="new:legend"),
             _point(episode="ep2", entity="register", key="register.no-dashes")]
    second = [_point(episode="ep1", entity="math", key="math.symbol-at-first-use"),
              _point(episode="ep3", entity="order", key="order.point-first")]
    a = mp.agreement(first, second, ["ep1", "ep2", "ep3", "ep4"])
    assert a["packets"] == 4
    # ep1 both found writing, ep4 neither did: 2 of 4 agree on whether there is a point.
    assert a["found_agree"] == 2
    assert a["both_found"] == 1
    assert a["entity_overlap"] == 0.5   # {math, figures} against {math}
    assert a["key_overlap"] == 0.5
    assert a["only_first"] == ["ep2"] and a["only_second"] == ["ep3"]


def test_the_agree_command_prints_the_figures(tmp_path, capsys):
    (tmp_path / "a.jsonl").write_text(json.dumps(_point(episode="ep1")) + "\n")
    (tmp_path / "b.jsonl").write_text(json.dumps(_point(episode="ep1")) + "\n")
    (tmp_path / "ids.json").write_text(json.dumps(["ep1", "ep2"]))
    assert mp.main(["agree", "--first", str(tmp_path / "a.jsonl"), "--second", str(tmp_path / "b.jsonl"),
                    "--ids", str(tmp_path / "ids.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["found_agree"] == 2 and out["key_overlap"] == 1.0

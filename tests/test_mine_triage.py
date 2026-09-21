"""Triage, in scripts/mine_triage.py: which episodes are worth reading, picked
by cheap signals and no model call.

Every episode here is a minimal synthetic dict with only the fields triage
reads. The content is an invented weather-station project."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mt = _load("mine_triage", SCRIPTS / "mine_triage.py")

SLIPS = [{"heard": "Redmi", "meant": "README", "context": ""},
         {"heard": "AI slow", "meant": "AI slop", "context": ""},
         {"heard": "in a slot", "meant": "AI slop", "context": "when the sentence is about machine-written text"}]

_n = [0]


def _ep(words, *, seen="", channel="typed", minute=0, session="s-1", edits=(), publishes=(), labels=(),
        pasted=(), suggested=False):
    _n[0] += 1
    return {"id": f"{session}:{_n[0]}", "origin_session": session, "ts": f"2026-08-01T09:{minute:02d}:00Z",
            "channel": channel, "suggested": suggested, "text": words, "words": words, "pasted": list(pasted),
            "seen": seen, "response": {"edits": [{"path": p, "old": "a", "new": "b"} for p in edits],
                                       "publishes": list(publishes), "commits": []},
            "fix_labels": list(labels), "pairs": [], "before": {"source": "reply", "text": seen}}


def _features(*episodes, slips=()):
    return mt.triage(list(episodes), list(slips))[-1]


# ---------- features ----------

def test_response_edits_prose():
    assert _features(_ep("fix it", edits=["docs/rain.md"]))["features"]["response_edits_prose"]
    assert _features(_ep("fix it", edits=["site/rain.html"]))["features"]["response_edits_prose"]
    pub = {"label": "", "refused": False}
    assert _features(_ep("fix it", publishes=[pub]))["features"]["response_edits_prose"]
    assert not _features(_ep("fix it", edits=["logger.py"]))["features"]["response_edits_prose"]


def test_quote_overlap_from_a_paste_or_a_quoted_phrase():
    assert _features(_ep("this [pasted] is wrong", pasted=["the gust log rolls over at midnight"]))[
        "features"]["quote_overlap"]
    seen = "The gust factor is the ratio of peak to mean wind."
    assert _features(_ep('what does "gust factor" mean here', seen=seen))["features"]["quote_overlap"]
    assert not _features(_ep('call it "wind peak"', seen=seen))["features"]["quote_overlap"]


def test_term_question_needs_a_term_from_what_they_saw_that_they_had_not_used():
    seen = "The gust factor is the ratio of peak to mean wind."
    earlier = _ep("check the gust log", minute=0)
    assert _features(earlier, _ep("what is the gust factor", seen=seen, minute=5))["features"]["term_question"]
    # every content word already used by the person
    assert not _features(_ep("check the gust log", minute=0),
                         _ep("why is the gust log", seen="The gust log is late.", minute=5))["features"]["term_question"]
    # not a question
    assert not _features(_ep("add the gust factor", seen=seen))["features"]["term_question"]


def test_evaluative_wording():
    assert _features(_ep("this paragraph is too dense"))["features"]["evaluative"]
    assert _features(_ep("much better, i approve"))["features"]["evaluative"]
    # the complaints the voice's evidence repeats most
    assert _features(_ep("do not drop tiny details here and there"))["features"]["evaluative"]
    assert _features(_ep("these numbers are misleading"))["features"]["evaluative"]
    assert not _features(_ep("run the logger again"))["features"]["evaluative"]


def test_document_noun():
    assert _features(_ep("fix the legend"))["features"]["document_noun"]
    assert _features(_ep("the axes are swapped"))["features"]["document_noun"]
    assert _features(_ep("show the images of the wet days"))["features"]["document_noun"]
    assert _features(_ep("keep the weekly reports short"))["features"]["document_noun"]
    assert not _features(_ep("restart the logger"))["features"]["document_noun"]


def test_repetition_across_sessions():
    first = _ep("please explain every symbol on the rain page before you use it", session="s-1", minute=0)
    again = _ep("please explain every symbol on the wind page before you use it!", session="s-2", minute=9)
    other = _ep("restart the logger and send me the totals for the week", session="s-3", minute=10)
    results = mt.triage([first, again, other], [])
    assert [r["features"]["repetition"] for r in results] == [False, True, False]


def test_label_and_commit_links():
    f = _features(_ep("ok", labels=[{"label": "Legend names each gauge", "source": "publish"}]))["features"]
    assert f["label_link"] and not f["commit_link"]
    f = _features(_ep("ok", labels=[{"label": "Say what a tip measures", "source": "commit"}]))["features"]
    assert f["commit_link"] and not f["label_link"]


# ---------- the candidate rule ----------

def test_one_strong_signal_makes_a_candidate():
    seen = "The gust factor is the ratio of peak to mean wind."
    r = _features(_ep("check the gust log", minute=0), _ep("what is the gust factor", seen=seen, minute=5))
    assert r["candidate"] and "term_question" in r["reason"]


def test_a_label_link_is_strong_only_with_evaluative_wording_or_a_document_noun():
    label = [{"label": "Bars instead of lines", "source": "publish"}]
    assert not _features(_ep("go on", labels=label))["candidate"]
    assert _features(_ep("the chart is confusing", labels=label))["candidate"]


def test_two_weak_signals_make_a_candidate_and_one_does_not():
    assert _features(_ep("fix the legend", edits=["docs/rain.md"]))["candidate"]
    r = _features(_ep("fix the legend"))
    assert not r["candidate"] and r["reason"] == "one weak signal: document_noun"
    assert _features(_ep("the caption is unclear"))["candidate"]


def test_comments_and_answers_are_candidates_by_construction_and_suggestions_never():
    assert _features(_ep("ok", channel="comment"))["candidate"]
    assert _features(_ep("the snow gauge first", channel="answer"))["candidate"]
    r = _features(_ep("explain the unclear legend", suggested=True))
    assert not r["candidate"] and r["reason"] == "suggested prompt"


# ---------- slips ----------

def test_slips_normalise_matching_but_not_quoting():
    r = _features(_ep("the Redmi is out of order"), slips=SLIPS)
    assert r["features"]["document_noun"]
    assert r["matched_text"] == "the README is out of order"
    assert r["words"] == "the Redmi is out of order"
    assert _features(_ep("this text reads like AI slow"), slips=SLIPS)["features"]["evaluative"]
    assert _features(_ep("this text is in a slot"), slips=SLIPS)["features"]["evaluative"]
    assert not _features(_ep("the gauge sits in a slot"), slips=SLIPS)["features"]["evaluative"]


def test_slips_load_from_toml(tmp_path):
    path = tmp_path / "slips.toml"
    path.write_text('[[slip]]\nheard = "Redmi"\nmeant = "README"\ncontext = ""\n')
    assert mt.load_slips(path) == [{"heard": "Redmi", "meant": "README", "context": ""}]


# ---------- gold sets ----------

ABOUT = '''# About

> "explain every symbol before you use it" (2026-08-01)

> "the first chart [...] was fine. Clear enough." and "the second one is too busy" (2026-08-02, dictated)

On the wind page: "still misses the units" (2026-08-03). Not a quote: "short" (about 3, on many days).
'''

STANDARDS = '''# Standards

- **Units.** Every axis names its unit ("put the units on every axis").
- **Order.** Results come first ("results first, then the method"; "fold the method").
'''


def test_quotes_are_read_from_the_voice_evidence_and_the_page_standards():
    assert mt.about_quotes(ABOUT) == ["explain every symbol before you use it",
                                      "the first chart [...] was fine. Clear enough.",
                                      "the second one is too busy",
                                      "still misses the units"]
    assert mt.standards_quotes(STANDARDS) == ["put the units on every axis", "results first, then the method",
                                              "fold the method"]


def test_a_quote_is_found_verbatim_with_gaps_and_whitespace_normalised():
    eps = [_ep("ok so the first chart   was fine.\nClear enough. do the next"), _ep("the second one is too busy")]
    assert mt.find_quote("the first chart [...] was fine. Clear enough.", eps) == [eps[0]["id"]]
    assert mt.find_quote("the second one is too busy", eps) == [eps[1]["id"]]
    assert mt.find_quote("never said", eps) == []
    # a document that quotes the person may restore an apostrophe they left out
    typed = [_ep("arent we on the rain page?")]
    assert mt.find_quote("aren't we on the rain page?", typed) == [typed[0]["id"]]


def test_the_keyword_gold_set_is_typed_messages_only():
    eps = [_ep("why is the log late"), _ep("why is the log late again", suggested=True),
           _ep("why", channel="comment"), _ep("restart the logger")]
    assert [e["id"] for e in mt.keyword_gold(eps)] == [eps[0]["id"]]


def test_a_labelled_publish_without_a_person_behind_it_is_self_initiated():
    label = {"label": "Bars instead of lines", "source": "publish"}
    after_suggestion = _ep("go on", suggested=True, labels=[label])
    after_comment = _ep("the bars are too thin", channel="comment", labels=[label])
    results = mt.triage([after_suggestion, after_comment], [])
    unanchored = [{"label": "First draft", "refused": False, "ts": "2026-08-01T08:00:00Z", "origin_session": "s-9"}]
    selfs = mt.self_initiated([after_suggestion, after_comment], results, unanchored)
    assert [(s["label"], s["why"]) for s in selfs] == [("First draft", "before any input in its session"),
                                                       ("Bars instead of lines", "follows a suggested prompt")]

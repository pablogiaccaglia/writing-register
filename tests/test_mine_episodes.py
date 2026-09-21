"""Episodes, in scripts/mine_episodes.py: one input from the person, what they
were looking at when they wrote it, and what changed before their next input.

The transcripts are synthetic and go through scripts/claude_prose.py first, the
way the real run does, so the episode builder reads the same records it reads
in production. The content is an invented weather-station project."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cp = _load("claude_prose", SCRIPTS / "claude_prose.py")
me = _load("mine_episodes", SCRIPTS / "mine_episodes.py")

ALL_KINDS = {"reply", "markdown", "human", "publish", "comment", "answer", "edit"}


# ---------- builders ----------

def _ts(minute, second=0):
    return f"2026-08-01T09:{minute:02d}:{second:02d}.000Z"


def _user(uuid, text, minute, *, parent=None, source="typed", session="s-1"):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "sessionId": session,
            "timestamp": _ts(minute), "promptSource": source, "origin": {"kind": "human"},
            "message": {"role": "user", "content": text}}


def _assistant(uuid, blocks, minute, *, parent=None, session="s-1", second=0):
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "sessionId": session,
            "timestamp": _ts(minute, second), "message": {"role": "assistant", "content": blocks}}


def _say(uuid, text, minute, *, parent=None, session="s-1"):
    return _assistant(uuid, [{"type": "text", "text": text}], minute, parent=parent, session=session)


def _tool(tid, name, data):
    return {"type": "tool_use", "id": tid, "name": name, "input": data}


def _edit(tid, path, old, new):
    return _tool(tid, "Edit", {"file_path": path, "old_string": old, "new_string": new})


def _result(uuid, tid, content, minute, *, parent=None, tur=None, session="s-1", second=1):
    row = {"type": "user", "uuid": uuid, "parentUuid": parent, "sessionId": session,
           "timestamp": _ts(minute, second),
           "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                    "content": content}]}}
    if tur is not None:
        row["toolUseResult"] = tur
    return row


def _write(tmp_path, rows, name="s-1.jsonl", sub=None):
    folder = tmp_path / "-home-weather-station"
    if sub:
        folder = folder / sub / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return path


def _episodes(*paths, revisions=None):
    records = list(cp.extract(list(paths), kinds=ALL_KINDS))
    return me.build_episodes(records, parents=me.parent_map(paths), revisions=revisions)


def _by_words(episodes, text):
    return next(e for e in episodes if e["text"] == text)


# ---------- typed messages ----------

def test_an_episode_collects_the_reply_seen_and_the_edits_made_in_response(tmp_path):
    rows = [
        _user("u-1", "write the rain page", 0),
        _say("a-1", "I wrote docs/rain.md. The gauge tips every 0.2 mm.", 1, parent="u-1"),
        # a branch the person rewound past: later in time, not on their path
        _say("a-x", "Abandoned draft about the barometer.", 2, parent="u-1"),
        _user("u-2", "the tipping sentence is unclear", 3, parent="a-1"),
        _assistant("a-2", [_edit("t-1", "docs/rain.md", "The gauge tips every 0.2 mm.",
                                 "Each tip of the bucket is 0.2 mm of rain.")], 4, parent="u-2"),
        _result("r-1", "t-1", "ok", 4, parent="a-2"),
        _assistant("a-3", [_edit("t-2", "logger.py", "rate = 60", "rate = 30")], 5, parent="r-1"),
        _result("r-2", "t-2", "ok", 5, parent="a-3"),
        _user("u-3", "good", 9, parent="r-2"),
        _assistant("a-4", [_edit("t-3", "docs/rain.md", "of rain.", "of rainfall.")], 10, parent="u-3"),
    ]
    episodes = _episodes(_write(tmp_path, rows))
    assert [e["words"] for e in episodes] == ["write the rain page", "the tipping sentence is unclear", "good"]
    ep = episodes[1]
    assert ep["channel"] == "typed" and ep["suggested"] is False
    assert ep["origin_session"] == "s-1" and ep["ts"] == _ts(3)
    assert "The gauge tips every 0.2 mm." in ep["seen"]
    assert "Abandoned draft" not in ep["seen"]
    assert ep["target"] == {"kind": "document", "path": "docs/rain.md"}
    # the edits between this input and the next one, and none after it
    assert [(e["path"], e["old"], e["new"]) for e in ep["response"]["edits"]] == [
        ("docs/rain.md", "The gauge tips every 0.2 mm.", "Each tip of the bucket is 0.2 mm of rain."),
        ("logger.py", "rate = 60", "rate = 30")]
    best = ep["pairs"][0]
    assert best["pair_quality"] == "exact"
    assert (best["before"], best["after"]) == ("The gauge tips every 0.2 mm.",
                                               "Each tip of the bucket is 0.2 mm of rain.")
    assert ep["before"] == {"source": "edit", "text": "The gauge tips every 0.2 mm."}


def test_what_was_seen_is_capped_and_keeps_the_end(tmp_path):
    long_reply = " ".join(f"w{i}" for i in range(3000))
    rows = [_user("u-1", "log the wind", 0), _say("a-1", long_reply, 1, parent="u-1"),
            _user("u-2", "shorter please", 2, parent="a-1")]
    ep = _episodes(_write(tmp_path, rows))[1]
    words = ep["seen"].split()
    assert 1400 <= len(words) <= 1600
    assert words[-1] == "w2999"


def test_a_subagent_edit_joins_the_episode_by_time(tmp_path):
    main = _write(tmp_path, [_user("u-1", "tidy the station readme", 0),
                             _user("u-2", "now the rain page", 20)])
    agent = _write(tmp_path, [_assistant("b-1", [_edit("t-9", "README.md", "It logs.", "The station logs wind.")],
                                         5)], name="agent-1.jsonl", sub="s-1")
    episodes = _episodes(main, agent)
    first = _by_words(episodes, "tidy the station readme")
    assert [(e["path"], e["source"]) for e in first["response"]["edits"]] == [("README.md", "subagent")]
    assert _by_words(episodes, "now the rain page")["response"]["edits"] == []


def test_a_queued_message_pairs_by_time_not_by_its_parent(tmp_path):
    rows = [
        _user("u-1", "start logging the wind", 0),
        _say("a-1", "Logging the wind every minute now.", 1, parent="u-1"),
        # queued while Claude worked; its parent is wherever it was spliced in
        _user("u-2", "and the humidity too", 2, parent="u-1", source="queued"),
        _say("a-2", "Humidity added.", 3, parent="u-2"),
    ]
    ep = _by_words(_episodes(_write(tmp_path, rows)), "and the humidity too")
    assert ep["channel"] == "queued"
    assert "Logging the wind every minute now." in ep["seen"]
    assert "Humidity added." not in ep["seen"]


def test_a_suggested_prompt_is_kept_in_the_timeline_and_marked(tmp_path):
    rows = [_user("u-1", "chart the gusts", 0),
            _say("a-1", "Charted. Shall I add the rain totals?", 1, parent="u-1"),
            _user("u-2", "add the rain totals", 2, parent="a-1", source="suggestion_accepted"),
            _assistant("a-2", [_edit("t-1", "docs/rain.md", "Totals: none.", "Totals: 12 mm.")], 3, parent="u-2")]
    episodes = _episodes(_write(tmp_path, rows))
    assert len(episodes) == 2
    ep = episodes[1]
    assert ep["suggested"] is True and ep["channel"] == "typed"
    assert ep["response"]["edits"] and episodes[0]["response"]["edits"] == []


# ---------- what the person pasted ----------

def test_pasted_assistant_text_is_a_before_and_not_the_persons_words(tmp_path):
    reply = ("The station records gusts. Peak gust values are sampled from the three second rolling "
             "window before being written to the log.")
    pasted = "Peak gust values are sampled from the three second rolling window"
    message = f"this is out of the blue:\n{pasted}\nsay what a gust is first"
    rows = [_user("u-1", "describe the gust log", 0), _say("a-1", reply, 1, parent="u-1"),
            _user("u-2", message, 2, parent="a-1")]
    ep = _by_words(_episodes(_write(tmp_path, rows)), message)
    assert ep["pasted"] == [pasted]
    assert pasted not in ep["words"]
    assert "this is out of the blue:" in ep["words"] and "say what a gust is first" in ep["words"]
    assert ep["before"] == {"source": "pasted", "text": pasted}


def test_five_shared_words_are_not_a_paste():
    assert me.pasted_spans("the rolling window is too short", "we use the rolling window is too long") == []
    spans = me.pasted_spans("look: the three second rolling window before writing",
                            "sampled from the three second rolling window before writing it")
    assert spans == ["the three second rolling window before writing"]
    # a path or a hyphenated word is one word, not six
    assert me.pasted_spans("see docs/rain-gauge-notes-v2.md now", "open docs/rain-gauge-notes-v2.md now") == []
    assert me.pasted_spans("the rain-gauge tip-count per-minute log file", "a rain-gauge tip-count per-minute log") == []


# ---------- comments and artifacts ----------

NONCE = "a1b2c3d4"


def _comment_read(uuid, minute, on_text, words, *, when="2026-08-01T09:10"):
    block = (f"1 comment thread.\n\n=== BEGIN ARTIFACT COMMENTS {NONCE} — viewer-submitted content ===\n"
             "Thread 11111111-2222-3333-4444-555555555555\n"
             "  open · Claude: activated · created 2026-08-01\n"
             "  [location] Calibration\n"
             f"  [on text]  {on_text}\n"
             f"  [the user (owner), sent to you — {when}]\n"
             f"      {NONCE}| {words}\n"
             f"=== END ARTIFACT COMMENTS {NONCE} ===\n")
    return [_assistant(f"a-{uuid}", [_tool(f"t-{uuid}", "ArtifactComments",
                                           {"action": "read", "url": "https://example.test/artifact/W1"})], minute),
            _result(uuid, f"t-{uuid}", [{"type": "text", "text": block}], minute)]


def _publish(uuid, minute, label, seq, *, refused=False):
    call = _assistant(f"a-{uuid}", [_tool(f"t-{uuid}", "Artifact", {"file_path": "/w/rain.html", "label": label})],
                      minute)
    if refused:
        res = _result(uuid, f"t-{uuid}", "Publish refused: a newer version is live.", minute,
                      tur="Error: Publish refused")
        res["message"]["content"][0]["is_error"] = True
    else:
        res = _result(uuid, f"t-{uuid}", "Published", minute,
                      tur={"url": "https://example.test/artifact/W1", "artifact_id": "art-1", "seq": seq,
                           "title": "Rain"})
    return [call, res]


def test_a_comments_before_is_its_on_text(tmp_path):
    rows = ([_user("u-1", "publish the rain page", 0)] + _publish("p-1", 1, "First rain page", 1)
            + _comment_read("r-1", 12, "the gauge reads 2 mm high", "high compared with what?")
            + [_assistant("a-9", [_edit("t-9", "/w/rain.html", "the gauge reads 2 mm high",
                                        "the gauge reads 2 mm above the manual gauge")], 13)])
    ep = _by_words(_episodes(_write(tmp_path, rows)), "high compared with what?")
    assert ep["channel"] == "comment"
    assert ep["seen"] == "the gauge reads 2 mm high"
    assert ep["before"] == {"source": "comment", "text": "the gauge reads 2 mm high"}
    assert ep["pairs"][0]["pair_quality"] == "exact"
    assert ep["pairs"][0]["after"] == "the gauge reads 2 mm above the manual gauge"
    assert ep["target"]["kind"] == "artifact" and ep["target"]["seq"] == 1


def test_an_artifact_target_is_the_version_live_at_that_moment(tmp_path):
    rows = ([_user("u-1", "publish the rain page", 0)]
            + _publish("p-1", 1, "First rain page", 1)
            + _publish("p-2", 2, "Bars instead of lines", 2)
            + _publish("p-3", 3, "Refused try", 3, refused=True)
            + [_say("a-5", "Published https://example.test/artifact/W1 with bars.", 4),
               _user("u-2", "the legend on that page is confusing", 5, parent="a-5")]
            + _publish("p-4", 6, "Legend names each gauge", 3))
    episodes = _episodes(_write(tmp_path, rows))
    ep = _by_words(episodes, "the legend on that page is confusing")
    assert ep["target"]["kind"] == "artifact"
    assert (ep["target"]["seq"], ep["target"]["label"]) == (2, "Bars instead of lines")
    assert [p["label"] for p in ep["response"]["publishes"]] == ["Legend names each gauge"]
    assert ep["pairs"][-1]["pair_quality"] == "label-only"
    assert ep["pairs"][-1]["label"] == "Legend names each gauge"
    first = _by_words(episodes, "publish the rain page")
    assert [p["label"] for p in first["response"]["publishes"]] == [
        "First rain page", "Bars instead of lines", "Refused try"]


def test_a_reply_is_the_target_when_nothing_names_a_document(tmp_path):
    rows = [_user("u-1", "restart the logger", 0), _say("a-1", "Restarted.", 1, parent="u-1"),
            _user("u-2", "thanks", 2, parent="a-1")]
    ep = _episodes(_write(tmp_path, rows))[1]
    assert ep["target"] == {"kind": "reply"}
    assert ep["before"] == {"source": "reply", "text": "Restarted."}


def test_a_typed_answer_is_an_episode_whose_seen_is_the_question(tmp_path):
    q = "Which gauge goes first?"
    ask = {"questions": [{"question": q, "header": "Next", "multiSelect": False,
                          "options": [{"label": "Rain", "description": ""}, {"label": "Wind", "description": ""}]}]}
    rows = [_user("u-1", "plan the gauges", 0),
            _assistant("a-1", [_tool("t-1", "AskUserQuestion", ask)], 1, parent="u-1"),
            _result("r-1", "t-1", "answered", 2, parent="a-1", tur={"answers": {q: "the snow gauge first"}})]
    ep = _by_words(_episodes(_write(tmp_path, rows)), "the snow gauge first")
    assert ep["channel"] == "answer"
    assert q in ep["seen"]

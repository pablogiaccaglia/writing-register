"""The end-of-turn rewrite sends only the changed passages to the model.

The rest of the document goes into the prompt as context and never comes back
from the model, so it cannot drift (2026-09-15)."""
import re

from writing_register.passages import humanize_passages
from writing_register.spawn import Answer

BASE = """# Capture

The logger service writes one log per station into the out folder.

The alerter reads the log the next morning and builds the daily card.
"""
FILLER = ("It's worth noting that the logger service is a pivotal and robust foundation "
          "that seamlessly prepares every card for the team.")
CURRENT = BASE.replace("# Capture\n\n", f"# Capture\n\n{FILLER}\n\n")
CLEAN = "The logger service prepares every card for the team."


class _Bodies(tuple):
    """Passage bodies; the reply is built with the markers found in the prompt."""


def _reply(*texts):
    return _Bodies(texts)


def _render(reply, prompt):
    if not isinstance(reply, _Bodies):
        return reply
    token = re.search(r"\[\[(wr-[0-9a-f]+) passage 1\]\]", prompt).group(1)
    return "\n".join(f"[[{token} passage {i}]]\n{t}\n[[/{token} passage {i}]]"
                     for i, t in enumerate(reply, 1))


class Spawn:
    def __init__(self, reply="", during=None):
        self.reply, self.during, self.calls, self.prompts = reply, during, 0, []

    def run(self, prompt, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        if self.during:
            self.during()
        return Answer(stdout=_render(self.reply, prompt), command=("claude",), duration_seconds=0.1)


def _doc(tmp_path, text):
    p = tmp_path / "CAPTURE.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_nothing_changed_means_no_model_call(tmp_path):
    p = _doc(tmp_path, BASE)
    s = Spawn(_reply(CLEAN))
    r = humanize_passages(p, BASE, spawn=s)
    assert s.calls == 0 and r.passages == 0 and not r.written and not r.refused
    assert p.read_text(encoding="utf-8") == BASE


def test_only_the_marked_passage_is_replaced(tmp_path):
    p = _doc(tmp_path, CURRENT)
    s = Spawn(_reply(CLEAN))
    r = humanize_passages(p, BASE, spawn=s)
    assert r.written and r.passages == 1
    assert p.read_text(encoding="utf-8") == CURRENT.replace(FILLER, CLEAN)


def test_the_prompt_shows_the_whole_document_and_marks_the_passage(tmp_path):
    p = _doc(tmp_path, CURRENT)
    s = Spawn(_reply(CLEAN))
    humanize_passages(p, BASE, spawn=s)
    prompt = s.prompts[0]
    assert re.search(r"\[\[wr-[0-9a-f]+ passage 1\]\]\n" + re.escape(FILLER) + r"\n\[\[/wr-[0-9a-f]+ passage 1\]\]", prompt)
    assert "The alerter reads the log the next morning" in prompt
    assert "only" in prompt.lower() and "humanizer" in prompt.lower()


def test_two_passages_are_spliced_in_their_places(tmp_path):
    second = ("It's worth noting that the alerter is a truly transformative system that "
              "serves as a testament to the daily card.")
    current = CURRENT.replace("builds the daily card.\n", f"builds the daily card.\n\n{second}\n")
    p = _doc(tmp_path, current)
    r = humanize_passages(p, BASE, spawn=Spawn(_reply(CLEAN, "The alerter builds the daily card.")))
    assert r.written and r.passages == 2
    assert p.read_text(encoding="utf-8") == current.replace(FILLER, CLEAN).replace(
        second, "The alerter builds the daily card.")


def test_a_reply_that_misses_a_passage_is_refused(tmp_path):
    p = _doc(tmp_path, CURRENT)
    r = humanize_passages(p, BASE, spawn=Spawn("The logger service prepares every card."))
    assert not r.written and "passage" in r.refused
    assert p.read_text(encoding="utf-8") == CURRENT


def test_the_checks_apply_to_the_passages(tmp_path):
    p = _doc(tmp_path, CURRENT)
    r = humanize_passages(p, BASE, spawn=Spawn(_reply("The logger service prepares 12 cards for the team.")))
    assert not r.written and "12" in r.refused
    assert p.read_text(encoding="utf-8") == CURRENT
    assert r.kept and r.kept.read_text(encoding="utf-8") == CURRENT.replace(
        FILLER, "The logger service prepares 12 cards for the team.")


def test_a_number_elsewhere_in_the_document_may_appear_in_a_passage(tmp_path):
    base = BASE.replace("per station", "per station, kept for 30 days,")
    current = base.replace("# Capture\n\n", f"# Capture\n\n{FILLER}\n\n")
    p = _doc(tmp_path, current)
    r = humanize_passages(p, base, spawn=Spawn(_reply("The logger service keeps each card for 30 days.")))
    assert r.written, r.refused


def test_an_edit_during_the_call_is_kept(tmp_path):
    p = _doc(tmp_path, CURRENT)

    def edit():
        p.write_text(CURRENT + "\nAn edit made during the call.\n", encoding="utf-8")

    r = humanize_passages(p, BASE, spawn=Spawn(_reply(CLEAN), during=edit))
    assert not r.written and r.conflict
    assert "An edit made during the call." in p.read_text(encoding="utf-8")

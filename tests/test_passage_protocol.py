"""How the passage rewrite talks to the model, and what it refuses.

Review 2, 2026-09-15: marker text inside a passage cut it short; a reply with
extra text, duplicate or reordered passages was accepted; the length floor was
applied to all passages together, so one deleted passage could hide behind a
long one; and a rewrite could add a heading or restructure a passage without
any check noticing."""
import re

from writing_register.passages import humanize_passages
from writing_register.spawn import Answer

BASE = """# Capture

The logger service writes one log per station into the out folder.

## Setup

The alerter reads the log the next morning and builds the daily card.
"""
ONE = ("It's worth noting that the logger service is a pivotal and robust foundation "
       "that seamlessly prepares every card for the team.")
TWO = ("It is also worth noting that the alerter serves as a testament to careful design "
       "and reads every log with great care.")
CURRENT = BASE.replace("# Capture\n\n", f"# Capture\n\n{ONE}\n\n").replace(
    "daily card.\n", f"daily card.\n\n{TWO}\n")


class Replying:
    """A model that answers with the markers found in the prompt."""

    def __init__(self, bodies=None, raw=None):
        self.bodies, self.raw, self.prompts = bodies or [], raw, []

    def run(self, prompt, **kw):
        self.prompts.append(prompt)
        token = re.search(r"\[\[(wr-[0-9a-f]+) passage 1\]\]", prompt).group(1)
        if self.raw is not None:
            reply = self.raw(token)
        else:
            reply = "\n".join(f"[[{token} passage {i}]]\n{b}\n[[/{token} passage {i}]]"
                              for i, b in enumerate(self.bodies, 1))
        return Answer(stdout=reply, command=("claude",), duration_seconds=0.1)


def _doc(tmp_path, text=CURRENT):
    p = tmp_path / "CAPTURE.md"
    p.write_text(text, encoding="utf-8")
    return p


GOOD = ["The logger service prepares every card for the team.",
        "The alerter reads every log carefully."]


def test_the_markers_carry_a_token_that_changes_every_call(tmp_path):
    s = Replying(GOOD)
    humanize_passages(_doc(tmp_path), BASE, spawn=s, write=False)
    humanize_passages(_doc(tmp_path), BASE, spawn=s, write=False)
    tokens = {re.search(r"\[\[(wr-[0-9a-f]+) passage", p).group(1) for p in s.prompts}
    assert len(tokens) == 2 and "[[wr passage" not in s.prompts[0]


def test_a_passage_that_mentions_marker_text_is_returned_whole(tmp_path):
    mention = ("The model reply wraps each passage as [[wr passage 1]] and [[/wr passage 1]] "
               "so the hook can find it again later.")
    current = BASE.replace("# Capture\n\n", f"# Capture\n\n{mention}\n\n")
    p = _doc(tmp_path, current)
    r = humanize_passages(p, BASE, spawn=Replying([mention.replace("so the hook", "and the hook")]))
    assert r.written, r.refused
    assert "and the hook can find it again later." in p.read_text(encoding="utf-8")


def test_text_outside_the_markers_is_refused(tmp_path):
    p = _doc(tmp_path)
    r = humanize_passages(p, BASE, spawn=Replying(raw=lambda t: (
        f"Here you go:\n[[{t} passage 1]]\n{GOOD[0]}\n[[/{t} passage 1]]\n"
        f"[[{t} passage 2]]\n{GOOD[1]}\n[[/{t} passage 2]]")))
    assert not r.written and "outside" in r.refused and r.retry


def test_duplicate_or_reordered_passages_are_refused(tmp_path):
    dup = lambda t: (f"[[{t} passage 1]]\n{GOOD[0]}\n[[/{t} passage 1]]\n"
                     f"[[{t} passage 1]]\n{GOOD[1]}\n[[/{t} passage 1]]")
    swapped = lambda t: (f"[[{t} passage 2]]\n{GOOD[1]}\n[[/{t} passage 2]]\n"
                         f"[[{t} passage 1]]\n{GOOD[0]}\n[[/{t} passage 1]]")
    for raw in (dup, swapped):
        p = _doc(tmp_path)
        r = humanize_passages(p, BASE, spawn=Replying(raw=raw))
        assert not r.written and r.retry and p.read_text(encoding="utf-8") == CURRENT


def test_an_empty_passage_is_refused_even_next_to_a_long_one(tmp_path):
    p = _doc(tmp_path)
    r = humanize_passages(p, BASE, spawn=Replying([ONE, ""]))
    assert not r.written and "passage 2" in r.refused
    assert p.read_text(encoding="utf-8") == CURRENT


def test_each_passage_has_its_own_length_floor(tmp_path):
    p = _doc(tmp_path)
    r = humanize_passages(p, BASE, spawn=Replying([ONE + " " + ONE, "Alerter."]))
    assert not r.written and "passage 2" in r.refused and "a cut" in r.refused


def test_a_passage_split_into_two_paragraphs_is_refused(tmp_path):
    p = _doc(tmp_path)
    r = humanize_passages(p, BASE, spawn=Replying([
        "The logger service prepares cards.\n\nIt does this for the team.", GOOD[1]]))
    assert not r.written and "passage 1" in r.refused


def test_a_passage_that_adds_a_heading_is_refused(tmp_path):
    p = _doc(tmp_path)
    r = humanize_passages(p, BASE, spawn=Replying([f"## Surprise\n{GOOD[0]}", GOOD[1]]))
    assert not r.written and "passage 1" in r.refused


def test_list_items_must_stay_the_same_number(tmp_path):
    base = "# Steps\n\n- open the station page and read the gauge on time\n"
    added = ("- It is worth noting that the alerter then seamlessly reads the log\n"
             "- It is worth noting that the card is then robustly filed on the board")
    current = base + added + "\n"
    merged = "- The alerter reads the log and files the card on the board"
    r = humanize_passages(_doc(tmp_path, current), base, spawn=Replying([merged]))
    assert not r.written and "item" in r.refused
    two = ("- The alerter then reads the log\n"
           "- The card is then filed on the board")
    p = _doc(tmp_path, current)
    r = humanize_passages(p, base, spawn=Replying([two]))
    assert r.written, r.refused
    assert p.read_text(encoding="utf-8") == base + two + "\n"


def test_a_link_to_a_heading_elsewhere_in_the_document_is_allowed(tmp_path):
    p = _doc(tmp_path)
    r = humanize_passages(p, BASE, spawn=Replying([
        "The logger service prepares every card for the team (see [Setup](#setup)).", GOOD[1]]))
    assert r.written, r.refused


def test_the_prompt_uses_embedded_mode_and_changes_prose_only(tmp_path):
    """2026-09-15: the automatic rewrite adds no background any more."""
    s = Replying(GOOD)
    humanize_passages(_doc(tmp_path), BASE, spawn=s, write=False)
    instructions = s.prompts[0].split("# The humanizer skill")[0]
    assert "embedded mode" in instructions and "file mode" not in instructions
    assert "Change prose only" in instructions and "inside the marked passages" not in instructions
def test_a_content_refusal_is_not_marked_for_retry(tmp_path):
    p = _doc(tmp_path)
    r = humanize_passages(p, BASE, spawn=Replying(["The logger service prepares 12 cards.", GOOD[1]]))
    assert not r.written and "12" in r.refused and not r.retry

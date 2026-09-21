"""What a rewritten passage must keep so it still fits where it came from.

Review 3, 2026-09-15: a list item could come back with another marker, number
or indentation; a paragraph could come back with a setext underline and become
a heading; a sentence in the middle of a paragraph could lose its full stop and
run into the next one; one CRLF anywhere made the whole file CRLF; and links to
duplicate or setext headings were refused while a `#` line inside code counted
as a heading."""
import re

from writing_register.passages import humanize_passages
from writing_register.spawn import Answer


class Replying:
    def __init__(self, bodies):
        self.bodies = bodies

    def run(self, prompt, **kw):
        token = re.search(r"\[\[(wr-[0-9a-f]+) passage 1\]\]", prompt).group(1)
        reply = "\n".join(f"[[{token} passage {i}]]\n{b}\n[[/{token} passage {i}]]"
                          for i, b in enumerate(self.bodies, 1))
        return Answer(stdout=reply, command=("claude",), duration_seconds=0.1)


def _doc(tmp_path, text):
    p = tmp_path / "doc.md"
    p.write_bytes(text.encode())
    return p


FILLER = ("It is worth noting that this pivotal step seamlessly serves as a testament "
          "to our robust commitment to accuracy")


def test_a_nested_list_item_keeps_its_indentation_and_marker(tmp_path):
    base = "# Steps\n\n- open the station page and read the gauge\n  - check the battery first\n"
    current = base + f"  - {FILLER}\n"
    r = humanize_passages(_doc(tmp_path, current), base, spawn=Replying(["* The step checks accuracy"]))
    assert not r.written and "prefix" in r.refused
    p = _doc(tmp_path, current)
    r = humanize_passages(p, base, spawn=Replying(["  - The step checks accuracy"]))
    assert r.written, r.refused
    assert p.read_bytes().decode() == base + "  - The step checks accuracy\n"


def test_a_numbered_item_keeps_its_number(tmp_path):
    base = "# Steps\n\n1. open the station page\n2. read the gauge on time\n"
    current = base + f"3. {FILLER}\n"
    r = humanize_passages(_doc(tmp_path, current), base, spawn=Replying(["1. The step checks accuracy"]))
    assert not r.written and "prefix" in r.refused


def test_a_setext_underline_in_a_reply_is_refused(tmp_path):
    base = "# Notes\n\nThe logger service writes one log per station.\n"
    current = base + f"\n{FILLER}.\n"
    r = humanize_passages(_doc(tmp_path, current), base,
                          spawn=Replying(["The capture pipeline checks accuracy\n==="]))
    assert not r.written and "passage 1" in r.refused


def test_a_sentence_in_the_middle_of_a_paragraph_keeps_its_full_stop(tmp_path):
    base = "# Notes\n\nThe logger service writes logs. The alerter builds the card.\n"
    current = base.replace("logs. ", f"logs. {FILLER}. ")
    r = humanize_passages(_doc(tmp_path, current), base,
                          spawn=Replying(["This step checks that every log is accurate"]))
    assert not r.written and "full stop" in r.refused
    p = _doc(tmp_path, current)
    r = humanize_passages(p, base, spawn=Replying(["This step checks that every log is accurate."]))
    assert r.written, r.refused
    assert ("logs. This step checks that every log is accurate. The alerter builds the card."
            in p.read_text())


def test_mixed_line_endings_stay_exactly_as_they_were(tmp_path):
    base = "# X\r\n\nOld paragraph here about the logger service.\n\nAnother old one.\r\n"
    current = base.replace("Another old one.", f"{FILLER}.\r\n\r\nAnother old one.")
    p = _doc(tmp_path, current)
    r = humanize_passages(p, base, spawn=Replying(["This step checks that every log is accurate."]))
    assert r.written, r.refused
    assert p.read_bytes() == current.replace(
        f"{FILLER}.", "This step checks that every log is accurate.").encode()


def test_links_follow_github_heading_anchors(tmp_path):
    base = ("# Guide\n\n## Setup\n\nFirst setup text.\n\n## Setup\n\nSecond setup text.\n\n"
            "Setext title\n============\n\n```bash\n# secret anchor\n```\n")
    current = base.replace("First setup text.", f"First setup text. {FILLER}.")
    ok = humanize_passages(_doc(tmp_path, current), base, spawn=Replying(
        ["This step checks accuracy (see [the second setup](#setup-1) and [the title](#setext-title))."]),
        write=False)
    assert not ok.refused, ok.refused
    bad = humanize_passages(_doc(tmp_path, current), base, spawn=Replying(
        ["This step checks that every log is accurate (see [the secret](#secret-anchor))."]), write=False)
    assert "#secret-anchor" in bad.refused

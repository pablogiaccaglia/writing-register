"""Rewriting only what changed in a turn.

2026-09-15, on the markdown hook rewriting the whole file after every
turn ("act with the best possible solution"): three rewrites of one unchanged
sentence gave three wordings, so untouched paragraphs drifted and a word Claude
changed on request could be changed back. Only new prose is rewritten now.

The threshold comes from 5,292 real Edit changes to markdown files in the
session logs on 2026-09-15: the 12% that add 7 words or fewer are tweaks
(renumbered steps, a table cell, one word), while changes adding 8 or more
words are new content."""
from writing_register.passages import MIN_NEW_WORDS, blocks, changed_passages

DOC = """---
title: Setup
---

# Setup

The logger service writes one log per station into the out folder.

- first item of a list
- second item of a list

| Name | Value |
|---|---|
| a | 1 |

```bash
echo "a code block"

echo "with a blank line inside"
```

The alerter reads the log the next morning and builds the daily card.
"""


def test_the_threshold_is_eight_words():
    assert MIN_NEW_WORDS == 8


def test_blocks_are_split_by_kind_and_code_fences_stay_whole():
    kinds = [b.kind for b in blocks(DOC)]
    assert kinds == ["other", "heading", "paragraph", "list", "table", "code", "paragraph"]
    code = [b for b in blocks(DOC) if b.kind == "code"][0]
    assert "with a blank line inside" in code.text


def test_block_offsets_point_at_their_text():
    for b in blocks(DOC):
        assert DOC[b.start:b.end] == b.text


def test_an_unchanged_document_has_no_passages():
    assert changed_passages(DOC, DOC) == []


def test_a_one_word_edit_is_left_alone():
    current = DOC.replace("next morning", "following morning")
    assert changed_passages(DOC, current) == []


def test_a_new_paragraph_is_the_only_passage():
    added = ("It's worth noting that this service is a pivotal and robust foundation "
             "that seamlessly prepares every card for the team.")
    current = DOC.replace("# Setup\n\n", f"# Setup\n\n{added}\n\n")
    passages = changed_passages(DOC, current)
    assert [p.text for p in passages] == [added]
    assert current[passages[0].start:passages[0].end] == added


def test_a_paragraph_that_gains_a_sentence_is_a_passage():
    sentence = "It also keeps a copy of the screenshot next to the log for the card."
    current = DOC.replace("into the out folder.", f"into the out folder. {sentence}")
    passages = changed_passages(DOC, current)
    assert [p.text for p in passages] == [sentence]


def test_new_paragraphs_are_separate_passages():
    """Review 2: a passage stays inside one block, so a rewrite cannot merge or
    restructure paragraphs."""
    one = "The first new paragraph explains why the service writes one log per station."
    two = "The second new paragraph explains where the alerter finds that log the next day."
    current = DOC.replace("# Setup\n\n", f"# Setup\n\n{one}\n\n{two}\n\n")
    assert [p.text for p in changed_passages(DOC, current)] == [one, two]


def test_tables_code_and_headings_are_never_passages():
    current = (DOC.replace("| a | 1 |", "| a | 1 |\n| a much longer row that adds many many words to the table | 2 |")
               .replace('echo "a code block"', 'echo "a code block that now has a great many more words in it"')
               .replace("# Setup", "# Setup of the logger service and the alerter and the daily card"))
    assert changed_passages(DOC, current) == []


def test_a_new_file_is_all_new_prose():
    passages = changed_passages("", DOC)
    texts = [p.text for p in passages]
    assert any("logger service" in t for t in texts) and any("daily card" in t for t in texts)
    assert not any(t.startswith("|") or t.startswith("```") for t in texts)


# Review 2, 2026-09-15: shapes the first parser misread as prose, so their
# code, quotes or tables could be rewritten.

def _kinds(text):
    return [(b.kind, b.text.split("\n")[0][:20]) for b in blocks(text)]


def test_a_fence_holding_a_shorter_fence_stays_one_code_block():
    text = "Intro.\n\n````markdown\nExample:\n```bash\ngit commit -m update the capture\n```\nOutro.\n````\n\nAfter.\n"
    assert [k for k, _ in _kinds(text)] == ["paragraph", "code", "paragraph"]


def test_a_fence_is_closed_only_by_a_bare_fence_as_long():
    text = "```\ncode\n```python\nstill code\n```\n\nAfter the code block.\n"
    assert [k for k, _ in _kinds(text)] == ["code", "paragraph"]


def test_indented_code_is_code():
    text = "Install it like this.\n\n    pip install --editable .\n    wr voice\n\nThen run the command.\n"
    assert [k for k, _ in _kinds(text)] == ["paragraph", "code", "paragraph"]


def test_blockquotes_and_html_are_not_prose():
    text = ("> the error said: permission denied while opening the socket file\n\n"
            "<details>\n<summary>Log</summary>\nlong log text\n</details>\n\nAfter.\n")
    assert [k for k, _ in _kinds(text)] == ["quote", "html", "paragraph"]


def test_a_table_right_after_a_paragraph_is_a_table():
    text = "The results were these.\n| name | value |\n|---|---|\n| a | 1 |\n"
    assert [k for k, _ in _kinds(text)] == ["paragraph", "table"]


def test_a_table_without_leading_pipes_is_a_table():
    text = "name | value\n--- | ---\na | 1\n"
    assert [k for k, _ in _kinds(text)] == ["table"]


def test_setext_headings_are_headings():
    text = "Title\n=====\n\nA paragraph.\n\nSection\n-------\n\nAnother paragraph.\n"
    assert [k for k, _ in _kinds(text)] == ["heading", "paragraph", "heading", "paragraph"]


def test_a_list_can_start_right_after_a_paragraph():
    text = "The steps are:\n- open the file\n- run the command\n"
    assert [k for k, _ in _kinds(text)] == ["paragraph", "list"]


def test_a_rule_at_the_top_is_not_front_matter():
    text = "---\n\nFirst paragraph here.\n\n---\n\nSecond paragraph here.\n"
    assert [k for k, _ in _kinds(text)] == ["rule", "paragraph", "rule", "paragraph"]


def test_front_matter_needs_keys():
    text = "---\ntitle: Setup\ntags: [a, b]\n---\n\nA paragraph.\n"
    assert [k for k, _ in _kinds(text)] == ["other", "paragraph"]


def test_new_offsets_still_point_at_their_text():
    text = ("---\ntitle: X\n---\n\nIntro.\n| a | b |\n|---|---|\n\n    code\n\n> quote\n\n"
            "Title\n=====\n\nSteps:\n- one\n- two\n")
    for b in blocks(text):
        assert text[b.start:b.end] == b.text


# Review 2, 2026-09-15: the unit of change was the whole block, so one added
# sentence or bullet sent the old paragraph or list to the model, which could
# then delete most of it. The unit is now the sentence or the list item, and
# only new ones are sent.

OLD_PARAGRAPH = ("The logger service polls each station. It reads the live gauges. "
                 "It writes one log per station. The alerter reads it the next morning. "
                 "The card lands on the board.")


def test_a_sentence_added_to_a_paragraph_is_the_only_passage():
    added = "It also keeps the screenshot next to the log so the card can show the gauge."
    base = f"# Notes\n\n{OLD_PARAGRAPH}\n"
    current = base.replace("It writes one log per station.",
                           f"It writes one log per station. {added}")
    passages = changed_passages(base, current)
    assert [p.text for p in passages] == [added]
    assert current[passages[0].start:passages[0].end] == added


def test_an_item_added_to_a_long_list_is_the_only_passage():
    items = [f"- item number {i} describes one step of the capture flow" for i in range(1, 17)]
    base = "# Steps\n\n" + "\n".join(items) + "\n"
    added = "- item seventeen explains how the alerter files the finished card on the board"
    current = base.rstrip("\n") + "\n" + added + "\n"
    assert [p.text for p in changed_passages(base, current)] == [added]


def test_a_moved_paragraph_is_not_new():
    a = "Alpha paragraph is about the logger service and how it polls stations."
    b = "Beta paragraph is about the alerter and how it builds the daily card."
    assert changed_passages(f"{a}\n\n{b}\n", f"{b}\n\n{a}\n") == []


def test_a_lightly_edited_sentence_next_to_a_new_one_is_left_alone():
    base = f"# Notes\n\n{OLD_PARAGRAPH}\n"
    added = "It also keeps the screenshot next to the log so the card can show the gauge."
    current = base.replace("It reads the live gauges.",
                           f"It reads the live gauges from the page. {added}")
    assert [p.text for p in changed_passages(base, current)] == [added]


def test_a_heavily_rewritten_sentence_is_new():
    base = f"# Notes\n\n{OLD_PARAGRAPH}\n"
    current = base.replace("The card lands on the board.",
                           "Each finished card is filed on the shared team board where everyone sees it.")
    assert [p.text for p in changed_passages(base, current)] == [
        "Each finished card is filed on the shared team board where everyone sees it."]


def test_bullets_and_numbers_are_not_words():
    assert changed_passages("# List\n", "# List\n\n- one\n- two\n- three\n- four\n") == []
    assert changed_passages("# List\n", "# List\n\n1. one\n2. two\n3. three\n4. four\n") == []


def test_a_new_paragraph_of_short_sentences_is_one_passage():
    added = "It works. It is fast. It keeps every log. It never drops a card."
    current = DOC.replace("# Setup\n\n", f"# Setup\n\n{added}\n\n")
    assert [p.text for p in changed_passages(DOC, current)] == [added]


def test_a_large_changed_region_stays_fast():
    import time
    base = "\n\n".join(f"Paragraph {i} says the same thing about the logger service again." for i in range(400))
    current = "\n\n".join(f"Paragraph {i} says a different thing about the alerter and the card now." for i in range(400))
    started = time.monotonic()
    changed_passages(base, current)
    assert time.monotonic() - started < 3


# Review 3, 2026-09-15: sentence splitting, list items holding code, HTML
# blocks with blank lines, autolinks, and alignment speed and quality.

def test_a_sentence_starting_lowercase_or_accented_is_its_own_unit():
    old = "The release pipeline builds the wheel and uploads it."
    for added in ("npm then publishes the package to the registry for every team that depends on it.",
                  "È importante che il pacchetto venga pubblicato prima della riunione di domani mattina."):
        base = f"# Notes\n\n{old}\n"
        current = base.replace(old, f"{old} {added}")
        assert [p.text for p in changed_passages(base, current)] == [added]


def test_abbreviations_and_code_do_not_end_a_sentence():
    added = ("Claude adds checks, e.g. the length floor and the `a. B` guard, so that Mr. Smith "
             "and every reviewer can trust the result of each rewrite.")
    current = DOC.replace("# Setup\n\n", f"# Setup\n\n{added}\n\n")
    assert [p.text for p in changed_passages(DOC, current)] == [added]


def test_a_list_item_holding_a_code_fence_is_not_prose():
    base = "# Install\n\n- first step of the install that we already had\n"
    current = base + ("- It is worth noting that you should run this command in the terminal right now:\n"
                      "\n    ```bash\n    pip install writing-register\n    ```\n")
    assert all("```" not in p.text for p in changed_passages(base, current))


def test_an_html_comment_with_blank_lines_is_one_block():
    text = "<!--\nold notes that were commented out\n\nstill inside the comment here\n-->\n\nAfter.\n"
    assert [b.kind for b in blocks(text)] == ["html", "paragraph"]


def test_a_pre_block_with_blank_lines_is_one_block():
    text = "<pre>\nline one of the preformatted text\n\nline two of it\n</pre>\n\nAfter.\n"
    assert [b.kind for b in blocks(text)] == ["html", "paragraph"]


def test_an_autolink_line_is_prose():
    text = "<https://example.com/docs> explains the logger service and how it polls every station.\n"
    assert [b.kind for b in blocks(text)] == ["paragraph"]


def test_a_rename_across_a_long_document_sends_nothing():
    base = "\n\n".join(f"Section {i} explains how the logger service handles station number {i}."
                       for i in range(70))
    current = base.replace("logger service", "recorder")
    assert changed_passages(base, current) == []


def test_repeated_identical_blocks_stay_fast():
    import time
    base = "\n\n---\n\n".join(f"Section {i} text about the logger service." for i in range(400))
    current = base.replace("Section 200 text", "Section 200 text, with a new clause that adds several words,")
    started = time.monotonic()
    changed_passages(base, current)
    assert time.monotonic() - started < 2

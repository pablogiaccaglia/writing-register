"""Putting back text the rewrite reshaped (review of 2026-09-15).

The review found that reverting one new block at a time duplicated a paragraph the
rewrite had split, lost one of two paragraphs it had merged, and deleted a
paragraph it had moved and reworded, and that pairing counted stop words, so
an unrelated new sentence passed as a rewording. A revert now puts back every
block connected to the rejected sentence, puts a moved paragraph's original
back where the rewrite put it, and refuses when the shape cannot be undone in
part without duplicating text."""
import pytest

from writing_register.changes import RevertUnsafe, changes_between, claims, revert_blocks


def _failing(old, new, needle):
    changes = changes_between(old, new)
    return changes, {c.number for c in claims(changes) if needle in c.new}


def test_a_split_paragraph_is_put_back_once():
    old = "# T\n\nThe logger polls the gauge at nine. It reads the sensors every second and saves them.\n"
    new = "# T\n\nThe logger polls the gauge at nine.\n\nIt reads the sensors every second and saves them to the archive.\n"
    changes, failing = _failing(old, new, "to the archive")
    assert failing
    assert revert_blocks(new, old, changes, failing) == old


def test_a_merge_of_two_paragraphs_puts_both_back():
    old = "# T\n\nThe logger polls the gauge at nine.\n\nIt reads the sensors every second.\n"
    new = "# T\n\nThe logger polls the gauge at nine and reads the sensors every second into the archive.\n"
    changes, failing = _failing(old, new, "into the archive")
    assert failing
    assert revert_blocks(new, old, changes, failing) == old


def test_a_moved_and_reworded_paragraph_gets_its_original_back_where_it_now_is():
    old = "# A\n\nAlpha paragraph explains the login flow of the service.\n\n# B\n\nBeta paragraph explains the captcha flow of the service.\n"
    new = "# A\n\nBeta paragraph explains the captcha flow of the service and its retries.\n\n# B\n\nAlpha paragraph explains the login flow of the service.\n"
    changes, failing = _failing(old, new, "its retries")
    assert failing
    reverted = revert_blocks(new, old, changes, failing)
    assert "Beta paragraph explains the captcha flow of the service." in reverted
    assert "its retries" not in reverted
    assert reverted.count("Alpha paragraph") == 1 and reverted.count("Beta paragraph") == 1


def test_a_revert_that_would_duplicate_text_is_refused():
    old = "# T\n\nThe report is built every night.\n\nThe report is built every night.\n\nIt lists open findings.\n"
    new = "# T\n\nThe report is built every night and pushed to the archive by itself.\n\nIt lists open findings.\n"
    changes, failing = _failing(old, new, "pushed to the archive")
    reverted_or_refused = None
    try:
        reverted_or_refused = revert_blocks(new, old, changes, failing)
    except RevertUnsafe:
        reverted_or_refused = "refused"
    assert reverted_or_refused in ("refused", old)


def test_an_unrelated_new_sentence_is_not_a_rewording():
    old = "# T\n\nThe report is stored in the folder of the team.\n"
    new = "# T\n\nThe report is sent to the manager of the team every week.\n"
    kinds = sorted(c.kind for c in changes_between(old, new) if c.kind != "same")
    assert kinds == ["added", "removed"]


def test_claims_come_in_document_order_with_their_kinds():
    old = "# T\n\nFirst old sentence about the logger service.\n\nSecond old sentence about the alerter.\n"
    new = ("# T\n\nFirst old sentence about the logger service, reworded slightly here.\n\n"
           "A brand new paragraph about something else entirely, the retention sweep.\n\n"
           "Second old sentence about the alerter.\n")
    cl = claims(changes_between(old, new))
    assert [c.kind for c in cl] == ["changed", "added"]
    assert [c.number for c in cl] == [1, 2]
    assert cl[0].new.startswith("First old sentence") and "retention sweep" in cl[1].new


# 2026-09-15: the diagnostic on the saved replay records showed the
# group revert refusing one document in all three runs, although every
# rejected sentence there was an addition. A rejected addition is now removed on
# its own, which can neither repeat nor lose original text; a rejected rewording
# still puts back its group of blocks.

def test_a_rejected_addition_is_removed_and_the_rewordings_around_it_stay():
    old = "# T\n\nThe logger polls the gauge at nine. It reads the sensors every second.\n"
    new = "# T\n\nThe logger polls the station at nine. It also uploads every recording to a shared drive. It reads the sensors every second.\n"
    changes, failing = _failing(old, new, "uploads every recording")
    assert failing and all(c.kind == "added" for c in changes if c.number in failing)
    reverted = revert_blocks(new, old, changes, failing)
    assert reverted == "# T\n\nThe logger polls the station at nine. It reads the sensors every second.\n"


def test_a_rejected_addition_in_a_list_removes_only_that_item():
    old = "# Steps\n\n- open the station page\n- read the gauge\n"
    new = "# Steps\n\n- open the station page\n- upload the recording to the shared drive afterwards\n- read the gauge\n"
    changes, failing = _failing(old, new, "upload the recording")
    assert revert_blocks(new, old, changes, failing) == old


def test_additions_far_apart_are_removed_even_when_moved_sentences_join_their_blocks():
    moved = "The polling loop runs once a second and reads the newest rows."
    old = (f"# A\n\nThe region is found by its label. {moved}\n\n# B\n\nOther text about the bot account.\n\n"
           "# C\n\nClosing notes about the research.\n")
    new = ("# A\n\nThe region is found by its label, which the firmware sets on every reading.\n\n# B\n\n"
           f"Other text about the bot account. It also stores every screenshot in the cloud bucket.\n\n# C\n\n{moved} Closing notes about the research.\n")
    changes, failing = _failing(old, new, "stores every screenshot")
    reverted = revert_blocks(new, old, changes, failing)
    assert "stores every screenshot" not in reverted and reverted.count(moved) == 1

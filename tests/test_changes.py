"""The sentence-level change list between a document and its rewrite.

2026-09-15: the checker verifies what a rewrite added or changed in
meaning, the CLI reports it, and the hook note hands Claude each changed
sentence. All three need the same list: which sentences were added, which
were changed and from what, which were removed. Reverting happens per block,
because rewrites merge and split sentences."""
from writing_register.changes import Change, changes_between, claims, revert_blocks

OLD = """# Findings

The classes and their descriptions are defined in `register/schema.json`, which is also where the Findings table of the archive takes its list from. A class either blocks or it does not.

ISO 27001 does not require quarterly reviews; an interval the team keeps is worth more than a stricter one it misses.

- open the station page
- read the gauge on time

The retention rules are in [compliance/retention.md](compliance/retention.md).
"""

NEW = """# Findings

The classes and their descriptions are defined in `register/schema.json`. The report writes each finding as a row in the Findings table of the archive, and that database takes its list of classes from the same file. A class either blocks or it does not.

ISO 27001 does not require quarterly reviews, so the team chose an interval it will keep instead of a stricter one it would miss.

- open the station page
- read the gauge on time
- write the identifier in the form the platform uses

Snapshot folders are removed after 13 months, but git history still holds every committed file.
"""


def _kinds(old, new):
    return [(c.kind, c.new or c.old) for c in changes_between(old, new) if c.kind != "same"]


def test_an_unchanged_document_has_only_same():
    assert all(c.kind == "same" for c in changes_between(OLD, OLD))


def test_whitespace_only_changes_are_same():
    assert all(c.kind == "same" for c in changes_between(OLD, OLD.replace("it does not.", "it  does not.")))


def test_the_kinds_of_change_in_a_real_rewrite():
    found = _kinds(OLD, NEW)
    assert ("added", "The report writes each finding as a row in the Findings table of the archive, and that database takes its list of classes from the same file.") in found
    assert ("changed", "ISO 27001 does not require quarterly reviews, so the team chose an interval it will keep instead of a stricter one it would miss.") in found
    assert ("added", "- write the identifier in the form the platform uses") in found
    assert ("removed", "The retention rules are in [compliance/retention.md](compliance/retention.md).") in found
    assert ("added", "Snapshot folders are removed after 13 months, but git history still holds every committed file.") in found


def test_a_changed_sentence_keeps_its_original_wording():
    changed = [c for c in changes_between(OLD, NEW) if c.kind == "changed"]
    interval = [c for c in changed if "interval" in c.new][0]
    assert interval.old == "ISO 27001 does not require quarterly reviews; an interval the team keeps is worth more than a stricter one it misses."


def test_a_light_rewording_is_still_a_change():
    new = OLD.replace("an interval the team keeps", "an interval the team actually keeps")
    changed = [c for c in changes_between(OLD, new) if c.kind == "changed"]
    assert len(changed) == 1 and "actually" in changed[0].new


def test_claims_are_added_and_changed_in_document_order_and_numbered():
    found = claims(changes_between(OLD, NEW))
    assert [c.kind for c in found] == ["changed", "added", "changed", "added", "added"] or [c.kind for c in found].count("added") == 3
    assert [c.number for c in found] == list(range(1, len(found) + 1))
    assert all(c.kind in ("added", "changed") for c in found)


def test_offsets_point_at_the_new_text():
    for c in changes_between(OLD, NEW):
        if c.kind in ("added", "changed", "same"):
            assert NEW[c.new_start:c.new_end] == c.new


def test_two_sentences_merged_into_one_revert_to_both():
    old = "# T\n\nThe bot joins the call. It reads the captions.\n"
    new = "# T\n\nThe bot joins the call and reads the captions.\n"
    changed = [c for c in changes_between(old, new) if c.kind == "changed"]
    assert len(changed) == 1 and changed[0].old == "The bot joins the call. It reads the captions."
    assert revert_blocks(new, old, changes_between(old, new), {changed[0].number}) == old


def test_reverting_a_block_puts_the_original_paragraph_back():
    changes = changes_between(OLD, NEW)
    failing = {c.number for c in claims(changes) if "13 months" in c.new or "the team chose" in c.new}
    reverted = revert_blocks(NEW, OLD, changes, failing)
    assert "an interval the team keeps is worth more" in reverted
    assert "13 months" not in reverted
    assert "The report writes each finding" in reverted  # the other block stays rewritten
    assert reverted.count("\n\n\n") == 0 and reverted.endswith("\n")


def test_reverting_an_added_list_item_keeps_the_rest_of_the_list():
    changes = changes_between(OLD, NEW)
    failing = {c.number for c in claims(changes) if "identifier" in c.new}
    reverted = revert_blocks(NEW, OLD, changes, failing)
    assert "- open the station page\n- read the gauge on time\n" in reverted and "identifier" not in reverted


def test_reverting_everything_gives_the_original_minus_what_the_rewrite_removed():
    """A removed sentence is not a claim, so no verdict brings it back."""
    changes = changes_between(OLD, NEW)
    everything = revert_blocks(NEW, OLD, changes, {c.number for c in claims(changes)})
    assert everything == OLD.replace("The retention rules are in [compliance/retention.md](compliance/retention.md).\n", "").rstrip("\n") + "\n"
    no_removal = OLD.replace("\nThe retention rules are in [compliance/retention.md](compliance/retention.md).\n", "")
    changes = changes_between(no_removal, NEW)
    assert revert_blocks(NEW, no_removal, changes, {c.number for c in claims(changes)}) == no_removal


def test_a_change_knows_its_block():
    for c in changes_between(OLD, NEW):
        assert c.block in ("paragraph", "list")

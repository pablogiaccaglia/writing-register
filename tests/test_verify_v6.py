"""Checker prompt v6 (2026-09-15), from the agreement analysis of the saved runs.

Of the six sentences reverted in every run, two were true and the checker had
not looked where the evidence was: a bullet of one project's README is
confirmed by a script under extension/ and a README under deploy/, and
another sentence by the project's getting-started guide.
The prompt now says to search front-end, extension, deploy and configuration
code too, and that the repository's own guides and design notes may confirm
how the team works."""
from writing_register.verify import SYSTEM


def test_the_prompt_asks_to_search_beyond_the_main_source_folder():
    lowered = SYSTEM.lower()
    assert "front-end" in lowered and "extension" in lowered and "deploy" in lowered


def test_the_prompt_lets_the_repository_guides_confirm_how_the_team_works():
    lowered = SYSTEM.lower()
    assert "guides and design notes" in lowered and "how the team works" in lowered
    assert "neither the code nor the repository's documents" in lowered

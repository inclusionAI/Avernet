"""`goal_excerpt` on the run summary.

`RunSummaryDTO` carries a prefix of the goal so a list row can be told apart
without every row shipping its whole prompt. The full goal stays on the detail.
"""
from __future__ import annotations

from agentclaw.community.adapters.http.agentswarm.router import (
    _GOAL_EXCERPT_CHARS, _goal_excerpt,
)


def test_a_short_goal_is_returned_whole() -> None:
    assert _goal_excerpt("Say hello world") == "Say hello world"


def test_a_long_goal_is_cut_and_marked() -> None:
    excerpt = _goal_excerpt("x" * (_GOAL_EXCERPT_CHARS + 50))
    assert len(excerpt) == _GOAL_EXCERPT_CHARS + 1  # the ellipsis
    assert excerpt.endswith("…")


def test_newlines_collapse_so_a_row_stays_one_paragraph() -> None:
    # A goal is free text; a list row must not inherit its line breaks.
    assert _goal_excerpt("first\nsecond\n\nthird") == "first second third"


def test_an_empty_goal_yields_an_empty_excerpt() -> None:
    assert _goal_excerpt("") == ""
    assert _goal_excerpt(None) == ""


def test_whitespace_only_is_not_reported_as_a_goal() -> None:
    assert _goal_excerpt("   \n\t ") == ""


def test_trailing_space_is_trimmed_before_the_ellipsis() -> None:
    excerpt = _goal_excerpt("a " * 200)
    assert not excerpt[:-1].endswith(" ")
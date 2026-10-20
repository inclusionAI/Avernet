"""The ``file.edit`` kinds (README §6.2) and the ``description_only`` condition (§6.3)."""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_genome.text_edits import (
    EditDoesNotApply,
    apply_edits,
    apply_unified_diff,
    description_only,
    insert_after,
    replace_section,
)

SOUL = (
    "# Soul\nYou are a patient support agent.\n\n## Tone\nBe friendly and brief.\n\n"
    "## Escalation\nEscalate when the customer asks for a manager.\nAlways apologise first.\n\n"
    "## Closing\nThank the customer and close the ticket.\n"
)


# -- replace_section ---------------------------------------------------------------------------


def test_replace_section_keeps_the_heading_and_stops_at_the_next_heading_of_same_level() -> None:
    out = replace_section(SOUL, "## Escalation", "Escalate to a human.\n\n")
    assert out == SOUL.replace(
        "## Escalation\nEscalate when the customer asks for a manager.\nAlways apologise first.\n\n",
        "## Escalation\nEscalate to a human.\n\n",
    )


def test_replace_section_runs_to_the_end_when_no_heading_follows() -> None:
    assert replace_section(SOUL, "## Closing", "Say goodbye.") == SOUL.replace(
        "Thank the customer and close the ticket.\n", "Say goodbye.\n"
    )


def test_replace_section_stops_at_a_higher_level_heading_but_not_a_lower_one() -> None:
    text = "# A\n## B\nb\n### C\nc\n# D\nd\n"
    assert replace_section(text, "## B", "new\n") == "# A\n## B\nnew\n# D\nd\n"


def test_replace_section_ignores_headings_inside_fences() -> None:
    text = "## Real\n```md\n## Real\n```\nbody\n~~~\n## Also fenced\n~~~\n## Next\n"
    assert replace_section(text, "## Real", "x\n") == "## Real\nx\n## Next\n"
    with pytest.raises(EditDoesNotApply, match="occurs 0 times"):
        replace_section(text, "## Also fenced", "x")


def test_replace_section_needs_exactly_one_heading() -> None:
    with pytest.raises(EditDoesNotApply, match="occurs 2 times"):
        replace_section("## A\n## A\n", "## A", "x")
    with pytest.raises(EditDoesNotApply, match="occurs 0 times"):
        replace_section("##  A\n", "## A", "x")


def test_replace_section_with_empty_content_empties_the_section() -> None:
    assert replace_section("## A\nold\n## B\n", "## A", "") == "## A\n## B\n"
    assert replace_section("## A\nold", "## A", "") == "## A"


# -- insert_after ------------------------------------------------------------------------------


def test_insert_after_puts_content_directly_below_the_anchor() -> None:
    assert insert_after("a\nb\nc\n", "b", "x") == "a\nb\nx\nc\n"
    assert insert_after("a\nb", "b", "x\ny\n") == "a\nb\nx\ny\n"


def test_insert_after_needs_exactly_one_anchor() -> None:
    with pytest.raises(EditDoesNotApply, match="occurs 2 times"):
        insert_after("b\nb\n", "b", "x")
    with pytest.raises(EditDoesNotApply, match="occurs 0 times"):
        insert_after("a\nbb\n", "b", "x")


# -- diff --------------------------------------------------------------------------------------


def test_diff_applies_hunks_in_order() -> None:
    text = "".join(f"line {i}\n" for i in range(10))
    diff = "@@ -1,2 +1,2 @@\n line 0\n-line 1\n+LINE 1\n@@ -10 +10 @@\n-line 9\n+LINE 9\n"
    assert apply_unified_diff(text, diff) == text.replace("line 1\n", "LINE 1\n").replace("line 9\n", "LINE 9\n")


def test_diff_pure_insertion_and_deletion() -> None:
    assert apply_unified_diff("a\nb\n", "@@ -1,0 +2 @@\n+x\n") == "a\nx\nb\n"
    assert apply_unified_diff("a\nb\n", "@@ -0,0 +1 @@\n+x\n") == "x\na\nb\n"
    assert apply_unified_diff("a\nb\n", "@@ -2 +1,0 @@\n-b\n") == "a\n"
    assert apply_unified_diff("", "@@ -0,0 +1,2 @@\n+a\n+b\n") == "a\nb\n"


def test_diff_honours_no_newline_at_end_of_file() -> None:
    assert apply_unified_diff("a\nb", "@@ -2 +2 @@\n-b\n\\ No newline at end of file\n+c\n") == "a\nc\n"
    assert apply_unified_diff("a\nb\n", "@@ -2 +2 @@\n-b\n+c\n\\ No newline at end of file\n") == "a\nc"
    assert apply_unified_diff("a\nb", "@@ -1,2 +1,2 @@\n-a\n+A\n b\n\\ No newline at end of file\n") == "A\nb"
    with pytest.raises(EditDoesNotApply, match="without a newline"):
        apply_unified_diff("a\nb", "@@ -2 +2 @@\n-b\n+c\n")
    with pytest.raises(EditDoesNotApply, match="not the file's last line"):
        apply_unified_diff("a\nb\n", "@@ -2 +2 @@\n-b\n\\ No newline at end of file\n+c\n")


@pytest.mark.parametrize(
    ("diff", "problem"),
    [
        ("@@ -1 +1 @@\n-nope\n+b\n", "the diff expects"),
        ("@@ -3 +3 @@\n-a\n+b\n", "the diff expects"),
        ("@@ -2 +2 @@\n-b\n+x\n@@ -1 +1 @@\n-a\n+y\n", "overlaps"),
        ("--- a\n+++ b\n@@ -1 +1 @@\n-a\n+b\n", "not a hunk header"),
        ("@@ -1 +1 @@\n-a\n", "ends inside a hunk"),
        ("@@ -1 +1 @@\n-a\n+b\n+c\n", "not a hunk header"),
        ("@@ -1 +1 @@\nx\n+b\n", "not a hunk line"),
        ("", "no hunk"),
    ],
)
def test_diff_is_strict(diff: str, problem: str) -> None:
    with pytest.raises(EditDoesNotApply, match=problem):
        apply_unified_diff("a\nb\n", diff)


def test_apply_edits_in_order() -> None:
    out = apply_edits("a\nb\n", [{"kind": "insert_after", "anchor": "a", "content": "x"}, {"kind": "diff", "diff": "@@ -3 +3 @@\n-b\n+B\n"}])
    assert out == "a\nx\nB\n"
    with pytest.raises(EditDoesNotApply, match="unknown edit kind"):
        apply_edits("a", [{"kind": "regex"}])


# -- description_only --------------------------------------------------------------------------

SKILL_MD = "---\nname: quality-check\ndescription: Check a reply.\n---\nRead RULES.md.\n"


def test_description_only_accepts_a_change_to_the_description_value() -> None:
    assert description_only(SKILL_MD.encode(), SKILL_MD.replace("Check a reply.", "Check every draft.").encode())
    folded = SKILL_MD.replace("description: Check a reply.", "description: >\n  Check a reply\n  against the rules.")
    assert description_only(SKILL_MD.encode(), folded.encode())


@pytest.mark.parametrize(
    "after",
    [
        SKILL_MD,  # unchanged
        SKILL_MD.replace("Read RULES.md.", "Read nothing."),  # body
        SKILL_MD.replace("name: quality-check", "name: other"),  # another key
        SKILL_MD.replace("name: quality-check\n", "name: quality-check\nlicense: MIT\n"),  # key added
        "no front matter\n",
        SKILL_MD.replace("description: Check a reply.\n", ""),  # description removed
    ],
)
def test_description_only_refuses_anything_else(after: str) -> None:
    assert not description_only(SKILL_MD.encode(), after.encode())
    assert not description_only(b"\xff", SKILL_MD.encode())

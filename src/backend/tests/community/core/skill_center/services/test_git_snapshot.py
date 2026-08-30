"""Contract tests for deterministic Space Skill Git snapshots."""

from __future__ import annotations

import pytest

from agentclaw.community.core.skill_center.git_snapshot import (
    GitSnapshotInvalidError,
    select_skill_source_subdir,
)


def test_git_snapshot_selection_prefers_root_skill_manifest():
    assert select_skill_source_subdir(
        ("z/SKILL.md", "SKILL.md", "a/SKILL.md"), requested_subdir=None
    ) == ""


def test_git_snapshot_selection_uses_normalized_parent_byte_order():
    assert select_skill_source_subdir(
        ("中/SKILL.md", "z/SKILL.md", "a/nested/SKILL.md"),
        requested_subdir=None,
    ) == "a/nested"


def test_git_snapshot_selection_freezes_an_explicit_subdir():
    assert select_skill_source_subdir(
        ("a/SKILL.md", "b/SKILL.md"), requested_subdir="./b/"
    ) == "b"


@pytest.mark.parametrize(
    ("paths", "subdir"),
    [
        (("a/SKILL.md",), "missing"),
        (("a/SKILL.md",), "../a"),
        ((), None),
    ],
)
def test_git_snapshot_selection_rejects_missing_or_unsafe_targets(paths, subdir):
    with pytest.raises(GitSnapshotInvalidError):
        select_skill_source_subdir(paths, requested_subdir=subdir)

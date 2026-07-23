"""Migration Quarantine cleanup safety tests."""

from __future__ import annotations

from pathlib import Path

from engine.community.plugins.skills_pool.layout_quarantine import (
    QuarantineCleanupStatus,
    cleanup_quarantine,
)


def test_cleanup_removes_only_requested_generation(tmp_path: Path) -> None:
    first = (
        tmp_path
        / ".openclaw/workspace/skills-pool/.migration-quarantine/generation-1"
    )
    second = first.parent / "generation-2"
    (first / "skills-local/a").mkdir(parents=True)
    (second / "skills-local/b").mkdir(parents=True)

    result = cleanup_quarantine(
        engine="openclaw",
        home=tmp_path,
        migration_generation="generation-1",
    )

    assert result.status is QuarantineCleanupStatus.CLEANED
    assert not first.exists()
    assert second.is_dir()


def test_cleanup_missing_generation_is_idempotent(tmp_path: Path) -> None:
    result = cleanup_quarantine(
        engine="openclaw",
        home=tmp_path,
        migration_generation="generation-1",
    )

    assert result.status is QuarantineCleanupStatus.ALREADY_ABSENT


def test_cleanup_rejects_invalid_generation(tmp_path: Path) -> None:
    result = cleanup_quarantine(
        engine="openclaw",
        home=tmp_path,
        migration_generation="../generation-2",
    )

    assert result.status is QuarantineCleanupStatus.INVALID


def test_cleanup_rejects_symlinked_quarantine_parent(tmp_path: Path) -> None:
    pool = tmp_path / ".openclaw/workspace/skills-pool"
    outside = tmp_path / "outside"
    (outside / "generation-1/skills-local/a").mkdir(parents=True)
    pool.mkdir(parents=True)
    (pool / ".migration-quarantine").symlink_to(
        outside,
        target_is_directory=True,
    )

    result = cleanup_quarantine(
        engine="openclaw",
        home=tmp_path,
        migration_generation="generation-1",
    )

    assert result.status is QuarantineCleanupStatus.INVALID
    assert (outside / "generation-1/skills-local/a").is_dir()


def test_cleanup_rejects_symlinked_workspace_ancestor(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    quarantine = (
        outside
        / "workspace/skills-pool/.migration-quarantine/generation-1/skills-local/a"
    )
    quarantine.mkdir(parents=True)
    (tmp_path / ".openclaw").symlink_to(outside, target_is_directory=True)

    result = cleanup_quarantine(
        engine="openclaw",
        home=tmp_path,
        migration_generation="generation-1",
    )

    assert result.status is QuarantineCleanupStatus.INVALID
    assert quarantine.is_dir()

"""Migration Quarantine cleanup safety tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.community.plugins.claude_code.plugin_impl import ClaudeCodePluginImpl
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
from engine.community.plugins.skills_pool.layout_quarantine import (
    QuarantineCleanupResult,
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


def test_cleanup_rejects_generation_that_is_not_a_directory(
    tmp_path: Path,
) -> None:
    generation = (
        tmp_path
        / ".openclaw/workspace/skills-pool/.migration-quarantine/generation-1"
    )
    generation.parent.mkdir(parents=True)
    generation.write_text("not a quarantine directory")

    result = cleanup_quarantine(
        engine="openclaw",
        home=tmp_path,
        migration_generation="generation-1",
    )

    assert result.status is QuarantineCleanupStatus.INVALID
    assert result.evidence == {"reason": "generation_not_real_directory"}


def test_cleanup_reports_remove_failure_without_deleting_other_generations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation = (
        tmp_path
        / ".openclaw/workspace/skills-pool/.migration-quarantine/generation-1"
    )
    sibling = generation.parent / "generation-2"
    generation.mkdir(parents=True)
    sibling.mkdir()

    def fail_remove(_: Path) -> None:
        raise OSError("busy")

    monkeypatch.setattr(shutil, "rmtree", fail_remove)

    result = cleanup_quarantine(
        engine="openclaw",
        home=tmp_path,
        migration_generation="generation-1",
    )

    assert result.status is QuarantineCleanupStatus.TRANSIENT_ERROR
    assert result.evidence["reason"] == "remove_failed"
    assert generation.is_dir()
    assert sibling.is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plugin", "module_path", "engine"),
    [
        (
            OpenClawPluginImpl(),
            "engine.community.plugins.openclaw._skills.cleanup_quarantine",
            "openclaw",
        ),
        (
            ClaudeCodePluginImpl(),
            "engine.community.plugins.claude_code._skills.cleanup_quarantine",
            "claude_code",
        ),
    ],
)
async def test_plugin_cleanup_port_returns_structured_result(
    plugin: object,
    module_path: str,
    engine: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_cleanup(**kwargs: object) -> QuarantineCleanupResult:
        calls.append(kwargs)
        return QuarantineCleanupResult(
            QuarantineCleanupStatus.CLEANED,
            {"path_absent": True},
        )

    monkeypatch.setattr(module_path, fake_cleanup)

    result = await plugin.cleanup_pool_quarantine(  # type: ignore[attr-defined]
        {"migration_generation": "generation-1"}
    )

    assert result["status"] == "CLEANED"
    assert calls == [
        {
            "engine": engine,
            "home": Path("/home/admin"),
            "migration_generation": "generation-1",
        }
    ]

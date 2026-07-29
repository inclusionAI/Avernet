from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from engine.community.plugins.claude_code.layout_pool import (
    LAYOUT_CONTRACT_VERSION,
    MappingPublishResult,
    MappingSourceLayout,
    MappingVerificationResult,
    PoolActivationResult,
    PoolActivationStatus,
    RuntimeLayoutInspection,
    RuntimeLayoutInspectionStatus,
    SkillMapping,
    activate_claude_code_pool,
    inspect_claude_code_runtime_layout,
    publish_claude_code_pool_mappings,
    verify_claude_code_pool_mappings,
)
from engine.community.plugins.claude_code.plugin_impl import ClaudeCodePluginImpl

PREPARATION_ID = "2a958f59-8cf4-4413-a267-7d56d3382f23"


def _prepared_home(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, Path, Path]:
    home = tmp_path / "home" / "admin"
    legacy_local = home / ".claude_code" / "workspace" / "skills" / "skills-local"
    active_root = home / ".claude" / "skills"
    local_bridge = active_root / "skills-local"
    repo_bridge = active_root / "skills-repo"
    pool_root = home / ".claude_code" / "workspace" / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_repo = pool_root / "skills-repo"

    (legacy_local / "handmade").mkdir(parents=True)
    (legacy_local / "handmade" / "SKILL.md").write_text("latest")
    (pool_local / "handmade").mkdir(parents=True)
    (pool_local / "handmade" / "SKILL.md").write_text("prepared")
    (pool_repo / "business" / "shared").mkdir(parents=True)
    (pool_repo / "business" / "shared" / "SKILL.md").write_text("repo")
    active_root.mkdir(parents=True, exist_ok=True)
    local_bridge.symlink_to(legacy_local, target_is_directory=True)
    repo_bridge.symlink_to(pool_repo, target_is_directory=True)

    marker = {
        "engine": "claude_code",
        "layout_contract_version": "skills-pool-p3-v1",
        "preparation_id": PREPARATION_ID,
        "prepared_at": "2026-07-23T06:00:00Z",
        "pool_local_root": str(pool_local),
        "pool_repo_root": str(pool_repo),
        "validation_summary": {
            "all_valid": True,
            "pool_local": {"path": str(pool_local), "valid": True},
            "pool_repo": {
                "path": str(pool_repo),
                "readable_mount": True,
                "valid": True,
            },
            "structural_bridges": [
                {
                    "name": "stable_local_bridge",
                    "path": str(local_bridge),
                    "target": str(legacy_local),
                    "valid": True,
                },
                {
                    "name": "stable_repo_bridge",
                    "path": str(repo_bridge),
                    "target": str(pool_repo),
                    "valid": True,
                },
            ],
            "managed_active_entries": [],
            "external_active_entry_count": 0,
        },
    }
    (pool_root / ".pool-ready").write_text(json.dumps(marker))
    return (
        home,
        legacy_local,
        local_bridge,
        repo_bridge,
        pool_local,
        pool_repo,
    )


def test_claude_code_probe_requires_both_stable_bridges(tmp_path: Path) -> None:
    home, _, _, repo_bridge, _, pool_repo = _prepared_home(tmp_path)

    ready = inspect_claude_code_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert ready.status is RuntimeLayoutInspectionStatus.READY
    assert ready.engine == "claude_code"
    assert ready.preparation_id == PREPARATION_ID
    assert ready.evidence["checks"]["stable_local_bridge_valid"] is True
    assert ready.evidence["checks"]["stable_repo_bridge_valid"] is True

    repo_bridge.unlink()
    repo_bridge.symlink_to(home / "wrong", target_is_directory=True)
    invalid = inspect_claude_code_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert invalid.status is RuntimeLayoutInspectionStatus.INVALID
    assert invalid.evidence["reason"] == "stable_repo_bridge_invalid"


def test_claude_code_probe_rejects_non_file_marker(tmp_path: Path) -> None:
    home = tmp_path / "home" / "admin"
    marker = (
        home
        / ".claude_code"
        / "workspace"
        / "skills-pool"
        / ".pool-ready"
    )
    marker.mkdir(parents=True)

    result = inspect_claude_code_runtime_layout(home=home)

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "marker_not_regular_file"


def test_claude_code_activation_retires_physical_legacy_local(
    tmp_path: Path,
) -> None:
    (
        home,
        legacy_local,
        local_bridge,
        repo_bridge,
        pool_local,
        pool_repo,
    ) = _prepared_home(tmp_path)
    mappings = [
        SkillMapping(
            source=str(pool_local / "handmade"),
            target=str(local_bridge.parent / "handmade"),
        ),
        SkillMapping(
            source=str(pool_repo / "business" / "shared"),
            target=str(repo_bridge.parent / "shared"),
        ),
    ]

    result = activate_claude_code_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=mappings,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert not legacy_local.exists()
    assert not legacy_local.is_symlink()
    assert not local_bridge.exists()
    assert not local_bridge.is_symlink()
    assert not repo_bridge.exists()
    assert not repo_bridge.is_symlink()
    assert (pool_local / "handmade" / "SKILL.md").read_text() == "latest"


def test_claude_code_publishes_and_verifies_pool_mappings(tmp_path: Path) -> None:
    home, _, local_bridge, _, pool_local, _ = _prepared_home(tmp_path)
    target = local_bridge.parent / "handmade"
    source = pool_local / "handmade"
    mapping = SkillMapping(source=str(source), target=str(target))

    published = publish_claude_code_pool_mappings(
        mappings=[mapping],
        home=home,
    )
    verified = verify_claude_code_pool_mappings(
        mappings=[mapping],
        home=home,
    )

    assert published.published is True
    assert target.is_symlink()
    assert target.resolve() == source.resolve()
    assert verified.valid is True
    assert verified.evidence["managed_checked"] == 1

    target.unlink()
    other_source = pool_local / "other"
    other_source.mkdir()
    target.symlink_to(other_source, target_is_directory=True)

    mismatch = verify_claude_code_pool_mappings(
        mappings=[mapping],
        home=home,
    )

    assert mismatch.valid is False
    assert mismatch.evidence["failures"][0]["reason"] == "managed_source_conflict"


@pytest.mark.asyncio
async def test_claude_code_port_runs_pool_filesystem_operations_off_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.community.plugins.claude_code import _skills

    loop_thread = threading.get_ident()
    worker_threads: list[int] = []
    received: list[dict[str, object]] = []

    def record(result: object, kwargs: dict[str, object]) -> object:
        worker_threads.append(threading.get_ident())
        received.append(kwargs)
        return result

    monkeypatch.setattr(
        _skills,
        "activate_claude_code_pool",
        lambda **kwargs: record(
            PoolActivationResult(
                PoolActivationStatus.COMMITTED,
                {"bridge": "valid"},
            ),
            kwargs,
        ),
    )
    monkeypatch.setattr(
        _skills,
        "inspect_claude_code_runtime_layout",
        lambda **kwargs: record(
            RuntimeLayoutInspection(
                status=RuntimeLayoutInspectionStatus.READY,
                engine="claude_code",
                layout_contract_version=LAYOUT_CONTRACT_VERSION,
                preparation_id=PREPARATION_ID,
                evidence={},
            ),
            kwargs,
        ),
    )
    monkeypatch.setattr(
        _skills,
        "publish_claude_code_pool_mappings",
        lambda **kwargs: record(
            MappingPublishResult(True, {"total": 1}),
            kwargs,
        ),
    )
    monkeypatch.setattr(
        _skills,
        "verify_claude_code_pool_mappings",
        lambda **kwargs: record(
            MappingVerificationResult(True, {"checked": 1}),
            kwargs,
        ),
    )
    port = ClaudeCodePluginImpl()
    params = {
        "migration_generation": "generation-1",
        "preparation_id": PREPARATION_ID,
        "registered_local_names": ["handmade"],
        "mappings": [{"source": "/pool/handmade", "target": "/skills/handmade"}],
    }

    assert (await port.activate_pool_layout(params))["committed"] is True
    assert (
        await port.probe_pool_layout(
            {"layout_contract_version": LAYOUT_CONTRACT_VERSION}
        )
    )["status"] == "READY"
    assert (await port.publish_pool_mappings(params))["published"] is True
    assert (await port.verify_pool_mappings(params))["valid"] is True
    assert len(worker_threads) == 4
    assert all(worker_thread != loop_thread for worker_thread in worker_threads)
    assert received[0] == {
        "migration_generation": "generation-1",
        "preparation_id": PREPARATION_ID,
        "registered_local_names": ["handmade"],
        "mappings": [
            SkillMapping(source="/pool/handmade", target="/skills/handmade")
        ],
    }
    assert received[1] == {
        "expected_contract_version": LAYOUT_CONTRACT_VERSION,
    }
    assert received[2] == received[3] == {
        "mappings": [
            SkillMapping(source="/pool/handmade", target="/skills/handmade")
        ],
        "source_layout": MappingSourceLayout.POOL,
    }

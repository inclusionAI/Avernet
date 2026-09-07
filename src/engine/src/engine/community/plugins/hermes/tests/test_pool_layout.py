from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.community.core.skills.layout_planner import (
    MAPPING_CONTRACT_VERSION,
)
from engine.community.plugins.hermes.layout_pool import (
    PoolActivationStatus,
    RuntimeLayoutInspectionStatus,
    SkillMapping,
    activate_hermes_pool,
    inspect_hermes_runtime_layout,
    publish_hermes_pool_mappings,
    rollback_hermes_pool,
    verify_hermes_pool_mappings,
)

PREPARATION_ID = "b7f7a125-9133-45fd-956d-fb66da81f68d"


def _pool_active_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home" / "admin"
    active_root = home / ".hermes" / "skills"
    pool_root = home / ".hermes" / "workspace" / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_repo = pool_root / "skills-repo"
    repo_bridge = home / ".hermes" / "skills-repo"
    pool_local.mkdir(parents=True)
    pool_repo.mkdir(parents=True)
    active_root.mkdir(parents=True)
    (pool_root / ".pool-ready").write_text(
        json.dumps(
            {
                "engine": "hermes",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": PREPARATION_ID,
                "prepared_at": "2026-07-24T00:00:00Z",
                "pool_local_root": str(pool_local),
                "pool_repo_root": str(pool_repo),
                "validation_summary": {
                    "all_valid": True,
                    "legacy_bridge_verified": True,
                    "pool_local": {"path": str(pool_local), "valid": True},
                    "pool_repo": {
                        "path": str(pool_repo),
                        "readable_mount": True,
                        "valid": True,
                    },
                    "managed_active_entries": [],
                    "structural_bridges": [
                        {
                            "name": "stable_local_bridge",
                            "path": str(active_root / "skills-local"),
                            "target": str(
                                home / ".hermes/workspace/skills/skills-local"
                            ),
                            "valid": True,
                        },
                        {
                            "name": "stable_repo_bridge",
                            "path": str(repo_bridge),
                            "target": str(pool_repo),
                            "valid": True,
                        },
                    ],
                },
            }
        )
    )
    (pool_root / ".pool-active").write_text(
        json.dumps(
            {
                "engine": "hermes",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": PREPARATION_ID,
                "migration_generation": "generation-1",
                "activation_state": "active",
                "mappings": [],
            }
        )
    )
    return home, active_root, pool_repo, repo_bridge


def test_probe_requires_verified_hermes_legacy_bridge(tmp_path: Path) -> None:
    home = tmp_path / "home" / "admin"
    active_root = home / ".hermes" / "skills"
    legacy_local = home / ".hermes" / "workspace" / "skills" / "skills-local"
    pool_root = home / ".hermes" / "workspace" / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_repo = pool_root / "skills-repo"
    local_bridge = active_root / "skills-local"
    repo_bridge = home / ".hermes" / "skills-repo"

    legacy_local.mkdir(parents=True)
    pool_local.mkdir(parents=True)
    pool_repo.mkdir(parents=True)
    active_root.mkdir(parents=True)
    local_bridge.symlink_to(legacy_local, target_is_directory=True)
    repo_bridge.symlink_to(pool_repo, target_is_directory=True)
    (pool_root / ".pool-ready").write_text(
        json.dumps(
            {
                "engine": "hermes",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": "b7f7a125-9133-45fd-956d-fb66da81f68d",
                "prepared_at": "2026-07-24T00:00:00Z",
                "pool_local_root": str(pool_local),
                "pool_repo_root": str(pool_repo),
                "validation_summary": {
                    "all_valid": True,
                    "legacy_bridge_verified": True,
                    "legacy_bridge_repaired": False,
                    "pool_local": {"path": str(pool_local), "valid": True},
                    "pool_repo": {
                        "path": str(pool_repo),
                        "readable_mount": True,
                        "valid": True,
                    },
                    "managed_active_entries": [],
                    "external_active_entry_count": 0,
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
                },
            }
        )
    )

    result = inspect_hermes_runtime_layout(
        home=home,
        mapping_contract_version=MAPPING_CONTRACT_VERSION,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY
    assert result.engine == "hermes"
    assert result.evidence["checks"]["legacy_local_bridge_valid"] is True
    assert result.evidence["mapping_contract_version"] == MAPPING_CONTRACT_VERSION
    assert result.evidence["resolved_layout"]["active_root"] == str(
        home / ".hermes/skills"
    )

    legacy_consumer = inspect_hermes_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert "mapping_contract_version" not in legacy_consumer.evidence

    marker_path = pool_root / ".pool-ready"
    marker = json.loads(marker_path.read_text())
    del marker["validation_summary"]["legacy_bridge_verified"]
    marker_path.write_text(json.dumps(marker))

    missing_h0_evidence = inspect_hermes_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert missing_h0_evidence.status is RuntimeLayoutInspectionStatus.INVALID
    assert missing_h0_evidence.evidence["reason"] == "marker_contract_mismatch"


def test_activation_retires_hermes_platform_bridges_and_uses_pool_mappings(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    active_root = home / ".hermes" / "skills"
    legacy_local = home / ".hermes" / "workspace" / "skills" / "skills-local"
    pool_root = home / ".hermes" / "workspace" / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_repo = pool_root / "skills-repo"
    local_bridge = active_root / "skills-local"
    repo_bridge = home / ".hermes" / "skills-repo"

    (legacy_local / "handmade").mkdir(parents=True)
    (legacy_local / "handmade" / "SKILL.md").write_text("latest")
    (pool_local / "handmade").mkdir(parents=True)
    (pool_local / "handmade" / "SKILL.md").write_text("prepared")
    pool_repo.mkdir(parents=True)
    active_root.mkdir(parents=True)
    local_bridge.symlink_to(legacy_local, target_is_directory=True)
    repo_bridge.symlink_to(pool_repo, target_is_directory=True)
    (pool_root / ".pool-ready").write_text(
        json.dumps(
            {
                "engine": "hermes",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": "b7f7a125-9133-45fd-956d-fb66da81f68d",
                "prepared_at": "2026-07-24T00:00:00Z",
                "pool_local_root": str(pool_local),
                "pool_repo_root": str(pool_repo),
                "validation_summary": {
                    "all_valid": True,
                    "legacy_bridge_verified": True,
                    "legacy_bridge_repaired": False,
                    "pool_local": {"path": str(pool_local), "valid": True},
                    "pool_repo": {
                        "path": str(pool_repo),
                        "readable_mount": True,
                        "valid": True,
                    },
                    "managed_active_entries": [],
                    "external_active_entry_count": 0,
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
                },
            }
        )
    )

    result = activate_hermes_pool(
        migration_generation="generation-1",
        preparation_id="b7f7a125-9133-45fd-956d-fb66da81f68d",
        registered_local_names=["handmade"],
        mappings=[
            SkillMapping(
                source=str(pool_local / "handmade"),
                target=str(active_root / "handmade"),
            )
        ],
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

    ready = inspect_hermes_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert ready.status is RuntimeLayoutInspectionStatus.READY
    assert ready.evidence["checks"]["legacy_repo_bridge_status"] == "absent"

    repeated = activate_hermes_pool(
        migration_generation="generation-1",
        preparation_id="b7f7a125-9133-45fd-956d-fb66da81f68d",
        registered_local_names=["handmade"],
        mappings=[
            SkillMapping(
                source=str(pool_local / "handmade"),
                target=str(active_root / "handmade"),
            )
        ],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert repeated.status is PoolActivationStatus.ALREADY_COMMITTED

    mapping = SkillMapping(
        source=str(pool_local / "handmade"),
        target=str(active_root / "handmade"),
    )
    published = publish_hermes_pool_mappings(mappings=[mapping], home=home)
    verified = verify_hermes_pool_mappings(mappings=[mapping], home=home)

    assert published.published is True
    assert verified.valid is True
    assert (active_root / "handmade").resolve() == (pool_local / "handmade")

    (pool_local / "handmade" / "SKILL.md").write_text("pool-write")
    rolled_back = rollback_hermes_pool(
        rollback_generation="rollback-1",
        registered_local_names=["handmade"],
        home=home,
    )

    assert rolled_back.status is PoolActivationStatus.COMMITTED
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()
    assert (legacy_local / "handmade" / "SKILL.md").read_text() == "pool-write"
    assert local_bridge.resolve() == legacy_local.resolve()
    assert repo_bridge.is_symlink()
    assert repo_bridge.resolve() == pool_repo.resolve()


def test_pool_active_probe_is_read_only_for_unexpected_hermes_repo_path(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    active_root = home / ".hermes" / "skills"
    pool_root = home / ".hermes" / "workspace" / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_repo = pool_root / "skills-repo"
    repo_bridge = home / ".hermes" / "skills-repo"
    pool_local.mkdir(parents=True)
    pool_repo.mkdir(parents=True)
    active_root.mkdir(parents=True)
    repo_bridge.mkdir(parents=True)
    (repo_bridge / "user-data").write_text("preserve")
    preparation_id = "b7f7a125-9133-45fd-956d-fb66da81f68d"
    (pool_root / ".pool-ready").write_text(
        json.dumps(
            {
                "engine": "hermes",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": preparation_id,
                "prepared_at": "2026-07-24T00:00:00Z",
                "pool_local_root": str(pool_local),
                "pool_repo_root": str(pool_repo),
                "validation_summary": {
                    "all_valid": True,
                    "legacy_bridge_verified": True,
                    "pool_local": {"path": str(pool_local), "valid": True},
                    "pool_repo": {
                        "path": str(pool_repo),
                        "readable_mount": True,
                        "valid": True,
                    },
                    "managed_active_entries": [],
                    "structural_bridges": [
                        {
                            "name": "stable_local_bridge",
                            "path": str(active_root / "skills-local"),
                            "target": str(
                                home / ".hermes/workspace/skills/skills-local"
                            ),
                            "valid": True,
                        },
                        {
                            "name": "stable_repo_bridge",
                            "path": str(repo_bridge),
                            "target": str(pool_repo),
                            "valid": True,
                        },
                    ],
                },
            }
        )
    )
    (pool_root / ".pool-active").write_text(
        json.dumps(
            {
                "engine": "hermes",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": preparation_id,
                "migration_generation": "generation-1",
                "activation_state": "active",
                "mappings": [],
            }
        )
    )

    result = inspect_hermes_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY
    assert result.evidence["checks"]["legacy_repo_bridge_status"] == "retained_object"
    assert (repo_bridge / "user-data").read_text() == "preserve"


def test_pool_active_probe_diagnoses_expected_and_unexpected_hermes_links(
    tmp_path: Path,
) -> None:
    home, _active_root, pool_repo, repo_bridge = _pool_active_fixture(tmp_path)
    relative_target = repo_bridge.parent / "workspace/skills-pool/skills-repo"
    repo_bridge.symlink_to(
        relative_target.relative_to(repo_bridge.parent),
        target_is_directory=True,
    )
    expected = inspect_hermes_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert expected.status is RuntimeLayoutInspectionStatus.READY
    assert (
        expected.evidence["checks"]["legacy_repo_bridge_status"]
        == "expected_bridge_present"
    )
    assert repo_bridge.is_symlink()

    repo_bridge.unlink()
    unexpected = home / "unexpected-repo"
    unexpected.mkdir()
    repo_bridge.symlink_to(unexpected, target_is_directory=True)
    retained = inspect_hermes_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert retained.status is RuntimeLayoutInspectionStatus.READY
    assert (
        retained.evidence["checks"]["legacy_repo_bridge_status"]
        == "retained_unexpected_symlink"
    )
    assert repo_bridge.is_symlink()


def test_pool_active_probe_treats_unreadable_hermes_repo_link_as_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home, _active_root, pool_repo, repo_bridge = _pool_active_fixture(tmp_path)
    repo_bridge.symlink_to(pool_repo, target_is_directory=True)
    original_readlink = Path.readlink

    def fail_repo_readlink(path):
        if path == repo_bridge:
            raise OSError("unreadable link")
        return original_readlink(path)

    monkeypatch.setattr(Path, "readlink", fail_repo_readlink)

    result = inspect_hermes_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY
    assert (
        result.evidence["checks"]["legacy_repo_bridge_status"] == "retained_unreadable"
    )


def test_pool_active_reconciliation_preserves_unowned_hermes_repo_entries(
    tmp_path: Path,
    caplog,
) -> None:
    home, _active_root, pool_repo, repo_bridge = _pool_active_fixture(tmp_path)
    repo_bridge.mkdir()
    (repo_bridge / "user-data").write_text("preserve")

    result = activate_hermes_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.ALREADY_COMMITTED
    assert (repo_bridge / "user-data").read_text() == "preserve"
    assert "retained unowned Legacy repo object" in caplog.text


def test_pool_active_reconciliation_preserves_unexpected_hermes_repo_link(
    tmp_path: Path,
    caplog,
) -> None:
    home, _active_root, pool_repo, repo_bridge = _pool_active_fixture(tmp_path)
    unexpected = home / "unexpected-repo"
    unexpected.mkdir()
    repo_bridge.symlink_to(unexpected, target_is_directory=True)

    result = activate_hermes_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.ALREADY_COMMITTED
    assert repo_bridge.resolve() == unexpected
    assert "retained unexpected Legacy repo symlink" in caplog.text


def test_pool_active_reconciliation_retries_hermes_repo_retirement_failure(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    home, _active_root, pool_repo, repo_bridge = _pool_active_fixture(tmp_path)
    repo_bridge.symlink_to(pool_repo, target_is_directory=True)
    original_unlink = Path.unlink

    def fail_repo_unlink(path, *args, **kwargs):
        if path == repo_bridge:
            raise OSError("read-only filesystem")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_repo_unlink)

    result = activate_hermes_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.ALREADY_COMMITTED
    assert repo_bridge.is_symlink()
    assert "could not retire obsolete repo bridge" in caplog.text


@pytest.mark.parametrize(
    ("method_name", "diagnostic"),
    [
        ("lstat", "could not inspect obsolete repo bridge"),
        ("readlink", "retained unreadable Legacy repo symlink"),
    ],
)
def test_pool_active_reconciliation_tolerates_unreadable_hermes_repo_entry(
    tmp_path: Path,
    monkeypatch,
    caplog,
    method_name,
    diagnostic,
) -> None:
    home, _active_root, pool_repo, repo_bridge = _pool_active_fixture(tmp_path)
    repo_bridge.symlink_to(pool_repo, target_is_directory=True)
    original = getattr(Path, method_name)

    def fail_repo_inspection(path, *args, **kwargs):
        if path == repo_bridge:
            raise OSError("unreadable entry")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, method_name, fail_repo_inspection)

    result = activate_hermes_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.ALREADY_COMMITTED
    assert diagnostic in caplog.text


def test_mapping_rejects_openclaw_source_instead_of_falling_back(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    active_root = home / ".hermes" / "skills"
    openclaw_source = (
        home
        / ".openclaw"
        / "workspace"
        / "skills-pool"
        / "skills-local"
        / "wrong-engine"
    )
    openclaw_source.mkdir(parents=True)
    active_root.mkdir(parents=True)

    result = publish_hermes_pool_mappings(
        mappings=[
            SkillMapping(
                source=str(openclaw_source),
                target=str(active_root / "wrong-engine"),
            )
        ],
        home=home,
    )

    assert result.published is False
    assert result.evidence["reason"] == "mapping_invalid"
    assert result.evidence["failures"][0]["reason"] == "source_outside_pool"
    assert not (active_root / "wrong-engine").exists()


def test_mapping_cannot_replace_hermes_permanent_local_bridge(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    active_root = home / ".hermes" / "skills"
    legacy_local = home / ".hermes" / "workspace" / "skills" / "skills-local"
    pool_local = home / ".hermes" / "workspace" / "skills-pool" / "skills-local"
    source = pool_local / "skills-local"
    source.mkdir(parents=True)
    legacy_local.mkdir(parents=True)
    active_root.mkdir(parents=True)
    local_bridge = active_root / "skills-local"
    local_bridge.symlink_to(legacy_local, target_is_directory=True)

    published = publish_hermes_pool_mappings(
        mappings=[
            SkillMapping(source=str(source), target=str(local_bridge)),
        ],
        home=home,
    )
    verified = verify_hermes_pool_mappings(
        mappings=[
            SkillMapping(source=str(source), target=str(local_bridge)),
        ],
        home=home,
    )

    assert published.published is False
    assert published.evidence["failures"][0]["reason"] == "target_invalid"
    assert verified.valid is False
    assert verified.evidence["failures"][0]["reason"] == "target_invalid"
    assert local_bridge.readlink() == legacy_local

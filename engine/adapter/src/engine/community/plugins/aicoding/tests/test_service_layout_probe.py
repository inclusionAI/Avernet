from pathlib import Path

import pytest
from engine.community.plugins.aicoding.layout_pool import inspect_aicoding_runtime_layout


@pytest.mark.parametrize("center_mounted", [True, False])
def test_aicoding_legacy_evidence_is_only_available_for_service_build(
    tmp_path: Path, center_mounted: bool
) -> None:
    active = tmp_path / ".claude" / "skills"
    active.mkdir(parents=True)
    repo = tmp_path / ".aicoding" / "skills-repo"
    repo.mkdir(parents=True)
    center = tmp_path / ".aicoding" / "workspace" / "skills-pool" / "skill-center"
    center.mkdir(parents=True)

    def is_mounted(path: Path) -> bool:
        return center_mounted and path == center

    probe = inspect_aicoding_runtime_layout(home=tmp_path, repo_is_mounted=is_mounted)
    assert probe.evidence["reason"] == "pool_ready_marker_absent"

    build_probe = inspect_aicoding_runtime_layout(
        home=tmp_path, repo_is_mounted=is_mounted, for_service_build=True
    )
    assert build_probe.status.value == "READY"
    assert build_probe.preparation_id is None
    assert build_probe.evidence["source_layout"] == "legacy"
    assert build_probe.evidence["resolved_layout"]["active_root"] == str(active)
    assert build_probe.evidence["resolved_layout"]["repo_root"] == str(repo)
    assert (
        "skills-pool-mapping-v3" in build_probe.evidence["supported_mapping_contract_versions"]
    ) is center_mounted
    assert not (center.parent / ".pool-ready").exists()

    (center.parent / ".pool-active").touch()
    conflict = inspect_aicoding_runtime_layout(
        home=tmp_path, repo_is_mounted=is_mounted, for_service_build=True
    )
    assert conflict.status.value != "READY"

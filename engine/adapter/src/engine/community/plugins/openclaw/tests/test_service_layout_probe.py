from pathlib import Path
from unittest.mock import patch

import pytest

from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutContext,
)
from engine.community.plugins.openclaw._skills import _SkillsPortMixin


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_is_symlink", [False, True])
@pytest.mark.parametrize(
    ("mounted", "mapping_versions"),
    [
        (True, ["skills-pool-mapping-v2", "skills-pool-mapping-v3"]),
        (False, ["skills-pool-mapping-v2"]),
    ],
)
async def test_fresh_legacy_layout_is_ready_for_service_build_only(
    tmp_path: Path, mounted: bool, mapping_versions: list[str], repo_is_symlink: bool
) -> None:
    workspace = tmp_path / ".openclaw" / "workspace"
    active = workspace / "skills"
    active.mkdir(parents=True)
    repo = active / "skills-repo"
    if repo_is_symlink:
        shared_repo = tmp_path / "shared-repo"
        shared_repo.mkdir()
        repo.symlink_to(shared_repo, target_is_directory=True)
    else:
        repo.mkdir()
    center = workspace / "skills-pool" / "skill-center"
    center.mkdir(parents=True)

    class Port(_SkillsPortMixin):
        _skills_layout_context = RuntimeLayoutContext(home=tmp_path)

        def _skills_center_is_mounted(self, path: Path) -> bool:
            return mounted and path == center

    port = Port()
    request = {"layout_contract_version": LAYOUT_CONTRACT_VERSION}
    pool_probe = await port.probe_pool_layout(request)
    assert pool_probe["status"] == "NOT_CAPABLE"
    assert pool_probe["evidence"]["reason"] == "pool_ready_marker_absent"

    build_probe = await port.probe_pool_layout({**request, "for_service_build": True})
    assert build_probe["status"] == "READY"
    assert build_probe["preparation_id"] is None
    assert build_probe["evidence"]["source_layout"] == "legacy"
    assert build_probe["evidence"]["resolved_layout"]["repo_root"] == str(
        active / "skills-repo"
    )
    assert build_probe["evidence"]["supported_mapping_contract_versions"] == (
        mapping_versions
    )
    assert not (center.parent / ".pool-ready").exists()

    # A transition marker precludes using evidence for a stable Legacy draft.
    (center.parent / ".pool-active").write_text('{"activation_state":"finalizing"}')
    conflict = await port.probe_pool_layout({**request, "for_service_build": True})
    assert conflict["status"] != "READY"


@pytest.mark.asyncio
@pytest.mark.parametrize("target_kind", ["missing", "file"])
async def test_service_layout_probe_rejects_invalid_legacy_repo_symlink(
    tmp_path: Path, target_kind: str
) -> None:
    workspace = tmp_path / ".openclaw" / "workspace"
    active = workspace / "skills"
    active.mkdir(parents=True)
    target = tmp_path / "shared-repo"
    if target_kind == "file":
        target.touch()
    (active / "skills-repo").symlink_to(target)
    center = workspace / "skills-pool" / "skill-center"
    center.mkdir(parents=True)

    class Port(_SkillsPortMixin):
        _skills_layout_context = RuntimeLayoutContext(home=tmp_path)

        def _skills_center_is_mounted(self, path: Path) -> bool:
            return path == center

    build_probe = await Port().probe_pool_layout(
        {"layout_contract_version": LAYOUT_CONTRACT_VERSION, "for_service_build": True}
    )
    assert build_probe["status"] == "NOT_CAPABLE"
    assert build_probe["evidence"]["reason"] == "pool_ready_marker_absent"


@pytest.mark.asyncio
async def test_service_layout_probe_rejects_unreadable_legacy_repo(tmp_path: Path) -> None:
    repo = tmp_path / ".openclaw" / "workspace" / "skills" / "skills-repo"
    repo.mkdir(parents=True)

    class Port(_SkillsPortMixin):
        _skills_layout_context = RuntimeLayoutContext(home=tmp_path)

    with patch(
        "engine.community.plugins.openclaw.service_layout_probe.os.scandir",
        side_effect=PermissionError,
    ) as scandir:
        build_probe = await Port().probe_pool_layout(
            {"layout_contract_version": LAYOUT_CONTRACT_VERSION, "for_service_build": True}
        )

    scandir.assert_called_once_with(repo)
    assert build_probe["status"] == "NOT_CAPABLE"

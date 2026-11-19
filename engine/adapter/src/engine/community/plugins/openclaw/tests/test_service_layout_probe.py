from pathlib import Path

import pytest

from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutContext,
)
from engine.community.plugins.openclaw._skills import _SkillsPortMixin


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mounted", "mapping_versions"),
    [
        (True, ["skills-pool-mapping-v2", "skills-pool-mapping-v3"]),
        (False, ["skills-pool-mapping-v2"]),
    ],
)
async def test_fresh_legacy_layout_is_ready_for_service_build_only(
    tmp_path: Path, mounted: bool, mapping_versions: list[str]
) -> None:
    workspace = tmp_path / ".openclaw" / "workspace"
    active = workspace / "skills"
    (active / "skills-repo").mkdir(parents=True)
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

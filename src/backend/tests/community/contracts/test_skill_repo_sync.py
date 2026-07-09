"""Rule 25 conformance — SkillRepoSyncPlugin.

Consumer under test: ``SkillService`` wires the plugin via DI and
calls ``self.skill_repo_sync.sync()`` / ``.get_scan_target()`` /
``.get_local_skills_root()`` from the market scanning paths.
Exercising ``SkillService`` end-to-end requires the full skill
repository graph; the contract is verified through the DI-bound
instance the service receives.

Plugin-hit assertion: the local ``LocalSkillRepoSyncPlugin.sync()``
simulates a real fetch — ``fetch`` reflects whether the local
``skills-repo`` source dir has content, while staying offline-friendly
(``success`` always True, exceptions captured in ``error``). This
source-driven ``fetch`` boolean is only producible by the local impl.
"""
from __future__ import annotations

import pytest

from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin


@pytest.mark.asyncio
async def test_local_skill_repo_sync_no_source_is_offline_friendly(
    world, tmp_path, monkeypatch
) -> None:
    """When the local skills-repo source is absent/empty, the local impl
    reports ``fetch=False`` but stays a success with no error — offline
    development must never fail on a missing source."""
    plugin = world.get(SkillRepoSyncPlugin)
    # Point the root at an empty dir: no ``skills-repo`` subtree exists.
    monkeypatch.setattr(plugin, "get_local_skills_root", lambda: tmp_path)
    result = await plugin.sync()
    assert result["success"] is True
    assert result["fetch"] is False
    assert result["error"] is None


@pytest.mark.asyncio
async def test_local_skill_repo_sync_with_source_reports_fetch(
    world, tmp_path, monkeypatch
) -> None:
    """When the local skills-repo source has content, the local impl
    simulates a real fetch: ``fetch=True`` with the discovered subtree."""
    repo_dir = tmp_path / "skills-repo"
    skill_dir = repo_dir / "data-init"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# data-init\n")

    plugin = world.get(SkillRepoSyncPlugin)
    monkeypatch.setattr(plugin, "get_local_skills_root", lambda: tmp_path)
    result = await plugin.sync()
    assert result["success"] is True
    assert result["fetch"] is True
    assert result["error"] is None
    assert result["subtrees"]["skills"]["entries"] == ["data-init"]


def test_local_skills_root_is_under_home(world) -> None:
    plugin = world.get(SkillRepoSyncPlugin)
    root = plugin.get_local_skills_root()
    assert root is not None
    # Local impl returns ~/.openclaw/workspace/skills — fingerprint.
    assert ".openclaw" in str(root)
    assert root.name == "skills"

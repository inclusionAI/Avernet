"""Unit tests for the community ``CommunitySkillRepoSync`` (B7).

Real local-directory skills source (no MockSeam). ``sync()`` reports on the
host-side skills-repo dir; path helpers honor ``AGENTCLAW_SKILLS_ROOT``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from agentclaw.community.plugins.community.skill_repo_sync import CommunitySkillRepoSync


def test_skills_root_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTCLAW_SKILLS_ROOT", str(tmp_path / "skills"))
    plugin = CommunitySkillRepoSync()
    assert plugin.get_local_skills_root() == tmp_path / "skills"


def test_skills_root_default(monkeypatch):
    monkeypatch.delenv("AGENTCLAW_SKILLS_ROOT", raising=False)
    plugin = CommunitySkillRepoSync()
    assert plugin.get_local_skills_root() == (
        Path.home() / ".openclaw" / "workspace" / "skills"
    )


def test_sync_reports_no_fetch_when_repo_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTCLAW_SKILLS_ROOT", str(tmp_path / "skills"))
    plugin = CommunitySkillRepoSync()
    result = asyncio.run(plugin.sync())
    assert result == {"success": True, "fetch": False, "subtrees": {}, "error": None}


def test_sync_fetches_when_repo_has_skills(monkeypatch, tmp_path):
    root = tmp_path / "skills"
    repo = root / "skills-repo"
    (repo / "alpha").mkdir(parents=True)
    (repo / "beta").mkdir(parents=True)
    monkeypatch.setenv("AGENTCLAW_SKILLS_ROOT", str(root))

    plugin = CommunitySkillRepoSync()
    result = asyncio.run(plugin.sync())
    assert result["success"] is True
    assert result["fetch"] is True
    assert result["subtrees"]["skills"]["entries"] == ["alpha", "beta"]


def test_get_scan_target_returns_fallback(tmp_path):
    plugin = CommunitySkillRepoSync()
    fallback = tmp_path / "market-repo"
    assert plugin.get_scan_target(fallback) == fallback


def test_data_init_path_prefers_env(monkeypatch, tmp_path):
    md = tmp_path / "SKILL.md"
    md.write_text("# data-init")
    monkeypatch.setenv("DATA_INIT_SKILL_MD_PATH", str(md))
    plugin = CommunitySkillRepoSync()
    assert plugin.get_data_init_skill_md_path() == str(md)


def test_data_init_path_falls_back_to_candidate(monkeypatch, tmp_path):
    # Env unset (and no env file) ⇒ a conventional path under the skills root.
    monkeypatch.delenv("DATA_INIT_SKILL_MD_PATH", raising=False)
    root = tmp_path / "skills"
    monkeypatch.setenv("AGENTCLAW_SKILLS_ROOT", str(root))
    plugin = CommunitySkillRepoSync()
    expected = root / "skills-repo" / "infra" / "data-init" / "SKILL.md"
    assert plugin.get_data_init_skill_md_path() == str(expected)


def test_sync_captures_oserror(monkeypatch, tmp_path):
    # An OSError while walking the repo dir is captured, not raised:
    # success stays True, fetch False, and the error is reported.
    root = tmp_path / "skills"
    (root / "skills-repo").mkdir(parents=True)
    monkeypatch.setenv("AGENTCLAW_SKILLS_ROOT", str(root))

    def _boom(self):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "iterdir", _boom)
    plugin = CommunitySkillRepoSync()
    result = asyncio.run(plugin.sync())
    assert result["success"] is True
    assert result["fetch"] is False
    assert result["subtrees"] == {}
    assert "permission denied" in result["error"]

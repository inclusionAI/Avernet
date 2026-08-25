"""Tests for the local test-runtime skill symlink synchronizer."""
from __future__ import annotations

from agentclaw.community.plugins.local.skill_symlink_sync import (
    LocalSkillSymlinkSynchronizer,
)


def test_sync_creates_desired_link_and_removes_stale_link(tmp_path, monkeypatch) -> None:
    skills_dir = tmp_path / "skills"
    repo_dir = skills_dir / "skills-repo"
    repo_dir.mkdir(parents=True)
    source = repo_dir / "demo"
    source.mkdir()
    stale = skills_dir / "stale"
    stale.symlink_to(source)

    synchronizer = LocalSkillSymlinkSynchronizer(skills_dir)
    monkeypatch.setattr(synchronizer, "_find_repo_source", lambda: None)

    result = synchronizer.sync([
        {
            "source": "/home/admin/.openclaw/workspace/skills/skills-repo/demo",
            "target": "/home/admin/.openclaw/workspace/skills/demo",
        }
    ])

    assert result == {
        "success": True,
        "message": "local sync done: created=1, removed=1, skipped=0",
        "created": 1,
        "removed": 1,
        "skipped": 0,
    }
    assert (skills_dir / "demo").is_symlink()
    assert not stale.exists()


def test_sync_rejects_target_outside_skills_directory(tmp_path, monkeypatch) -> None:
    skills_dir = tmp_path / "skills"
    source = skills_dir / "skills-repo" / "demo"
    source.mkdir(parents=True)
    synchronizer = LocalSkillSymlinkSynchronizer(skills_dir)
    monkeypatch.setattr(synchronizer, "_find_repo_source", lambda: None)

    result = synchronizer.sync([
        {
            "source": str(source),
            "target": str(tmp_path / "outside"),
        }
    ])

    assert result["created"] == 0
    assert result["skipped"] == 1
    assert not (tmp_path / "outside").exists()

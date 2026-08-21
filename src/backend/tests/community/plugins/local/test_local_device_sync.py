"""Tests for the Core local DeviceSync service."""

from pathlib import Path

from agentclaw.community.core.devices.services.local_device_sync import (
    LocalDeviceSyncService,
)


def test_sync_bot_config_returns_local_skip_sentinel():
    service = LocalDeviceSyncService(skills_dir=None)
    result = service.sync_bot_config(
        bot_id="b1",
        binding_id=42,
        public="1",
        permission_owner="owner",
        user_id="u",
        nick_name="n",
    )
    assert result == {
        "success": False,
        "message": "local mode — device sync skipped",
    }


def test_sync_bot_config_noop_even_with_zero_binding_id():
    service = LocalDeviceSyncService(skills_dir=None)
    result = service.sync_bot_config(
        bot_id="b1",
        binding_id=0,
        public="0",
        permission_owner=None,
        user_id="u",
        nick_name="n",
    )
    assert result["success"] is False
    assert "local mode" in result["message"]


def test_sync_symlinks_creates_relative_link_and_removes_stale_link(
    tmp_path, monkeypatch
):
    skills_dir = tmp_path / "skills"
    source = skills_dir / "skills-repo" / "skill-a"
    source.mkdir(parents=True)
    stale = skills_dir / "stale"
    stale.symlink_to(source)

    service = LocalDeviceSyncService(skills_dir=skills_dir)
    monkeypatch.setattr(service, "_ensure_skills_repo", lambda: None)

    result = service.sync_symlinks(
        [
            {
                "source": "/home/admin/.openclaw/workspace/skills/skills-repo/skill-a",
                "target": "/home/admin/.openclaw/workspace/skills/skill-a",
            }
        ]
    )

    link = skills_dir / "skill-a"
    assert result["created"] == 1
    assert result["removed"] == 1
    assert link.is_symlink()
    assert link.readlink() == Path("skills-repo/skill-a")
    assert not stale.exists()


def test_sync_symlinks_skips_invalid_and_missing_sources(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    service = LocalDeviceSyncService(skills_dir=skills_dir)
    monkeypatch.setattr(service, "_ensure_skills_repo", lambda: None)

    result = service.sync_symlinks(
        [
            {"source": "", "target": "ignored"},
            {
                "source": "/home/admin/.openclaw/workspace/skills/missing",
                "target": "/home/admin/.openclaw/workspace/skills/missing-link",
            },
        ]
    )

    assert result["created"] == 0
    assert result["skipped"] == 1
    assert not (skills_dir / "missing-link").exists()


def test_sync_symlinks_uses_absolute_source_outside_skills_dir(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    external_source = tmp_path / "external-skill"
    external_source.mkdir()
    service = LocalDeviceSyncService(skills_dir=skills_dir)
    monkeypatch.setattr(service, "_ensure_skills_repo", lambda: None)

    result = service.sync_symlinks(
        [
            {
                "source": str(external_source),
                "target": "/home/admin/.openclaw/workspace/skills/external",
            }
        ]
    )

    link = skills_dir / "external"
    assert result["created"] == 1
    assert link.is_symlink()
    assert link.readlink() == external_source


def test_sync_symlinks_counts_symlink_creation_failure(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    source = skills_dir / "skills-repo" / "skill-a"
    source.mkdir(parents=True)
    service = LocalDeviceSyncService(skills_dir=skills_dir)
    monkeypatch.setattr(service, "_ensure_skills_repo", lambda: None)

    def fail_symlink(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "symlink_to", fail_symlink)
    result = service.sync_symlinks(
        [
            {
                "source": "/home/admin/.openclaw/workspace/skills/skills-repo/skill-a",
                "target": "/home/admin/.openclaw/workspace/skills/skill-a",
            }
        ]
    )

    assert result["created"] == 0
    assert result["skipped"] == 1


def test_ensure_skills_repo_handles_missing_source_and_existing_correct_link(
    tmp_path, monkeypatch
):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    service = LocalDeviceSyncService(skills_dir=skills_dir)

    monkeypatch.setattr(service, "_find_repo_source", lambda: None)
    service._ensure_skills_repo()
    target = skills_dir / "skills-repo"
    assert target.is_dir()

    target.rmdir()
    source = tmp_path / "repo-source"
    source.mkdir()
    target.symlink_to(source)
    monkeypatch.setattr(service, "_find_repo_source", lambda: source)

    service._ensure_skills_repo()
    assert target.is_symlink()
    assert target.resolve() == source.resolve()

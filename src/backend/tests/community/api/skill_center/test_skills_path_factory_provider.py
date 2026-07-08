"""Tests for WorkspacePathFactory skills dir routing by is_desktop.

Validates that the path_factory routes to BAAS engine-view paths when
is_desktop=True and to cloud/local paths when is_desktop=False.

See: docs/superpowers/plans/2026-05-19-fix-skills-path-device-provider-propagation.md
"""
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.plugins.local.skill_repo_sync import LocalSkillRepoSyncPlugin


def _factory():
    # ``get_bot_skills_{local,repo}_dir`` only reads
    # ``skill_repo_sync.get_local_skills_root()``. The local impl returns a
    # non-None root, which puts these tests' "cloud OSS-view" assertions on
    # the local-host branch (also not the BAAS engine-view path), so the
    # "must NOT be /home/admin/..." invariants still hold.
    return WorkspacePathFactory(skill_repo_sync=LocalSkillRepoSyncPlugin())


class TestWorkspacePathFactoryDesktopRouting:
    """Routing by is_desktop parameter (bot_type == "desktop")."""

    def test_baas_device_returns_engine_view_path(self):
        """Desktop bot (is_desktop=True) gets BAAS engine-view skills-local."""
        factory = _factory()
        path = factory.get_bot_skills_local_dir(
            entity_id="myentity",
            bot_id="mybot",
            engine_type="openclaw",
            entity_type="staff",
            is_desktop=True,
        )
        assert str(path) == "/home/admin/.openclaw/workspace/skills/skills-local"

    def test_non_desktop_personal_service_bot_uses_cloud_path(self):
        """Non-desktop (is_desktop=False) personal/service bot uses cloud OSS-view."""
        factory = _factory()
        path = factory.get_bot_skills_local_dir(
            entity_id="myentity",
            bot_id="mybot",
            engine_type="openclaw",
            entity_type="staff",
            is_desktop=False,
        )
        # Must NOT be the BAAS engine-view root
        assert "skills-local" in str(path)
        assert not str(path).startswith("/home/admin/.openclaw/workspace/skills/skills-local")

    def test_repo_dir_desktop_returns_engine_view(self):
        """Desktop bot (is_desktop=True) gets BAAS engine-view skills-repo."""
        factory = _factory()
        path = factory.get_bot_skills_repo_dir(
            entity_id="myentity",
            bot_id="mybot",
            engine_type="openclaw",
            entity_type="staff",
            is_desktop=True,
        )
        assert str(path) == "/home/admin/.openclaw/workspace/skills/skills-repo"

    def test_repo_dir_non_desktop_uses_cloud_path(self):
        """Non-desktop (is_desktop=False) uses cloud OSS-view for skills-repo."""
        factory = _factory()
        path = factory.get_bot_skills_repo_dir(
            entity_id="myentity",
            bot_id="mybot",
            engine_type="openclaw",
            entity_type="staff",
            is_desktop=False,
        )
        assert "skills-repo" in str(path)
        assert not str(path).startswith("/home/admin/.openclaw/workspace/skills/skills-repo")

    def test_local_dir_default_is_non_desktop(self):
        """Omitting is_desktop defaults to non-desktop (cloud/local path)."""
        factory = _factory()
        p_default = factory.get_bot_skills_local_dir(
            entity_id="myentity", bot_id="mybot", engine_type="openclaw", entity_type="staff"
        )
        p_explicit_false = factory.get_bot_skills_local_dir(
            entity_id="myentity",
            bot_id="mybot",
            engine_type="openclaw",
            entity_type="staff",
            is_desktop=False,
        )
        assert str(p_default) == str(p_explicit_false)

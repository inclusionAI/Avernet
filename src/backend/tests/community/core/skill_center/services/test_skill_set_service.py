"""Tests for agentclaw.community.core.services.skill_set_service.SkillSetService."""
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from agentclaw.community.core.skill_center.services.repositories import SkillSetRepository


# ── fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def mock_skill_set_repo():
    repo = MagicMock(spec=SkillSetRepository)
    repo.list_all.return_value = []
    repo.list_all_exclude_deleted.return_value = []
    repo.get_skill_set_by_name_include_deleted.return_value = None
    repo.get_by_id.return_value = None
    repo.create.return_value = {
        "id": "1", "name": "TestSet", "description": "desc",
        "is_default": False, "is_builtin": False, "user_id": None,
        "bolt_id": "default", "env": "dev", "is_active": False,
        "gmt_created": "2024-01-01", "gmt_modified": "2024-01-01",
    }
    repo.update.return_value = {"id": "1", "name": "TestSet"}
    repo.delete.return_value = True
    return repo


# ── TestSkillSetServiceCRUD ──────────────────────────────────────────


class TestSkillSetServiceValidation:
    """_validate_name checks."""

    def test_name_with_underscore_raises(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        with pytest.raises(ValueError, match="underscore"):
            svc._validate_name("bad_name")

    def test_name_without_underscore_ok(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        svc._validate_name("good-name")  # Should not raise


class TestSkillSetServiceCreate:
    """create_skill_set."""

    def test_create_skill_set(self, mock_skill_set_repo):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_skill_set_repo

        result = svc.create_skill_set("TestSet", description="desc")
        assert result["name"] == "TestSet"
        mock_skill_set_repo.create.assert_called_once()


class TestSkillSetServiceList:
    """list_skill_sets."""

    def test_list_skill_sets(self, mock_skill_set_repo):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        mock_skill_set_repo.list_all.return_value = [
            {"id": "1", "name": "Set1"},
            {"id": "2", "name": "Set2"},
        ]
        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_skill_set_repo

        result = svc.list_skill_sets()
        assert len(result) == 2


class TestSkillSetServiceDelete:
    """delete_skill_set."""

    def test_delete_skill_set(self, mock_skill_set_repo):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        mock_skill_set_repo.get_by_id.return_value = {
            "id": "1", "name": "ToDelete", "is_default": False, "is_builtin": False
        }
        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_skill_set_repo

        assert svc.delete_skill_set("1") is True
        mock_skill_set_repo.delete.assert_called_once_with("1")


# ── TestGetBotPaths ──────────────────────────────────────────────────


class TestGetBotPaths:
    """_get_bot_paths helper function."""

    def test_default_paths(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import _get_bot_paths
        from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
        from agentclaw.community.plugins.local.skill_repo_sync import LocalSkillRepoSyncPlugin
        pf = WorkspacePathFactory(skill_repo_sync=LocalSkillRepoSyncPlugin())
        skills_dir, repo_dir, local_dir = _get_bot_paths(path_factory=pf)
        assert skills_dir is not None
        assert repo_dir is not None
        assert local_dir is not None

    def test_user_id_path(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import _get_bot_paths
        from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
        from agentclaw.community.plugins.local.skill_repo_sync import LocalSkillRepoSyncPlugin
        pf = WorkspacePathFactory(skill_repo_sync=LocalSkillRepoSyncPlugin())
        skills_dir, repo_dir, local_dir = _get_bot_paths(path_factory=pf, user_id="12345")
        assert skills_dir is not None


# ── TestRemoveDefaultMcp ────────────────────────────────────────────


class TestRemoveDefaultMcp:
    """remove_mcp_from_skill_set for default vs normal skill sets."""

    def _make_mcp_sync_service_mock(self):
        """Create a mock MCPSyncService."""
        mock = MagicMock()
        mock.remove_mcp_detail = AsyncMock(return_value={"success": True})
        mock.sync_mcp_detail = AsyncMock(return_value={"success": True})
        return mock

    @pytest.mark.asyncio
    async def test_remove_mcp_from_default_skillset_writes_exclusion(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True}
        mock_repo.add_default_mcp_exclusion.return_value = True
        mock_repo.list_all.return_value = []

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_repo
        svc.entity_id = "staff_user1"
        svc.bot_id = "default"
        svc.entity_type = "staff"
        svc._mcp_sync_service = self._make_mcp_sync_service_mock()

        with patch.object(svc, "refresh_mcp_scope", new=AsyncMock()):
            with patch.object(svc, "get_set_mcp_servers", return_value=[]):
                result = await svc.remove_mcp_from_skill_set("1", "mcp.ant.antprocessai.anttaskmcp", user_id="user1")

        assert result.get("success") is True
        mock_repo.add_default_mcp_exclusion.assert_called_once_with(
            user_id="user1", bot_id="default", skill_set_id=1, server_code="mcp.ant.antprocessai.anttaskmcp"
        )

    @pytest.mark.asyncio
    async def test_remove_mcp_from_default_skillset_calls_sync_remove_mcp_when_no_other_refs(self):
        """When no other skill sets reference the MCP, sync_remove_mcp should be called."""
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True}
        mock_repo.add_default_mcp_exclusion.return_value = True
        mock_repo.list_all.return_value = []

        mock_sync = self._make_mcp_sync_service_mock()

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),
            mcp_sync_service=mock_sync,
            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_repo
        svc.entity_id = "staff_user1"
        svc.bot_id = "default"
        svc.entity_type = "staff"

        with patch.object(svc, "refresh_mcp_scope", new=AsyncMock()):
            with patch.object(svc, "get_set_mcp_servers", return_value=[]):
                result = await svc.remove_mcp_from_skill_set("1", "mcp.ant.antprocessai.anttaskmcp", user_id="user1")

        assert result.get("success") is True
        mock_sync.remove_mcp_detail.assert_called_once_with(
            server_code="mcp.ant.antprocessai.anttaskmcp",
            bot_id="default",
            user_id="staff_user1",
        )

    @pytest.mark.asyncio
    async def test_remove_mcp_from_default_skillset_skips_sync_remove_mcp_when_other_refs_exist(self):
        """When another skill set still references the MCP, sync_remove_mcp should NOT be called."""
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True}
        mock_repo.add_default_mcp_exclusion.return_value = True
        mock_repo.list_all.return_value = [
            {"id": "2", "name": "OtherSet", "is_default": False},
        ]

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        mock_sync = self._make_mcp_sync_service_mock()

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),
            mcp_sync_service=mock_sync,
            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_repo
        svc.entity_id = "staff_user1"
        svc.bot_id = "default"
        svc.entity_type = "staff"

        with patch.object(svc, "refresh_mcp_scope", new=AsyncMock()):
            with patch.object(svc, "get_set_mcp_servers", return_value=[
                {"server_code": "mcp.ant.antprocessai.anttaskmcp"}
            ]):
                result = await svc.remove_mcp_from_skill_set("1", "mcp.ant.antprocessai.anttaskmcp", user_id="user1")

        assert result.get("success") is True
        mock_sync.remove_mcp_detail.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_remove_mcp_from_normal_skillset_deletes_record(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "2", "is_default": False}
        mock_repo.remove_mcp_from_set.return_value = True
        mock_repo.list_all.return_value = []
        mock_repo.get_mcp_servers_in_set.return_value = []

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_repo
        svc.entity_id = "staff_user1"
        svc.bot_id = "default"
        svc.entity_type = "staff"
        svc._mcp_sync_service = MagicMock()
        svc._mcp_sync_service.remove_mcp_detail = AsyncMock(return_value={"success": True})

        with patch.object(svc, "refresh_mcp_scope", new=AsyncMock()):
            with patch.object(svc, "get_set_mcp_servers", return_value=[]):
                result = await svc.remove_mcp_from_skill_set("2", "mcp.custom.server", user_id="user1")

        assert result.get("success") is True
        mock_repo.remove_mcp_from_set.assert_called_once_with("2", "mcp.custom.server")

    @pytest.mark.asyncio
    async def test_remove_mcp_nonexistent_returns_false(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "2", "is_default": False}
        mock_repo.remove_mcp_from_set.return_value = False

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_repo

        result = await svc.remove_mcp_from_skill_set("2", "mcp.doesnot.exist", user_id="user1")
        assert result.get("success") is False


# ── TestGetSetMcpServers ─────────────────────────────────────────────


class TestGetSetMcpServers:
    """get_set_mcp_servers returns default MCPs for default skill sets."""

    def test_default_skillset_includes_default_mcps(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True}
        mock_repo.get_mcp_servers_in_set.return_value = []
        mock_repo.get_excluded_mcps.return_value = []

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_repo
        svc.bot_id = "default"

        result = svc.get_set_mcp_servers("1", user_id="user1")
        assert len(result) > 0
        codes = {r["server_code"] for r in result}
        assert "mcp.ant.antprocessai.anttaskmcp" in codes

    def test_default_skillset_excludes_user_excluded_mcps(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True}
        mock_repo.get_mcp_servers_in_set.return_value = []
        mock_repo.get_excluded_mcps.return_value = ["mcp.ant.antprocessai.anttaskmcp"]

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_repo
        svc.bot_id = "default"

        result = svc.get_set_mcp_servers("1", user_id="user1")
        codes = {r["server_code"] for r in result}
        assert "mcp.ant.antprocessai.anttaskmcp" not in codes

    def test_normal_skillset_returns_db_mcps_only(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "2", "is_default": False}
        mock_repo.get_mcp_servers_in_set.return_value = [
            {"id": 10, "server_code": "mcp.custom.server", "name": "custom", "description": "desc", "icon": None}
        ]

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_repo

        result = svc.get_set_mcp_servers("2", user_id="user1")
        assert len(result) == 1
        assert result[0]["server_code"] == "mcp.custom.server"


# ── TestCollectBotActiveMcps ────────────────────────────────────────


class TestCollectBotActiveMcps:
    """collect_bot_active_mcps filters out user-excluded default MCPs."""

    def test_collect_excludes_user_excluded_default_mcps(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        mock_repo = MagicMock()
        mock_repo.get_all_active_skill_sets.return_value = [
            {"id": 1, "is_default": True},
        ]
        mock_repo.get_by_id.side_effect = lambda id: {"id": id, "is_default": True}
        mock_repo.get_mcp_servers_in_set.return_value = []
        mock_repo.get_excluded_mcps.return_value = []
        mock_repo.get_all_excluded_mcps.return_value = ["mcp.ant.antprocessai.anttaskmcp"]

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        svc.skill_set_repo = mock_repo
        svc.bot_id = "default"

        with patch.object(svc, "get_set_mcp_servers") as mock_get_mcps:
            mock_get_mcps.return_value = []
            result = svc.collect_bot_active_mcps("entity1", "default", "user1", "staff")

        codes = {r["server_code"] for r in result}
        assert "mcp.ant.antprocessai.anttaskmcp" not in codes




# ── TestRemoveSkillFromDefaultSet ────────────────────────────────────


class TestRemoveSkillFromDefaultSet:
    """remove_skill_from_set for default skill sets writes exclusion."""

    def _make_svc(self, mock_repo, skill_repo=None, skill_service=None):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
                skill_repo=skill_repo or MagicMock(),
                skill_set_repo=mock_repo,
                mcp_center=MagicMock(),
                mcp_config_service=MagicMock(),
                skill_service=skill_service or MagicMock(),
                bot_repo=MagicMock(),
                path_factory=MagicMock(),
            )
        svc.entity_id = "staff_user1"
        svc.bot_id = "default"
        svc.entity_type = "staff"
        return svc

    @pytest.mark.asyncio
    async def test_default_set_writes_exclusion_and_deactivates(self):
        """Default set: writes exclusion record, deactivates skill, syncs."""
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True, "bolt_id": "default"}
        mock_repo.add_default_skill_exclusion.return_value = True

        mock_skill_repo = MagicMock()
        mock_skill_repo.get_by_id.return_value = {"id": "42", "git_path": "git://biz/my-skill"}

        mock_skill_svc = MagicMock()
        mock_skill_svc.get_link_name.return_value = "biz_my-skill"
        mock_skill_svc.deactivate_skill = AsyncMock(return_value=True)

        svc = self._make_svc(mock_repo, skill_repo=mock_skill_repo, skill_service=mock_skill_svc)
        with patch.object(svc, "_sync_symlinks_to_device_if_needed"):
            result = await svc.remove_skill_from_set("1", "42", user_id="user1")

        assert result is True
        mock_repo.add_default_skill_exclusion.assert_called_once_with(
            user_id="user1", bot_id="default", skill_set_id=1, skill_id=42
        )
        mock_skill_svc.deactivate_skill.assert_called_once_with(
            "biz_my-skill", bolt_id="default", user_id="user1"
        )

    @pytest.mark.asyncio
    async def test_default_set_deactivate_failure_rollback(self):
        """Default set: deactivate_skill failure rolls back exclusion."""
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True, "bolt_id": "default"}
        mock_repo.add_default_skill_exclusion.return_value = True

        mock_skill_repo = MagicMock()
        mock_skill_repo.get_by_id.return_value = {"id": "42", "git_path": "git://biz/my-skill"}

        mock_skill_svc = MagicMock()
        mock_skill_svc.get_link_name.return_value = "biz_my-skill"
        mock_skill_svc.deactivate_skill = AsyncMock(side_effect=Exception("device error"))

        svc = self._make_svc(mock_repo, skill_repo=mock_skill_repo, skill_service=mock_skill_svc)
        result = await svc.remove_skill_from_set("1", "42", user_id="user1")

        assert result is False
        mock_repo.remove_default_skill_exclusion.assert_called_once_with(
            user_id="user1", bot_id="default", skill_set_id=1, skill_id=42
        )

    @pytest.mark.asyncio
    async def test_default_set_no_git_path_still_writes_exclusion(self):
        """Default set: skill without git_path still writes exclusion (no deactivate)."""
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True, "bolt_id": "default"}
        mock_repo.add_default_skill_exclusion.return_value = True

        mock_skill_repo = MagicMock()
        mock_skill_repo.get_by_id.return_value = {"id": "42", "git_path": None}

        mock_skill_svc = MagicMock()

        svc = self._make_svc(mock_repo, skill_repo=mock_skill_repo, skill_service=mock_skill_svc)
        with patch.object(svc, "_sync_symlinks_to_device_if_needed"):
            result = await svc.remove_skill_from_set("1", "42", user_id="user1")

        assert result is True
        mock_repo.add_default_skill_exclusion.assert_called_once()
        mock_skill_svc.deactivate_skill.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_set_uses_entity_id_when_no_user_id(self):
        """Default set: falls back to entity_id when user_id is None."""
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True, "bolt_id": "default"}
        mock_repo.add_default_skill_exclusion.return_value = True

        mock_skill_repo = MagicMock()
        mock_skill_repo.get_by_id.return_value = {"id": "42", "git_path": None}

        svc = self._make_svc(mock_repo, skill_repo=mock_skill_repo)
        with patch.object(svc, "_sync_symlinks_to_device_if_needed"):
            result = await svc.remove_skill_from_set("1", "42", user_id=None)

        assert result is True
        mock_repo.add_default_skill_exclusion.assert_called_once_with(
            user_id="staff_user1", bot_id="default", skill_set_id=1, skill_id=42
        )


# ── TestGetSetSkillsExclusion ────────────────────────────────────────


class TestGetSetSkillsExclusion:
    """get_set_skills filters excluded skills for default sets."""

    def _make_svc(self, mock_repo):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
                skill_repo=MagicMock(),
                skill_set_repo=mock_repo,
                mcp_center=MagicMock(),
                mcp_config_service=MagicMock(),
                skill_service=MagicMock(),
                bot_repo=MagicMock(),
                path_factory=MagicMock(),
            )
        svc.bot_id = "default"
        return svc

    def test_default_set_filters_excluded_skills(self):
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True}
        mock_repo.get_skills_in_set.return_value = [
            {"id": "10", "name": "skill-a"},
            {"id": "20", "name": "skill-b"},
            {"id": "30", "name": "skill-c"},
        ]
        mock_repo.get_excluded_skills.return_value = [20]

        svc = self._make_svc(mock_repo)
        result = svc.get_set_skills("1", user_id="user1")

        assert len(result) == 2
        assert all(s["name"] != "skill-b" for s in result)
        mock_repo.get_excluded_skills.assert_called_once_with(
            user_id="user1", bot_id="default", skill_set_id=1
        )

    def test_default_set_no_exclusions_returns_all(self):
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True}
        mock_repo.get_skills_in_set.return_value = [
            {"id": "10", "name": "skill-a"},
            {"id": "20", "name": "skill-b"},
        ]
        mock_repo.get_excluded_skills.return_value = []

        svc = self._make_svc(mock_repo)
        result = svc.get_set_skills("1", user_id="user1")

        assert len(result) == 2

    def test_default_set_no_user_id_no_filtering(self):
        """Without user_id, no exclusion lookup happens."""
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True}
        mock_repo.get_skills_in_set.return_value = [
            {"id": "10", "name": "skill-a"},
        ]

        svc = self._make_svc(mock_repo)
        result = svc.get_set_skills("1", user_id=None)

        assert len(result) == 1
        mock_repo.get_excluded_skills.assert_not_called()

    def test_normal_set_no_filtering(self):
        """Non-default sets never filter, even with user_id."""
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "2", "is_default": False}
        mock_repo.get_skills_in_set.return_value = [
            {"id": "10", "name": "skill-a"},
            {"id": "20", "name": "skill-b"},
        ]

        svc = self._make_svc(mock_repo)
        result = svc.get_set_skills("2", user_id="user1")

        assert len(result) == 2
        mock_repo.get_excluded_skills.assert_not_called()

    def test_default_set_all_excluded_returns_empty(self):
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True}
        mock_repo.get_skills_in_set.return_value = [
            {"id": "10", "name": "skill-a"},
            {"id": "20", "name": "skill-b"},
        ]
        mock_repo.get_excluded_skills.return_value = [10, 20]

        svc = self._make_svc(mock_repo)
        result = svc.get_set_skills("1", user_id="user1")

        assert result == []


class TestAddSkillsToSetExclusion:
    """add_skills_to_set respects default-set skill exclusions."""

    def _make_svc(self, mock_repo, skill_repo):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
                skill_repo=skill_repo,
                skill_set_repo=mock_repo,
                mcp_center=MagicMock(),
                mcp_config_service=MagicMock(),
                skill_service=MagicMock(),
                bot_repo=MagicMock(),
                path_factory=MagicMock(),
            )
        svc.bot_id = "default"
        return svc

    @pytest.mark.asyncio
    async def test_excluded_default_skill_can_be_added_to_normal_set(self):
        mock_repo = MagicMock()
        mock_repo.get_by_id.side_effect = lambda skill_set_id: {
            "1": {"id": "1", "name": "Default", "is_default": True},
            "2": {"id": "2", "name": "Custom", "is_default": False, "is_active": False},
        }.get(str(skill_set_id))
        mock_repo.list_all.return_value = [
            {"id": "1", "name": "Default", "is_default": True},
            {"id": "2", "name": "Custom", "is_default": False, "is_active": False},
        ]
        mock_repo.get_excluded_skills.return_value = [42]

        def get_skills_in_set(skill_set_id):
            if str(skill_set_id) == "1":
                return [{"id": "42", "name": "bcs-coordination"}]
            return []

        mock_repo.get_skills_in_set.side_effect = get_skills_in_set

        mock_skill_repo = MagicMock()
        mock_skill_repo.get_by_id.return_value = {
            "id": "42",
            "name": "bcs-coordination",
            "git_path": "git://default/bcs-coordination",
            "bolt_id": "default",
        }

        svc = self._make_svc(mock_repo, mock_skill_repo)

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"):
            result = await svc.add_skills_to_set("2", ["42"], user_id="user1")

        assert result["failed"] == []
        assert result["success"] == [{"skill_id": "42", "name": "bcs-coordination"}]
        mock_repo.add_skill_to_set.assert_called_once_with("2", "42")
        mock_repo.get_excluded_skills.assert_called_once_with(
            user_id="user1", bot_id="default", skill_set_id=1
        )

    @pytest.mark.asyncio
    async def test_unexcluded_default_skill_still_blocks_normal_set(self):
        mock_repo = MagicMock()
        mock_repo.get_by_id.side_effect = lambda skill_set_id: {
            "1": {"id": "1", "name": "Default", "is_default": True},
            "2": {"id": "2", "name": "Custom", "is_default": False, "is_active": False},
        }.get(str(skill_set_id))
        mock_repo.list_all.return_value = [
            {"id": "1", "name": "Default", "is_default": True},
            {"id": "2", "name": "Custom", "is_default": False, "is_active": False},
        ]
        mock_repo.get_excluded_skills.return_value = []
        mock_repo.get_skills_in_set.side_effect = lambda skill_set_id: (
            [{"id": "42", "name": "bcs-coordination"}]
            if str(skill_set_id) == "1"
            else []
        )

        mock_skill_repo = MagicMock()
        mock_skill_repo.get_by_id.return_value = {
            "id": "42",
            "name": "bcs-coordination",
            "git_path": "git://default/bcs-coordination",
            "bolt_id": "default",
        }

        svc = self._make_svc(mock_repo, mock_skill_repo)

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"):
            result = await svc.add_skills_to_set("2", ["42"], user_id="user1")

        assert result["success"] == []
        assert result["failed"] == [{
            "skill_id": "42",
            "error": "Skill 'bcs-coordination' already exists in another skill set for this bot",
        }]
        mock_repo.add_skill_to_set.assert_not_called()


class TestAddMcpToSkillSetTeclawEndToEnd:
    """End-to-end: adding an MCP to a teclaw bot's skill set succeeds and delivers
    via the whole-artifact path — reproduces the reported bug (the add used to fail
    with '缺少设备连接信息' and roll the DB write back). Uses a REAL MCPSyncService so
    the teclaw routing actually runs; only the leaf deps are mocked."""

    def _real_mcp_sync_service(self, *, resolver, dispatcher, passport_update):
        from agentclaw.community.core.mcp.services.sync_service import MCPSyncService

        svc = MCPSyncService.__new__(MCPSyncService)
        provider = MagicMock()
        provider.collect_bot_active_mcps.return_value = []
        provider.collect_bot_mcps.return_value = []
        svc._mcp_provider_factory = lambda: provider
        svc._mcp_provider_cached = provider
        svc.mcp_center = MagicMock()
        svc.user_mcp_config_repo = MagicMock()
        svc.passport_update = passport_update
        svc.mcp_config_service = MagicMock()
        svc.mcp_config_service.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)
        svc.bot_repository = MagicMock()
        # 新链路:resolver + dispatcher (旧 device_sync_supplier 已废弃) — provider thunks
        svc._resolver_provider = lambda: resolver
        svc._device_sync_dispatcher_provider = lambda: dispatcher
        return svc

    @pytest.mark.asyncio
    async def test_add_mcp_to_teclaw_bot_succeeds_without_rollback(self):
        from agentclaw.community.core.devices.services.device_context import DeviceContext
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

        # Per-bot DeviceSyncPlugin double: MCP methods are SYNC (run via to_thread).
        # For a teclaw bot the real plugin delivers the whole artifact; here we assert
        # the service routes the add + scope legs through it (provider-blind).
        plugin = MagicMock()
        plugin.sync_single_mcp = MagicMock(return_value=True)
        plugin.sync_all_mcp_servers = MagicMock(return_value=True)
        ctx = DeviceContext(
            provider="teclaw",
            conn_info={"engine_type": "teclaw"},
            binding_id=42,
            bot_id="bot1",
            user_id="staff_user1",
        )
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = plugin
        passport_update = MagicMock()
        mcp_sync = self._real_mcp_sync_service(
            resolver=resolver, dispatcher=dispatcher, passport_update=passport_update,
        )

        skill_set_repo = MagicMock()
        skill_set_repo.get_mcp_servers_in_set.return_value = []  # not already associated

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
                skill_repo=MagicMock(),
                skill_set_repo=skill_set_repo,
                mcp_center=MagicMock(),
                mcp_config_service=MagicMock(),
                skill_service=MagicMock(),
                bot_repo=MagicMock(),
                path_factory=MagicMock(),
            )
        svc.bot_id = "bot1"
        svc.entity_id = "staff_user1"
        svc.entity_type = "staff"
        svc.engine_type = "teclaw"
        svc._mcp_sync_service = mcp_sync
        svc._bot_repo.get_by_id_and_owner.return_value = {"active_engine": "teclaw"}
        svc.mcp_center = MagicMock()
        svc.mcp_center.get_mcp_detail.return_value = {"server_code": "mcp.t.1", "name": "M1"}

        with patch.object(svc, "get_skill_set", return_value={"id": "ss1", "is_default": False}):
            result = await svc.add_mcp_to_skill_set("ss1", "mcp.t.1", user_id="user1")

        # The reported bug: this used to be False with "缺少设备连接信息".
        assert result["success"] is True, result
        # Delivered via the per-bot plugin (resolved through resolver+dispatcher): the
        # add leg pushes the MCP and the scope leg declares the allow-list.
        resolver.resolve_for_bot.assert_called()
        dispatcher.dispatch.assert_called()
        plugin.sync_single_mcp.assert_called_once()
        plugin.sync_all_mcp_servers.assert_called_once()
        # DB association persisted — no rollback.
        skill_set_repo.remove_mcp_from_set.assert_not_called()
        # Scope refresh still updates the passport for teclaw.
        passport_update.update_passport.assert_called_once()


# ── TestOwnerIdResolution ────────────────────────────────────────────


class TestAddSkillsToSetOwnerIdResolution:
    """add_skills_to_set: owner_id resolution when auto-activating skills in an active set."""

    def _make_svc(self, *, bot_repo=None, skill_repo=None, skill_service=None):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        if bot_repo is None:
            bot_repo = MagicMock()
        if skill_repo is None:
            skill_repo = MagicMock()
        if skill_service is None:
            skill_service = MagicMock()
            skill_service.activate_skill = AsyncMock(return_value=True)
            skill_service.RESERVED_SKILL_NAMES = set()
        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
                skill_repo=skill_repo,
                skill_set_repo=MagicMock(),
                mcp_center=MagicMock(),
                mcp_config_service=MagicMock(),
                skill_service=skill_service,
                bot_repo=bot_repo,
                path_factory=MagicMock(),
            )
        svc.bot_id = "bot-1"
        svc.entity_id = "staff_entity1"
        return svc

    @pytest.mark.asyncio
    async def test_auto_activate_uses_bot_owner_id(self):
        """When bot has owner_id, it should be used for activate_skill."""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {"id": "bot-1", "owner_id": "owner_abc"}

        skill_repo = MagicMock()
        skill_repo.get_by_id.return_value = {"id": "10", "name": "skill-a", "git_path": "git://biz/skill-a"}

        skill_service = MagicMock()
        skill_service.activate_skill = AsyncMock(return_value=True)
        skill_service.RESERVED_SKILL_NAMES = set()

        mock_set_repo = MagicMock()
        mock_set_repo.get_by_id.return_value = {"id": "1", "is_default": False, "is_active": True}
        mock_set_repo.get_skills_in_set.return_value = []
        mock_set_repo.list_all.return_value = []
        mock_set_repo.add_skill_to_set.return_value = True

        svc = self._make_svc(bot_repo=bot_repo, skill_repo=skill_repo, skill_service=skill_service)
        svc.skill_set_repo = mock_set_repo

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"):
            result = await svc.add_skills_to_set("1", ["10"], user_id="user1")

        assert result["success"] == [{"skill_id": "10", "name": "skill-a"}]
        # activate_skill should be called with owner_id from bot
        skill_service.activate_skill.assert_called_once_with(
            "git://biz/skill-a", user_id="owner_abc", bolt_id="bot-1"
        )

    @pytest.mark.asyncio
    async def test_auto_activate_fallback_to_entity_id_when_no_owner(self):
        """When bot exists but owner_id is None, fallback to entity_id."""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {"id": "bot-1", "owner_id": None}

        skill_repo = MagicMock()
        skill_repo.get_by_id.return_value = {"id": "10", "name": "skill-a", "git_path": "git://biz/skill-a"}

        skill_service = MagicMock()
        skill_service.activate_skill = AsyncMock(return_value=True)
        skill_service.RESERVED_SKILL_NAMES = set()

        mock_set_repo = MagicMock()
        mock_set_repo.get_by_id.return_value = {"id": "1", "is_default": False, "is_active": True}
        mock_set_repo.get_skills_in_set.return_value = []
        mock_set_repo.list_all.return_value = []
        mock_set_repo.add_skill_to_set.return_value = True

        svc = self._make_svc(bot_repo=bot_repo, skill_repo=skill_repo, skill_service=skill_service)
        svc.skill_set_repo = mock_set_repo

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"):
            result = await svc.add_skills_to_set("1", ["10"], user_id="user1")

        assert result["success"] == [{"skill_id": "10", "name": "skill-a"}]
        # activate_skill should fallback to entity_id
        skill_service.activate_skill.assert_called_once_with(
            "git://biz/skill-a", user_id="staff_entity1", bolt_id="bot-1"
        )

    @pytest.mark.asyncio
    async def test_auto_activate_fallback_to_user_id_when_no_bot(self):
        """When bot_repo returns None, fallback to entity_id then user_id."""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = None

        skill_repo = MagicMock()
        skill_repo.get_by_id.return_value = {"id": "10", "name": "skill-a", "git_path": "git://biz/skill-a"}

        skill_service = MagicMock()
        skill_service.activate_skill = AsyncMock(return_value=True)
        skill_service.RESERVED_SKILL_NAMES = set()

        mock_set_repo = MagicMock()
        mock_set_repo.get_by_id.return_value = {"id": "1", "is_default": False, "is_active": True}
        mock_set_repo.get_skills_in_set.return_value = []
        mock_set_repo.list_all.return_value = []
        mock_set_repo.add_skill_to_set.return_value = True

        svc = self._make_svc(bot_repo=bot_repo, skill_repo=skill_repo, skill_service=skill_service)
        svc.skill_set_repo = mock_set_repo
        # Set entity_id to None to test fallback to user_id
        svc.entity_id = None

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"):
            result = await svc.add_skills_to_set("1", ["10"], user_id="user1")

        assert result["success"] == [{"skill_id": "10", "name": "skill-a"}]
        # activate_skill should fallback to user_id (since bot is None and entity_id is None)
        skill_service.activate_skill.assert_called_once_with(
            "git://biz/skill-a", user_id="user1", bolt_id="bot-1"
        )


class TestSwitchToSkillSetOwnerIdResolution:
    """SkillSetSwitcher.switch_to_skill_set: owner_id resolution."""

    def _make_switcher(self, *, bot_repo=None, skill_service=None, skill_set_repo=None):
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            SkillSetSwitcher,
            SkillSetService,
        )

        if bot_repo is None:
            bot_repo = MagicMock()
        if skill_service is None:
            skill_service = MagicMock()
            skill_service.activate_skill = AsyncMock(return_value=True)
            skill_service.get_active_skills = MagicMock(return_value=[])
            skill_service.RESERVED_SKILL_NAMES = set()
        if skill_set_repo is None:
            skill_set_repo = MagicMock()

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
                skill_repo=MagicMock(),
                skill_set_repo=skill_set_repo,
                mcp_center=MagicMock(),
                mcp_config_service=MagicMock(),
                skill_service=skill_service,
                bot_repo=bot_repo,
                path_factory=MagicMock(),
            )
        svc.bot_id = "bot-1"
        svc.entity_id = "staff_entity1"

        skill_set_factory = MagicMock()
        skill_set_factory.create.return_value = svc

        switcher = SkillSetSwitcher(
            skill_set_factory=skill_set_factory,
            resolver=MagicMock(),
            device_sync_dispatcher=MagicMock(),
            device_plugin=MagicMock(),
            path_factory=MagicMock(),
            device_fs_dispatcher=MagicMock(),
            skills_dir=MagicMock(),
            bot_id="bot-1",
            user_id="user1",
        )
        return switcher, svc

    def _patch_switcher_deps(self, switcher, svc):
        """Patch all post-activation deps so switch_to_skill_set runs to completion."""
        svc.refresh_mcp_scope = AsyncMock(return_value={"success": True})
        mock_mcp_sync = MagicMock()
        mock_mcp_sync.sync_mcp_details = AsyncMock(return_value={"success": True})
        svc._mcp_sync_service = mock_mcp_sync
        switcher._do_device_sync = MagicMock(return_value={"success": True})

    @pytest.mark.asyncio
    async def test_switch_uses_bot_owner_id(self):
        """When bot has owner_id, switch_to_skill_set should use it for activate_skill."""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {"id": "bot-1", "owner_id": "owner_xyz"}

        skill_service = MagicMock()
        skill_service.activate_skill = AsyncMock(return_value=True)
        skill_service.get_active_skills = MagicMock(return_value=[])
        skill_service.RESERVED_SKILL_NAMES = set()

        skill_set_repo = MagicMock()
        skill_set_repo.get_by_id.return_value = {"id": "1", "name": "Set1", "is_default": False}

        switcher, svc = self._make_switcher(
            bot_repo=bot_repo, skill_service=skill_service, skill_set_repo=skill_set_repo,
        )
        svc.get_set_skills = MagicMock(return_value=[
            {"id": "10", "git_path": "git://biz/skill-a"},
        ])
        switcher._cleanup_all_non_reserved_items = MagicMock(return_value=[])
        self._patch_switcher_deps(switcher, svc)

        with patch.object(switcher, "_save_current_skill_set"), \
             patch("agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"):
            result = await switcher.switch_to_skill_set("1", user_id="user1")

        assert result.success is True
        skill_service.activate_skill.assert_called_once_with(
            "git://biz/skill-a", user_id="owner_xyz", bolt_id="bot-1"
        )

    @pytest.mark.asyncio
    async def test_switch_fallback_to_entity_id_when_no_owner(self):
        """When bot exists but owner_id is None, fallback to entity_id."""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {"id": "bot-1", "owner_id": None}

        skill_service = MagicMock()
        skill_service.activate_skill = AsyncMock(return_value=True)
        skill_service.get_active_skills = MagicMock(return_value=[])
        skill_service.RESERVED_SKILL_NAMES = set()

        skill_set_repo = MagicMock()
        skill_set_repo.get_by_id.return_value = {"id": "1", "name": "Set1", "is_default": False}

        switcher, svc = self._make_switcher(
            bot_repo=bot_repo, skill_service=skill_service, skill_set_repo=skill_set_repo,
        )
        svc.get_set_skills = MagicMock(return_value=[
            {"id": "10", "git_path": "git://biz/skill-a"},
        ])
        switcher._cleanup_all_non_reserved_items = MagicMock(return_value=[])
        self._patch_switcher_deps(switcher, svc)

        with patch.object(switcher, "_save_current_skill_set"), \
             patch("agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"):
            result = await switcher.switch_to_skill_set("1", user_id="user1")

        assert result.success is True
        skill_service.activate_skill.assert_called_once_with(
            "git://biz/skill-a", user_id="staff_entity1", bolt_id="bot-1"
        )

    @pytest.mark.asyncio
    async def test_switch_fallback_to_user_id_when_no_bot(self):
        """When bot_repo returns None, fallback to entity_id then user_id."""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = None

        skill_service = MagicMock()
        skill_service.activate_skill = AsyncMock(return_value=True)
        skill_service.get_active_skills = MagicMock(return_value=[])
        skill_service.RESERVED_SKILL_NAMES = set()

        skill_set_repo = MagicMock()
        skill_set_repo.get_by_id.return_value = {"id": "1", "name": "Set1", "is_default": False}

        switcher, svc = self._make_switcher(
            bot_repo=bot_repo, skill_service=skill_service, skill_set_repo=skill_set_repo,
        )
        svc.entity_id = None  # No entity_id either
        svc.get_set_skills = MagicMock(return_value=[
            {"id": "10", "git_path": "git://biz/skill-a"},
        ])
        switcher._cleanup_all_non_reserved_items = MagicMock(return_value=[])
        self._patch_switcher_deps(switcher, svc)

        with patch.object(switcher, "_save_current_skill_set"), \
             patch("agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"):
            result = await switcher.switch_to_skill_set("1", user_id="user1")

        assert result.success is True
        skill_service.activate_skill.assert_called_once_with(
            "git://biz/skill-a", user_id="user1", bolt_id="bot-1"
        )


class TestSyncSkillSetToActiveOwnerIdResolution:
    """SkillSetSwitcher.sync_skill_set_to_active: owner_id resolution."""

    def _make_switcher(self, *, bot_repo=None, skill_service=None, skill_set_repo=None):
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            SkillSetSwitcher,
            SkillSetService,
        )

        if bot_repo is None:
            bot_repo = MagicMock()
        if skill_service is None:
            skill_service = MagicMock()
            skill_service.activate_skill = AsyncMock(return_value=True)
            skill_service.get_active_skills = MagicMock(return_value=[])
            skill_service.RESERVED_SKILL_NAMES = set()
            skill_service.repo_dir = MagicMock()
            skill_service.local_dir = MagicMock()
        if skill_set_repo is None:
            skill_set_repo = MagicMock()

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
                skill_repo=MagicMock(),
                skill_set_repo=skill_set_repo,
                mcp_center=MagicMock(),
                mcp_config_service=MagicMock(),
                skill_service=skill_service,
                bot_repo=bot_repo,
                path_factory=MagicMock(),
            )
        svc.bot_id = "bot-1"
        svc.entity_id = "staff_entity1"

        skill_set_factory = MagicMock()
        skill_set_factory.create.return_value = svc

        switcher = SkillSetSwitcher(
            skill_set_factory=skill_set_factory,
            resolver=MagicMock(),
            device_sync_dispatcher=MagicMock(),
            device_plugin=MagicMock(),
            path_factory=MagicMock(),
            device_fs_dispatcher=MagicMock(),
            skills_dir=MagicMock(),
            bot_id="bot-1",
            user_id="user1",
        )
        return switcher, svc

    @pytest.mark.asyncio
    async def test_sync_uses_bot_owner_id(self):
        """When bot has owner_id, sync_skill_set_to_active should use it for activate_skill."""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {"id": "bot-1", "owner_id": "owner_sync"}

        skill_service = MagicMock()
        skill_service.activate_skill = AsyncMock(return_value=True)
        skill_service.get_active_skills = MagicMock(return_value=[])
        skill_service.RESERVED_SKILL_NAMES = set()
        skill_service.repo_dir = MagicMock()
        skill_service.local_dir = MagicMock()

        skill_set_repo = MagicMock()
        skill_set_repo.get_by_id.return_value = {"id": "1", "name": "Set1", "is_default": False}

        switcher, svc = self._make_switcher(
            bot_repo=bot_repo, skill_service=skill_service, skill_set_repo=skill_set_repo,
        )
        svc.get_set_skills = MagicMock(return_value=[
            {"id": "10", "git_path": "git://biz/skill-a"},
        ])
        switcher._get_active_skill_ids = MagicMock(return_value=[])

        with patch.object(switcher, "_save_current_skill_set"):
            result = await switcher.sync_skill_set_to_active("1", user_id="user1")

        assert result.success is True
        skill_service.activate_skill.assert_called_once_with(
            "git://biz/skill-a", user_id="owner_sync", bolt_id="bot-1"
        )

    @pytest.mark.asyncio
    async def test_sync_fallback_to_entity_id_when_no_owner(self):
        """When bot exists but owner_id is None, fallback to entity_id."""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = {"id": "bot-1", "owner_id": None}

        skill_service = MagicMock()
        skill_service.activate_skill = AsyncMock(return_value=True)
        skill_service.get_active_skills = MagicMock(return_value=[])
        skill_service.RESERVED_SKILL_NAMES = set()
        skill_service.repo_dir = MagicMock()
        skill_service.local_dir = MagicMock()

        skill_set_repo = MagicMock()
        skill_set_repo.get_by_id.return_value = {"id": "1", "name": "Set1", "is_default": False}

        switcher, svc = self._make_switcher(
            bot_repo=bot_repo, skill_service=skill_service, skill_set_repo=skill_set_repo,
        )
        svc.get_set_skills = MagicMock(return_value=[
            {"id": "10", "git_path": "git://biz/skill-a"},
        ])
        switcher._get_active_skill_ids = MagicMock(return_value=[])

        with patch.object(switcher, "_save_current_skill_set"):
            result = await switcher.sync_skill_set_to_active("1", user_id="user1")

        assert result.success is True
        skill_service.activate_skill.assert_called_once_with(
            "git://biz/skill-a", user_id="staff_entity1", bolt_id="bot-1"
        )

    @pytest.mark.asyncio
    async def test_sync_fallback_to_user_id_when_no_bot(self):
        """When bot_repo returns None, fallback to entity_id then user_id."""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = None

        skill_service = MagicMock()
        skill_service.activate_skill = AsyncMock(return_value=True)
        skill_service.get_active_skills = MagicMock(return_value=[])
        skill_service.RESERVED_SKILL_NAMES = set()
        skill_service.repo_dir = MagicMock()
        skill_service.local_dir = MagicMock()

        skill_set_repo = MagicMock()
        skill_set_repo.get_by_id.return_value = {"id": "1", "name": "Set1", "is_default": False}

        switcher, svc = self._make_switcher(
            bot_repo=bot_repo, skill_service=skill_service, skill_set_repo=skill_set_repo,
        )
        svc.entity_id = None  # No entity_id either
        svc.get_set_skills = MagicMock(return_value=[
            {"id": "10", "git_path": "git://biz/skill-a"},
        ])
        switcher._get_active_skill_ids = MagicMock(return_value=[])

        with patch.object(switcher, "_save_current_skill_set"):
            result = await switcher.sync_skill_set_to_active("1", user_id="user1")

        assert result.success is True
        skill_service.activate_skill.assert_called_once_with(
            "git://biz/skill-a", user_id="user1", bolt_id="bot-1"
        )

"""Tests for agentclaw.community.core.services.skill_set_service.SkillSetService."""
import logging
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from agentclaw.community.core.repository.protocols.skill_center import SkillSetRepository


def _edit_guard():
    guard = MagicMock()
    guard.acquire_for_edit_wait = AsyncMock(return_value=object())
    return guard


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

    def test_collect_excludes_user_excluded_default_mcps(self, caplog):
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
            reader=MagicMock(
                **{"active_mcp_server_codes.return_value": frozenset()}
            ),
        )
        svc.skill_set_repo = mock_repo
        svc.bot_id = "default"

        caplog.set_level(logging.INFO)
        with patch.object(svc, "get_set_mcp_servers") as mock_get_mcps:
            mock_get_mcps.return_value = []
            result = svc.collect_bot_active_mcps("entity1", "default", "user1", "staff")

        codes = {r["server_code"] for r in result}
        assert "mcp.ant.antprocessai.anttaskmcp" not in codes
        messages = [record.getMessage() for record in caplog.records]
        for stage in (
            "default_ext_info",
            "active_skill_sets",
            "default_mcp_exclusions",
            "active_skill_assets",
            "installed_mcp_codes",
            "resolve_non_default_codes",
        ):
            assert any(
                "[collect_bot_active_mcps] timing" in message
                and f"stage={stage}" in message
                and "duration_ms=" in message
                for message in messages
            )

    def test_get_bot_mcp_codes_for_env_uses_only_explicit_env_repository_reads(self):
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            SkillSetService,
        )

        mock_repo = MagicMock()
        mock_repo.get_all_active_skill_sets_for_env.return_value = [
            {"id": "2", "name": "Prod Set", "is_default": False}
        ]
        mock_repo.get_mcp_servers_in_set_for_env.return_value = [
            {"id": "10", "server_code": "mcp.prod.only", "name": "prod"}
        ]
        mock_repo.get_all_excluded_mcps.return_value = []
        with patch(
            "agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"
        ):
            svc = SkillSetService(
                skill_repo=MagicMock(),
                skill_set_repo=mock_repo,
                mcp_center=MagicMock(),
                mcp_config_service=MagicMock(),
                skill_service=MagicMock(),
                bot_repo=MagicMock(),
                path_factory=MagicMock(),
            )

        codes = svc.get_bot_mcp_codes_for_env(
            entity_id="172168",
            bot_id="default",
            user_id="172168",
            entity_type="staff",
            engine_type="openclaw",
            target_env="prod",
        )

        assert "mcp.prod.only" in codes
        mock_repo.get_all_active_skill_sets_for_env.assert_called_once_with(
            user_id="172168",
            bolt_id="default",
            engine_type="openclaw",
            env="prod",
        )
        mock_repo.get_mcp_servers_in_set_for_env.assert_called_once_with(
            "2", env="prod"
        )
        mock_repo.get_all_active_skill_sets.assert_not_called()
        mock_repo.get_mcp_servers_in_set.assert_not_called()




# ── TestRemoveSkillFromDefaultSet ────────────────────────────────────


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

    def test_default_set_exclusions_are_read_for_the_bot_owner(self):
        """A collaborator sees the owner's removals, not their own.

        The exclusion is the Bot's state: it is written under the owner and
        read under the owner by the listing repair and the runtime projection.
        Reading it here under the caller would give a collaborator a different
        Default membership than the Bot actually has.
        """
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"id": "1", "is_default": True}
        mock_repo.get_skills_in_set.return_value = [{"id": "10", "name": "a"}]
        mock_repo.get_excluded_skills.return_value = []

        svc = self._make_svc(mock_repo)
        svc.entity_id = "staff_owner"
        svc.get_set_skills("1", user_id="collaborator")

        mock_repo.get_excluded_skills.assert_called_once_with(
            user_id="staff_owner", bot_id="default", skill_set_id=1
        )

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


class TestSyncMcpDelivery:
    """sync_mcp_delivery: per-MCP configuration, scoped to what changed.

    Delivery is deliberately not total. Re-pushing an unchanged MCP rewrites
    its device-side configuration from the DB for nothing, and removing one
    still supplied elsewhere would break it — so this only ever sees the codes
    a mutation declared, already guarded against the projected set.
    """

    def _make_svc(self, *, delivery=None, removal=None):
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
        svc.bot_id = "bot1"
        svc.user_id = "user1"
        svc.entity_id = "staff_user1"
        svc.engine_type = "moltis"
        svc.mcp_center = MagicMock()
        svc.mcp_center.get_mcp_detail.side_effect = lambda code: {"server_code": code}
        svc._mcp_sync_service = MagicMock()
        svc._mcp_sync_service.sync_mcp_details_for_bot = AsyncMock(
            return_value=delivery if delivery is not None else {"success": True}
        )
        svc._mcp_sync_service.remove_mcp_detail = AsyncMock(
            return_value=removal if removal is not None else {"success": True}
        )
        return svc

    @pytest.mark.asyncio
    async def test_one_claimed_code_costs_one_push(self):
        """Problem 3, stated as a test.

        Adding one MCP to a Bot that already has others must push exactly
        that one — the batch entrypoint resolves the device once, so a
        single-entry batch is one device write, not a fan-out.
        """
        svc = self._make_svc()

        assert await svc.sync_mcp_delivery(
            claimed=frozenset({"mcp.new"}), released=frozenset()
        ) is True

        svc._mcp_sync_service.sync_mcp_details_for_bot.assert_awaited_once_with(
            user_id="user1",
            mcp_entries=[{"server_code": "mcp.new"}],
            bot_id="bot1",
            entity_id="staff_user1",
            engine_type="moltis",
        )
        svc._mcp_sync_service.remove_mcp_detail.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_released_code_is_removed_from_the_device(self):
        """Problem 2: the removal that had no production caller at all.

        Without it the MCP leaves the allow-list but its endpoint, api_key
        and headers stay registered on the container indefinitely.
        """
        svc = self._make_svc()

        assert await svc.sync_mcp_delivery(
            claimed=frozenset(), released=frozenset({"mcp.gone"})
        ) is True

        svc._mcp_sync_service.remove_mcp_detail.assert_awaited_once_with(
            server_code="mcp.gone", bot_id="bot1", user_id="staff_user1"
        )
        svc._mcp_sync_service.sync_mcp_details_for_bot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_scope_touches_the_device_at_all(self):
        """A mutation that changed no MCP claim does no MCP I/O."""
        svc = self._make_svc()

        assert await svc.sync_mcp_delivery(
            claimed=frozenset(), released=frozenset()
        ) is True

        svc._mcp_sync_service.sync_mcp_details_for_bot.assert_not_awaited()
        svc._mcp_sync_service.remove_mcp_detail.assert_not_awaited()
        svc.mcp_center.get_mcp_detail.assert_not_called()

    @pytest.mark.asyncio
    async def test_catalogue_gap_on_a_claimed_code_fails_delivery(self):
        """Still fails closed — but only for a code being installed.

        The old combined method resolved every projected code, so an
        unrelated delisted MCP blocked every add.
        """
        svc = self._make_svc()
        svc.mcp_center.get_mcp_detail.side_effect = lambda code: None

        assert await svc.sync_mcp_delivery(
            claimed=frozenset({"mcp.delisted"}), released=frozenset()
        ) is False
        svc._mcp_sync_service.sync_mcp_details_for_bot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_push_reports_failure(self):
        svc = self._make_svc(delivery={"success": False, "error": "device refused"})

        assert await svc.sync_mcp_delivery(
            claimed=frozenset({"mcp.new"}), released=frozenset()
        ) is False

    @pytest.mark.asyncio
    async def test_failed_removal_reports_failure(self):
        svc = self._make_svc(removal={"success": False, "error": "device refused"})

        assert await svc.sync_mcp_delivery(
            claimed=frozenset(), released=frozenset({"mcp.gone"})
        ) is False

    @pytest.mark.asyncio
    async def test_a_raising_push_does_not_escape(self):
        svc = self._make_svc()
        svc._mcp_sync_service.sync_mcp_details_for_bot = AsyncMock(
            side_effect=RuntimeError("device refused the payload")
        )

        assert await svc.sync_mcp_delivery(
            claimed=frozenset({"mcp.new"}), released=frozenset()
        ) is False

    @pytest.mark.asyncio
    async def test_configuration_lands_before_it_is_withdrawn(self):
        """Claims are pushed before releases are removed.

        Within one scope the two are independent, but the order is fixed so a
        code that is both released and re-claimed cannot end up deleted.
        """
        svc = self._make_svc()
        order: list[str] = []
        svc._mcp_sync_service.sync_mcp_details_for_bot = AsyncMock(
            side_effect=lambda **kw: order.append("push") or {"success": True}
        )
        svc._mcp_sync_service.remove_mcp_detail = AsyncMock(
            side_effect=lambda **kw: order.append("remove") or {"success": True}
        )

        await svc.sync_mcp_delivery(
            claimed=frozenset({"mcp.new"}), released=frozenset({"mcp.gone"})
        )

        assert order == ["push", "remove"]


class TestSyncMcpDesiredState:
    """sync_mcp_desired_state: declaration only.

    Declaration is total and overwrite-style — ``sync_all_mcp_servers`` is the
    device's reconciliation command, so it carries the whole projected set and
    runs even when that set is empty. Per-MCP configuration delivery is a
    separate, scoped act; see ``TestSyncMcpDelivery``.
    """

    def _make_svc(self, *, delivery=None):
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
        svc.bot_id = "bot1"
        svc.user_id = "user1"
        svc.entity_id = "staff_user1"
        svc.engine_type = "moltis"
        svc.mcp_center = MagicMock()
        svc.mcp_center.get_mcp_detail.side_effect = lambda code: {"server_code": code}
        svc._mcp_sync_service = MagicMock()
        svc._mcp_sync_service.sync_mcp_details_for_bot = AsyncMock(
            return_value=delivery if delivery is not None else {"success": True}
        )

        plugin = MagicMock()
        plugin.sync_all_mcp_servers = MagicMock(return_value=True)
        svc._resolver = MagicMock()
        svc._device_sync_dispatcher = MagicMock()
        svc._device_sync_dispatcher.dispatch.return_value = plugin
        return svc, plugin

    @pytest.mark.asyncio
    async def test_declares_every_projected_code_in_sorted_order(self):
        svc, plugin = self._make_svc()
        codes = {"mcp.s2", "mcp.s0", "mcp.s1"}

        assert await svc.sync_mcp_desired_state(server_codes=codes) is True

        # Dicts, not bare strings: ``filter_servers`` reads server_code off
        # each entry, so a list of strings would declare an empty allow-list.
        plugin.sync_all_mcp_servers.assert_called_once_with(
            [{"server_code": code} for code in sorted(codes)]
        )

    @pytest.mark.asyncio
    async def test_declaration_pushes_no_configuration(self):
        """The regression this split exists to fix.

        Declaring the allow-list used to also re-push every projected MCP's
        configuration, so adding one MCP rewrote the device-side config of
        every other MCP the Bot had.
        """
        svc, plugin = self._make_svc()

        await svc.sync_mcp_desired_state(server_codes={"mcp.s0", "mcp.s1"})

        svc._mcp_sync_service.sync_mcp_details_for_bot.assert_not_awaited()
        svc.mcp_center.get_mcp_detail.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_catalogue_gap_no_longer_blocks_the_declaration(self):
        """Declaration needs codes, not payloads.

        It used to resolve every code through MCP Center, so one delisted MCP
        anywhere on the Bot failed the whole projection.
        """
        svc, plugin = self._make_svc()
        svc.mcp_center.get_mcp_detail.side_effect = (
            lambda code: None if code == "mcp.s1" else {"server_code": code}
        )

        assert (
            await svc.sync_mcp_desired_state(server_codes={"mcp.s0", "mcp.s1"})
            is True
        )
        plugin.sync_all_mcp_servers.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_desired_state_still_declares_the_empty_allow_list(self):
        svc, plugin = self._make_svc()

        assert await svc.sync_mcp_desired_state(server_codes=set()) is True

        plugin.sync_all_mcp_servers.assert_called_once_with([])

    @pytest.mark.asyncio
    async def test_blocking_device_calls_do_not_run_on_the_event_loop(self):
        """resolve_for_bot and sync_all_mcp_servers are blocking HTTP; running
        them inline would stall every other task on this worker."""
        import threading

        loop_thread = threading.get_ident()
        seen: dict[str, int] = {}

        svc, plugin = self._make_svc()
        svc._resolver.resolve_for_bot.side_effect = (
            lambda *a, **k: seen.setdefault("resolve", threading.get_ident())
        )
        plugin.sync_all_mcp_servers.side_effect = (
            lambda *a, **k: seen.setdefault("declare", threading.get_ident()) and True
        )

        await svc.sync_mcp_desired_state(server_codes={"mcp.s0"})

        assert seen["resolve"] != loop_thread
        assert seen["declare"] != loop_thread

"""Tests for SkillSetService.refresh_mcp_scope delegating to MCPSyncService."""
import pytest
from unittest.mock import AsyncMock, MagicMock


class TestRefreshMcpScope:
    """SkillSetService.refresh_mcp_scope should delegate to MCPSyncService."""

    @pytest.mark.asyncio
    async def test_calls_mcp_sync_service(self):
        """Should delegate to MCPSyncService.refresh_mcp_scope with correct params."""
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

        mock_mcp_sync_service = MagicMock()
        mock_mcp_sync_service.refresh_mcp_scope = AsyncMock(
            return_value={"success": True}
        )

        from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
        from agentclaw.community.plugins.local.skill_repo_sync import LocalSkillRepoSyncPlugin

        service = SkillSetService(
            skill_repo=MagicMock(),
            skill_set_repo=MagicMock(),
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            entity_id="100014",
            bot_id="test-bot",
            engine_type="openclaw",
            entity_type="staff",
            mcp_sync_service=mock_mcp_sync_service,
            bot_repo=MagicMock(),
            path_factory=WorkspacePathFactory(skill_repo_sync=LocalSkillRepoSyncPlugin()),
        )

        result = await service.refresh_mcp_scope(
            user_id="100014", engine_type="openclaw"
        )

        assert result["success"] is True
        mock_mcp_sync_service.refresh_mcp_scope.assert_awaited_once_with(
            user_id="100014",
            entity_id="100014",
            bot_id="test-bot",
            entity_type="staff",
            engine_type="openclaw",
        )

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.mcp.services.sync_service import MCPSyncService


@pytest.mark.asyncio
async def test_identity_sync_replaces_complete_non_local_mcp_manifest() -> None:
    passport = MagicMock()
    service = MCPSyncService(
        mcp_provider_factory=MagicMock(),
        mcp_center=MagicMock(),
        user_mcp_config_repo=MagicMock(),
        passport_update=passport,
        mcp_config_service=MagicMock(),
        bot_repository=MagicMock(),
        resolver_provider=MagicMock(),
        device_sync_dispatcher_provider=MagicMock(),
    )

    result = await service.sync_mcp_identity_to_agent_principal(
        user_id="owner-1",
        entity_id="entity-1",
        bot_id="bot-1",
        entity_type="staff",
        engine_type="openclaw",
        active_mcps=[
            {
                "server_code": "calendar",
                "name": "Calendar",
                "description": "calendar access",
            },
            {"server_code": "shell", "source": "local"},
        ],
        identity_modes={"calendar": "caller"},
    )

    assert result == {"success": True}
    passport.update_mcp_identity_to_agent_principal.assert_called_once_with(
        bot_id="bot-1",
        user_id="owner-1",
        mcp_items=[
            {
                "mcp_code": "calendar",
                "mcp_name": "Calendar",
                "mcp_desc": "calendar access",
                "identity_mode": "caller",
            }
        ],
    )

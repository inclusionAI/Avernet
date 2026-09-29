"""Internal product adapter for scoped MCP Header groups."""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.mcp.router import router
from agentclaw.community.api.mcp_config_service import MCPConfigServiceProtocol
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol
from agentclaw.community.api.mcp_scoped_config_service import MCPScopedConfigServiceProtocol
from agentclaw.community.core.mcp.scoped_config_flow import MCPScopedConfigService
from agentclaw.community.core.mcp.effective_mcp_state_reader_protocol import (
    EffectiveMCPStateReaderProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotMCPConfigRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.bot.mcp import (
    ScopedMCPConfigRepositoryProtocol,
)


def _client():
    config = MagicMock()
    config.get_user_unified_config.return_value = {
        "headers": {"B": "2"}, "endpoint_env": "PROD",
        "transport_protocol": "SSE",
    }
    config.validate_effective_scoped_config.return_value = {"valid": True}
    bots = MagicMock()
    bots.list_live_bot_ids_by_owner.return_value = ["bot-x"]
    bots.get_by_id_and_owner.return_value = {"bot_id": "bot-x", "active_engine": "openclaw"}
    bot_configs = MagicMock()
    bot_configs.list_by_owner_and_server_code.return_value = {
        "bot-x": {"headers": {"A": "3"}}
    }
    market = MagicMock()
    market.get_mcp_detail.return_value = {
        "serverCode": "mcp.weather",
        "endpoints": [
            {"env": "PROD", "networkType": "OFFICE", "transportProtocol": "SSE"}
        ],
    }
    sync = MagicMock()
    sync.sync_mcp_detail_to_all_bots = AsyncMock(return_value={
        "success": True, "sync_results": [], "sync_summary": {}
    })
    command = MagicMock()
    capability = MagicMock()
    capability.effective_mcp_server_codes.return_value = {"mcp.weather"}

    class _Module(Module):
        def configure(self, binder):
            binder.bind(MCPConfigServiceProtocol, to=config)
            binder.bind(MCPMarketServiceProtocol, to=market)
            binder.bind(MCPSyncServiceProtocol, to=sync)
            binder.bind(BotRepository, to=bots)
            binder.bind(BotMCPConfigRepositoryProtocol, to=bot_configs)
            binder.bind(ScopedMCPConfigRepositoryProtocol, to=command)
            binder.bind(EffectiveMCPStateReaderProtocol, to=capability)
            binder.bind(
                MCPScopedConfigServiceProtocol,
                to=MCPScopedConfigService(
                    config_service=config,
                    bot_config_repo=bot_configs,
                    bot_repo=bots,
                    command_repo=command,
                    market_service=market,
                    sync_service=sync,
                    capability_reader=capability,
                ),
            )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="owner", staffId="owner", operatorName="Owner"
    )
    attach_injector(app, Injector([_Module()]))
    return TestClient(app), command


def test_internal_get_scoped_config_uses_authenticated_owner() -> None:
    client, _ = _client()

    response = client.get("/api/mcp/config-groups", params={"server_code": "mcp.weather"})

    assert response.status_code == 200
    assert response.json()["data"]["params"] == [
        {"key": "B", "value": "2", "bots": []},
        {"key": "A", "value": "3", "bots": ["bot-x"]},
    ]


def test_internal_post_scoped_config_writes_owner_snapshot() -> None:
    client, command = _client()

    response = client.post("/api/mcp/config-groups", json={
        "server_code": "mcp.weather",
        "endpoint_env": "PROD",
        "transport_protocol": "SSE",
        "params": [
            {"key": "B", "value": "2", "bots": []},
            {"key": "A", "value": "3", "bots": ["bot-x"]},
        ],
    })

    assert response.status_code == 200
    assert response.json()["data"]["params"][1] == {
        "key": "A", "value": "3", "bots": ["bot-x"]
    }
    assert command.replace.call_args.kwargs["user_id"] == "owner"

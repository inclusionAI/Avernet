"""Observable scoped MCP Header configuration behavior."""

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.mcp.scoped_config_flow import (
    HeaderGroup,
    read_scoped_config,
    write_scoped_config,
)
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.mcp.errors import McpConfigValueError
from agentclaw.community.core.models.mcp import BotMCPConfig, UserMCPConfig
from agentclaw.community.core.repository.implementations.bot.bot_mcp_config import (
    BotMCPConfigRepository,
)
from agentclaw.community.core.repository.implementations.bot.scoped_mcp_config import (
    ScopedMCPConfigRepository,
)
from agentclaw.community.core.repository.implementations.bot.user_mcp_config import (
    UserMCPConfigRepository,
)
from agentclaw.community.di.config import McpRuntimeCredentialsConfig
from agentclaw.community.utils.env_utils import get_current_env


def test_read_returns_explicit_groups_without_copying_inherited_values() -> None:
    config = MagicMock()
    config.get_user_unified_config.return_value = {
        "headers": {"X-Region": "east", "X-Trace": "on"},
        "endpoint_env": "PROD",
        "transport_protocol": "SSE",
    }
    bot_configs = MagicMock()
    bot_configs.list_by_owner_and_server_code.return_value = {
        "bot-x": {"headers": {"x-region": "east"}},
        "deleted-bot": {"headers": {"X-Ignored": "old"}},
    }
    bots = MagicMock()
    bots.list_live_bot_ids_by_owner.return_value = ["bot-x"]

    result = read_scoped_config(
        user_id="owner",
        server_code="mcp.weather",
        config_service=config,
        bot_config_repo=bot_configs,
        bot_repo=bots,
    )

    assert result.server_code == "mcp.weather"
    assert result.endpoint_env == "PROD"
    assert result.transport_protocol == "SSE"
    assert [(group.key, group.value, group.bots) for group in result.params] == [
        ("X-Region", "east", ()),
        ("X-Trace", "on", ()),
        ("x-region", "east", ("bot-x",)),
    ]


def test_write_persists_scoped_rules_and_keeps_offline_projection_best_effort(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    UserMCPConfig.__table__.create(engine)
    BotMCPConfig.__table__.create(engine)
    sessions = sessionmaker(bind=engine)

    class _Database:
        @contextmanager
        def transactional_orm_session(self):
            session = sessions()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        orm_session = transactional_orm_session

    db = _Database()
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                UserMCPConfig(
                    user_id="owner", server_code="mcp.weather", env=get_current_env(),
                    api_key="existing-secret",
                    extra_config='{"api_key":"existing-secret","headers":{"B":"2"},"endpoint_env":"PROD","transport_protocol":"SSE"}',
                ),
                BotMCPConfig(
                    owner_id="owner", bot_id="bot-x", server_code="mcp.weather",
                    env=get_current_env(), config='{"headers":{"A":"old"}}',
                ),
            ]
        )

    user_repo = UserMCPConfigRepository(db)
    bot_config_repo = BotMCPConfigRepository(db)
    bots = MagicMock()
    bots.list_live_bot_ids_by_owner.return_value = ["bot-x"]
    bots.get_by_id_and_owner.return_value = {
        "bot_id": "bot-x", "owner_id": "owner", "active_engine": "openclaw"
    }
    capability = MagicMock()
    capability.effective_mcp_server_codes.return_value = {"mcp.weather"}
    detail = {
        "serverCode": "mcp.weather", "runMode": "REMOTE",
        "endpoints": [
            {"env": "PROD", "networkType": "OFFICE", "transportProtocol": "SSE"}
        ],
    }
    center = MagicMock()
    center.get_mcp_detail.return_value = detail
    config = MCPConfigService(
        user_mcp_config_repo=user_repo, bot_mcp_config_repo=bot_config_repo,
        mcp_center=center, bot_repo=bots, capability_reader=capability,
        mcp_runtime_credentials=McpRuntimeCredentialsConfig(),
        secret_resolver=MagicMock(),
    )
    market = MagicMock()
    market.get_mcp_detail.return_value = detail
    sync = MagicMock()
    sync.sync_mcp_detail_to_all_bots = AsyncMock(return_value={
        "success": True,
        "sync_results": [{"bot_id": "bot-x", "synced": False, "reason": "OFFLINE"}],
        "sync_summary": {"affected": 1, "synced": 0, "offline": 1, "failed": 0},
    })
    monkeypatch.setattr(
        "agentclaw.community.core.mcp.services._defaults.get_default_mcp_servers",
        lambda _engine: [],
    )

    result = asyncio.run(write_scoped_config(
        user_id="owner", server_code="mcp.weather", endpoint_env="PROD",
        transport_protocol="SSE",
        params=(
            HeaderGroup(key="B", value="4", bots=()),
            HeaderGroup(key="A", value="3", bots=("bot-x",)),
        ),
        config_service=config, bot_config_repo=bot_config_repo, bot_repo=bots,
        command_repo=ScopedMCPConfigRepository(db), market_service=market,
        sync_service=sync, capability_reader=capability,
    ))

    override = bot_config_repo.get_by_bot_and_server_code(
        owner_id="owner", bot_id="bot-x", server_code="mcp.weather"
    )
    _, effective_headers, _, _ = config.build_mcp_sync_payload(
        user_id="owner", mcp_data=detail, bot_override=override
    )
    assert effective_headers == {"B": "4", "A": "3"}
    assert result.sync_summary["offline"] == 1
    assert UserMCPConfigRepository(db).get_by_user_and_server_code(
        "owner", "mcp.weather"
    )["api_key"] == "existing-secret"


def test_write_rejects_center_combination_without_any_installed_bot() -> None:
    config = MagicMock()
    bots = MagicMock()
    bots.list_live_bot_ids_by_owner.return_value = []
    market = MagicMock()
    market.get_mcp_detail.return_value = {
        "serverCode": "mcp.weather", "runMode": "REMOTE",
        "endpoints": [
            {"env": "PROD", "networkType": "OFFICE", "transportProtocol": "SSE"}
        ],
    }
    command = MagicMock()

    with pytest.raises(McpConfigValueError):
        asyncio.run(write_scoped_config(
            user_id="owner", server_code="mcp.weather", endpoint_env="PRE",
            transport_protocol="STREAMABLE_HTTP", params=(),
            config_service=config, bot_config_repo=MagicMock(), bot_repo=bots,
            command_repo=command, market_service=market,
            sync_service=MagicMock(), capability_reader=MagicMock(),
        ))

    command.replace.assert_not_called()


def test_write_rejects_platform_managed_header_before_db_mutation() -> None:
    bots = MagicMock()
    bots.list_live_bot_ids_by_owner.return_value = []
    center = MagicMock()
    detail = {
        "serverCode": "mcp.weather", "runMode": "REMOTE",
        "endpoints": [
            {"env": "PROD", "networkType": "OFFICE", "transportProtocol": "SSE"}
        ],
    }
    center.get_mcp_detail.return_value = detail
    config = MCPConfigService(
        user_mcp_config_repo=MagicMock(), bot_mcp_config_repo=MagicMock(),
        mcp_center=center, bot_repo=bots, capability_reader=MagicMock(),
        mcp_runtime_credentials=McpRuntimeCredentialsConfig(
            header_secrets={"mcp.weather": {"X-Managed": "secret-ref"}}
        ),
        secret_resolver=MagicMock(),
    )
    command = MagicMock()

    with pytest.raises(McpConfigValueError, match="managed"):
        asyncio.run(write_scoped_config(
            user_id="owner", server_code="mcp.weather", endpoint_env="PROD",
            transport_protocol="SSE",
            params=(HeaderGroup(key="x-managed", value="literal", bots=()),),
            config_service=config, bot_config_repo=MagicMock(), bot_repo=bots,
            command_repo=command, market_service=center,
            sync_service=MagicMock(), capability_reader=MagicMock(),
        ))

    command.replace.assert_not_called()


def test_write_reports_batch_projection_failure_without_reverting_committed_config() -> None:
    config = MagicMock()
    config.get_user_unified_config.return_value = {
        "headers": {"B": "4"}, "endpoint_env": "PROD", "transport_protocol": "SSE"
    }
    config.validate_scoped_headers.return_value = {"valid": True}
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
        "serverCode": "mcp.weather", "runMode": "REMOTE",
        "endpoints": [
            {"env": "PROD", "networkType": "OFFICE", "transportProtocol": "SSE"}
        ],
    }
    capability = MagicMock()
    capability.effective_mcp_server_codes.return_value = {"mcp.weather"}
    sync = MagicMock()
    sync.sync_mcp_detail_to_all_bots = AsyncMock(side_effect=RuntimeError("device unavailable"))
    command = MagicMock()

    result = asyncio.run(write_scoped_config(
        user_id="owner", server_code="mcp.weather", endpoint_env="PROD",
        transport_protocol="SSE",
        params=(HeaderGroup(key="A", value="3", bots=("bot-x",)),),
        config_service=config, bot_config_repo=bot_configs, bot_repo=bots,
        command_repo=command, market_service=market,
        sync_service=sync, capability_reader=capability,
    ))

    command.replace.assert_called_once()
    assert result.sync_summary == {
        "affected_bot_count": 1,
        "synced_count": 0,
        "offline_count": 0,
        "runtime_drift_count": 0,
        "failed_count": 1,
    }
    assert result.sync_results[0]["bot_id"] == "bot-x"
    assert result.sync_results[0]["synced"] is False


def test_write_rejects_user_protocol_not_reachable_by_owned_bot_engine() -> None:
    bots = MagicMock()
    bots.list_live_bot_ids_by_owner.return_value = ["bot-x"]
    bots.get_by_id_and_owner.return_value = {
        "bot_id": "bot-x", "active_engine": "openclaw"
    }
    detail = {
        "serverCode": "mcp.weather", "runMode": "REMOTE",
        "endpoints": [
            {"env": "PROD", "networkType": "OFFICE", "transportProtocol": "SSE"},
            {"env": "PROD", "networkType": "INTRANET", "transportProtocol": "STREAMABLE_HTTP"},
        ],
    }
    center = MagicMock()
    center.get_mcp_detail.return_value = detail
    user_repo = MagicMock()
    user_repo.get_by_user_and_server_code.return_value = None
    config = MCPConfigService(
        user_mcp_config_repo=user_repo, bot_mcp_config_repo=MagicMock(),
        mcp_center=center, bot_repo=bots, capability_reader=MagicMock(),
        mcp_runtime_credentials=McpRuntimeCredentialsConfig(),
        secret_resolver=MagicMock(),
    )
    bot_configs = MagicMock()
    bot_configs.list_by_owner_and_server_code.return_value = {}
    command = MagicMock()

    with pytest.raises(McpConfigValueError, match="STREAMABLE_HTTP"):
        asyncio.run(write_scoped_config(
            user_id="owner", server_code="mcp.weather", endpoint_env="PROD",
            transport_protocol="STREAMABLE_HTTP", params=(),
            config_service=config, bot_config_repo=bot_configs, bot_repo=bots,
            command_repo=command, market_service=center,
            sync_service=MagicMock(), capability_reader=MagicMock(),
        ))

    command.replace.assert_not_called()


def test_write_stores_rule_for_uninstalled_bot_without_device_probe() -> None:
    config = MagicMock()
    config.get_user_unified_config.return_value = {
        "headers": {}, "endpoint_env": "PROD", "transport_protocol": "SSE"
    }
    config.validate_scoped_headers.return_value = {"valid": True}
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
        "serverCode": "mcp.weather", "runMode": "REMOTE",
        "endpoints": [
            {"env": "PROD", "networkType": "OFFICE", "transportProtocol": "SSE"}
        ],
    }
    capability = MagicMock()
    capability.effective_mcp_server_codes.return_value = set()
    sync = MagicMock()
    command = MagicMock()

    result = asyncio.run(write_scoped_config(
        user_id="owner", server_code="mcp.weather", endpoint_env="PROD",
        transport_protocol="SSE",
        params=(HeaderGroup(key="A", value="3", bots=("bot-x",)),),
        config_service=config, bot_config_repo=bot_configs, bot_repo=bots,
        command_repo=command, market_service=market,
        sync_service=sync, capability_reader=capability,
    ))

    assert command.replace.call_args.kwargs["bot_headers"] == {"bot-x": {"A": "3"}}
    sync.sync_mcp_detail_to_all_bots.assert_not_called()
    assert result.sync_summary["affected_bot_count"] == 0

"""Tests for MCPSyncService — provider-blind delivery via resolver + dispatcher.

MCPSyncService no longer branches on ``device_provider``: it obtains a per-bot
``DeviceSyncPlugin`` from
``DeviceContextResolver.resolve_for_bot → DeviceSyncDispatcher.dispatch(ctx)``
and calls the MCP methods on it. The plugin decides *how* to deliver
(arca/baas per-MCP, teclaw whole-artifact, local no-op) — that's covered by
the plugin/contract tests. Here we assert the service routes correctly, maps
``DeviceNotBoundError`` / ``UnknownProviderError`` to the "missing device"
error, and that the multi-bot batch rolls back uniformly (Option B — a teclaw
delivery failure is NOT best-effort).

The plugin's MCP methods are **synchronous** (the service wraps them in
``asyncio.to_thread``), so the doubles use plain ``MagicMock``.
"""
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.devices.services.device_context import (
    DeviceContext,
    DeviceNotBoundError,
)
from agentclaw.community.core.caller_identity.models import McpCallType
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService


def _make_mcp_provider(mcps=None, all_mcps=None, summary=None):
    """Create a mock BotMCPProvider."""
    provider = MagicMock()
    provider.collect_bot_active_mcps.return_value = mcps or []
    provider.collect_bot_mcps.return_value = all_mcps or mcps or []
    provider.get_active_skill_sets_mcp_summary.return_value = summary or ([], [])
    return provider


def _make_plugin(**overrides):
    """A per-bot DeviceSyncPlugin double. MCP methods are SYNC (run via to_thread)."""
    plugin = MagicMock()
    plugin.sync_all_mcp_servers = MagicMock(return_value=True)
    plugin.sync_single_mcp = MagicMock(return_value=True)
    plugin.sync_remove_mcp = MagicMock(return_value=True)
    plugin.has_mcp = MagicMock(return_value=True)
    for k, v in overrides.items():
        setattr(plugin, k, v)
    return plugin


def _make_ctx(bot_id: str = "bot1", user_id: str = "u1") -> DeviceContext:
    """工厂:稳定 DeviceContext mock。"""
    return DeviceContext(
        provider="baas",
        conn_info={"engine_type": "openclaw"},
        binding_id=42,
        bot_id=bot_id,
        user_id=user_id,
    )


def _make_resolver_and_dispatcher(plugin=None, *, unavailable=False):
    """Build a (resolver, dispatcher, plugin) triple to inject."""
    resolver = MagicMock()
    dispatcher = MagicMock()
    if unavailable:
        resolver.resolve_for_bot.side_effect = DeviceNotBoundError("no binding")
    else:
        resolver.resolve_for_bot.return_value = _make_ctx()
    actual_plugin = plugin or _make_plugin()
    dispatcher.dispatch.return_value = actual_plugin
    return resolver, dispatcher, actual_plugin


def _make_sync_service(
    mcp_provider=None,
    passport_update=None,
    mcp_config_service=None,
    bot_repository=None,
    caller_identity_repository=None,
    mcp_center=None,
    resolver=None,
    dispatcher=None,
    plugin=None,
):
    """Create MCPSyncService with all dependencies mocked.

    ``resolver`` / ``dispatcher`` 替代旧 ``device_sync_supplier`` —— 取自
    ``_make_resolver_and_dispatcher`` 工厂。
    """
    provider = mcp_provider or _make_mcp_provider()
    service = MCPSyncService.__new__(MCPSyncService)
    service._mcp_provider_factory = lambda: provider
    service._mcp_provider_cached = provider
    service.mcp_center = mcp_center or MagicMock()
    service.user_mcp_config_repo = MagicMock()
    service.passport_update = passport_update or MagicMock()
    if isinstance(service.passport_update, MagicMock):
        snapshot = service.passport_update.query_agent_passport.return_value
        if isinstance(snapshot, MagicMock):
            existing_clis = service.passport_update.query_passport_clis.return_value
            service.passport_update.query_agent_passport.return_value = {
                "mcps": [],
                "clis": existing_clis if isinstance(existing_clis, list) else [],
            }
    service.mcp_config_service = mcp_config_service or MagicMock()
    service.mcp_config_service.build_mcp_sync_payload.return_value = (
        None, {}, "PROD", None
    )
    service.bot_repository = bot_repository or MagicMock()
    if caller_identity_repository is None:
        caller_identity_repository = MagicMock()
        caller_identity_repository.list_draft_call_types.return_value = {}
    service.caller_identity_repository = caller_identity_repository
    if resolver is None or dispatcher is None:
        _r, _d, _ = _make_resolver_and_dispatcher(plugin=plugin)
        service._resolver_provider = (lambda r=(resolver or _r): r)
        service._device_sync_dispatcher_provider = (lambda d=(dispatcher or _d): d)
    else:
        service._resolver_provider = lambda: resolver
        service._device_sync_dispatcher_provider = lambda: dispatcher
    return service


class TestRefreshMcpScope:
    """Test refresh_mcp_scope: scope declaration + passport update."""

    @pytest.mark.asyncio
    async def test_preserves_explicit_caller_identity_in_passport_scope(self):
        """A normal MCP refresh must not rewrite a configured caller MCP to owner."""
        caller_identity_repository = MagicMock()
        caller_identity_repository.list_draft_call_types.return_value = {
            "mcp.deepinsight": McpCallType.CALLER,
        }
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.return_value = {
            "id": 42,
            "active_engine": "claude_code",
            "template_type": "generalCC",
        }
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[
                {"server_code": "mcp.deepinsight", "name": "DeepInsight"},
                {"server_code": "mcp.owner", "name": "Owner MCP"},
            ]),
            passport_update=passport_update,
            bot_repository=bot_repository,
            caller_identity_repository=caller_identity_repository,
        )

        result = await service.refresh_mcp_scope(
            user_id="caller-1",
            entity_id="owner-1",
            bot_id="default",
            entity_type="staff",
            engine_type="claude_code",
        )

        assert result["success"] is True
        caller_identity_repository.list_draft_call_types.assert_called_once_with(
            42,
            "claude_code",
        )
        bot_repository.get_by_id_and_owner.assert_called_once_with("default", "owner-1")
        assert passport_update.update_passport.call_args.kwargs["resource_scope"][
            "mcp_items"
        ] == [
            {
                "mcp_code": "mcp.deepinsight",
                "mcp_name": "DeepInsight",
                "mcp_desc": None,
                "identity_mode": "caller",
            },
            {
                "mcp_code": "mcp.owner",
                "mcp_name": "Owner MCP",
                "mcp_desc": None,
                "identity_mode": "owner",
            },
        ]

    @pytest.mark.asyncio
    async def test_preserves_agentpass_only_caller_identity_without_sparse_row(self):
        """A history-only Caller MCP survives an unrelated MCP scope refresh."""
        caller_identity_repository = MagicMock()
        caller_identity_repository.list_draft_call_types.return_value = {}
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.return_value = {
            "id": 42,
            "active_engine": "openclaw",
        }
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        passport_update.query_agent_passport.return_value = {
            "mcps": [{"mcp_code": "mcp.history", "identity_mode": "caller"}],
            "clis": [],
        }
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.history"}]),
            passport_update=passport_update,
            bot_repository=bot_repository,
            caller_identity_repository=caller_identity_repository,
        )

        result = await service.refresh_mcp_scope(
            user_id="caller-1",
            entity_id="owner-1",
            bot_id="default",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result == {"success": True}
        assert passport_update.update_passport.call_args.kwargs["resource_scope"][
            "mcp_items"
        ] == [{"mcp_code": "mcp.history", "mcp_name": None, "mcp_desc": None, "identity_mode": "caller"}]

    @pytest.mark.asyncio
    async def test_logs_scope_payload_before_passport_update(self):
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        expected_resource_scope = {
            "mcp_codes": ["mcp.deepinsight"],
            "mcp_items": [
                {
                    "mcp_code": "mcp.deepinsight",
                    "mcp_name": "DeepInsight",
                    "mcp_desc": None,
                    "identity_mode": "owner",
                }
            ],
            "cli_items": [],
        }
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.return_value = None
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[
                {"server_code": "mcp.deepinsight", "name": "DeepInsight"},
            ]),
            passport_update=passport_update,
            bot_repository=bot_repository,
        )

        with patch(
            "agentclaw.community.core.mcp.services.sync_service.logger.info"
        ) as log_info:
            def verify_log_before_passport_call(**_: object) -> None:
                log_info.assert_any_call(
                    "[MCPSyncService] Passport update request: "
                    "operation=mcp_scope_refresh, bot_id=%s, user_id=%s, "
                    "resource_scope=%s, bot_name=%s, bot_desc=%s, engine_type=%s",
                    "bot-1",
                    "user-1",
                    expected_resource_scope,
                    None,
                    None,
                    "openclaw",
                )

            passport_update.update_passport.side_effect = verify_log_before_passport_call
            result = await service.refresh_mcp_scope(
                user_id="user-1",
                entity_id="owner-1",
                bot_id="bot-1",
                entity_type="staff",
                engine_type="openclaw",
            )

        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_updates_virtual_default_bot_without_caller_identity_lookup(self):
        """A default bot without an ac_bots row still refreshes owner MCP scope."""
        caller_identity_repository = MagicMock()
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.return_value = None
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[
                {"server_code": "mcp.default", "name": "Default MCP"},
            ]),
            passport_update=passport_update,
            bot_repository=bot_repository,
            caller_identity_repository=caller_identity_repository,
        )

        result = await service.refresh_mcp_scope(
            user_id="caller-1",
            entity_id="owner-1",
            bot_id="default",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result["success"] is True
        bot_repository.get_by_id_and_owner.assert_called_once_with("default", "owner-1")
        caller_identity_repository.list_draft_call_types.assert_not_called()
        assert passport_update.update_passport.call_args.kwargs["resource_scope"][
            "mcp_items"
        ] == [
            {
                "mcp_code": "mcp.default",
                "mcp_name": "Default MCP",
                "mcp_desc": None,
                "identity_mode": "owner",
            },
        ]

    @pytest.mark.asyncio
    async def test_does_not_update_passport_when_identity_scope_lookup_fails(self):
        """Do not replace a Passport scope when caller identity cannot be read."""
        caller_identity_repository = MagicMock()
        caller_identity_repository.list_draft_call_types.side_effect = RuntimeError(
            "database unavailable"
        )
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.return_value = {
            "id": 42,
            "active_engine": "claude_code",
        }
        passport_update = MagicMock()

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.deepinsight"}]),
            passport_update=passport_update,
            bot_repository=bot_repository,
            caller_identity_repository=caller_identity_repository,
        )

        result = await service.refresh_mcp_scope(
            user_id="owner-1",
            entity_id="entity-1",
            bot_id="bot-1",
            entity_type="staff",
            engine_type="claude_code",
        )

        assert result["success"] is False
        assert "查询 MCP 调用身份失败" in result["error"]
        passport_update.update_passport.assert_not_called()

    @pytest.mark.asyncio
    async def test_updates_passport_when_scope_ok(self):
        """When scope declaration succeeds, passport should be updated
        with all active MCP codes and the latest AgentPass CLI scope."""
        mcps = [
            {"server_code": "mcp.test.1", "name": "MCP 1"},
            {"server_code": "mcp.test.2", "name": "MCP 2"},
        ]
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = [
            {"cli_code": "cli.keep", "cli_name": "Keep CLI", "cli_desc": None}
        ]

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=mcps),
            passport_update=passport_update,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result["success"] is True
        passport_update.update_passport.assert_called_once()
        call_kwargs = passport_update.update_passport.call_args
        resource_scope = call_kwargs.kwargs["resource_scope"]
        assert "mcp.test.1" in resource_scope["mcp_codes"]
        assert "mcp.test.2" in resource_scope["mcp_codes"]
        assert resource_scope["cli_items"] == [
            {
                "cli_code": "cli.keep",
                "cli_name": "Keep CLI",
                "cli_desc": None,
                "identity_mode": "owner",
            }
        ]

    @pytest.mark.asyncio
    async def test_updates_passport_without_local_mcp_scope(self):
        """Local/stdin MCPs stay in device scope but are excluded from AgentPass."""
        mcps = [
            {"server_code": "mcp.remote.1", "name": "Remote MCP"},
            {"server_code": "hitl", "name": "HITL MCP"},
            {
                "server_code": "mcp.inline.local",
                "name": "Inline Local MCP",
                "runMode": "LOCAL",
                "stdioConfigs": [{"command": "node", "arguments": ["server.js"]}],
            },
        ]
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=mcps),
            passport_update=passport_update,
            resolver=resolver,
            dispatcher=dispatcher,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result["success"] is True
        plugin.sync_all_mcp_servers.assert_called_once_with(mcps)
        resource_scope = passport_update.update_passport.call_args.kwargs[
            "resource_scope"
        ]
        assert resource_scope["mcp_codes"] == ["mcp.remote.1"]

    @pytest.mark.asyncio
    async def test_merges_default_cli_items_when_syncing_aicoding_scope(self):
        """MCP scope refresh should not clear engine default CLI grants."""
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.return_value = {
            "id": 1,
            "bot_name": "AICoding Bot",
            "bot_desc": "desc",
            "active_engine": "aicoding",
            "template_type": "personalCoding",
        }

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
            bot_repository=bot_repository,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="aicoding",
        )

        assert result["success"] is True
        resource_scope = passport_update.update_passport.call_args.kwargs[
            "resource_scope"
        ]
        cli_codes = [item["cli_code"] for item in resource_scope["cli_items"]]
        assert cli_codes == [
            "adev-cli",
            "acli",
            "antcode-cli",
            "linke-cli",
            "linkw-cli",
            "qmx-invoke-cli",
            "serverless",
            "derisk-cli",
            "yuque-cli",
        ]

    @pytest.mark.asyncio
    async def test_merges_current_cli_items_before_default_cli_items(self):
        """Existing passport CLI metadata wins; defaults fill only missing codes."""
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = [
            {"cli_code": "adev-cli", "cli_name": "Custom Adev", "cli_desc": "kept"},
            {"cli_code": "custom-cli", "cli_name": "Custom", "cli_desc": None},
        ]
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.return_value = {
            "id": 1,
            "active_engine": "claude_code",
            "template_type": "personalCoding",
        }

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
            bot_repository=bot_repository,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="claude_code",
        )

        assert result["success"] is True
        cli_items = passport_update.update_passport.call_args.kwargs["resource_scope"][
            "cli_items"
        ]
        cli_codes = [item["cli_code"] for item in cli_items]
        assert cli_items[0] == {
            "cli_code": "adev-cli",
            "cli_name": "Custom Adev",
            "cli_desc": "kept",
            "identity_mode": "owner",
        }
        assert "custom-cli" in cli_codes
        assert cli_codes.count("adev-cli") == 1
        assert "antcode-cli" in cli_codes

    @pytest.mark.asyncio
    async def test_bot_active_engine_wins_over_default_refresh_engine_for_cli_scope(
        self,
    ):
        """Background refresh defaults MCP routing but uses bot engine for CLI."""
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.return_value = {
            "id": 1,
            "active_engine": "aicoding",
            "template_type": "personalCoding",
        }

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
            bot_repository=bot_repository,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1", entity_id="100", bot_id="bot1", entity_type="staff"
        )

        assert result["success"] is True
        resource_scope = passport_update.update_passport.call_args.kwargs[
            "resource_scope"
        ]
        cli_codes = [item["cli_code"] for item in resource_scope["cli_items"]]
        assert "antcode-cli" in cli_codes
        assert len(cli_codes) == 9

    @pytest.mark.asyncio
    async def test_does_not_update_passport_when_bot_metadata_query_fails(self, caplog):
        """Bot metadata is required to safely preserve default CLI scope."""
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        bot_repository = MagicMock()
        bot_repository.get_by_id_and_owner.side_effect = RuntimeError("db down")

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
            bot_repository=bot_repository,
        )

        with caplog.at_level("INFO", logger="start"):
            result = await service.refresh_mcp_scope(
                user_id="user1",
                entity_id="100",
                bot_id="bot1",
                entity_type="staff",
                engine_type="openclaw",
            )

        assert result["success"] is False
        assert "获取 bot 信息失败" in result["error"]
        passport_update.query_passport_clis.assert_not_called()
        passport_update.update_passport.assert_not_called()
        assert "[MCPSyncService] 获取 bot 信息失败，无法安全解析默认 CLI 范围, bot_id=bot1" in caplog.text

    @pytest.mark.asyncio
    async def test_does_not_update_passport_when_cli_scope_query_fails(self, caplog):
        """updatePassport requires full MCP+CLI scope; missing CLI scope aborts the update."""
        passport_update = MagicMock()
        passport_update.query_agent_passport.side_effect = RuntimeError("passport-token-secret")
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
        )

        with caplog.at_level("INFO", logger="start"):
            result = await service.refresh_mcp_scope(
                user_id="user1",
                entity_id="100",
                bot_id="bot1",
                entity_type="staff",
                engine_type="openclaw",
            )

        assert result["success"] is False
        assert result == {"success": False, "error": "查询 CLI 范围失败"}
        passport_update.update_passport.assert_not_called()
        logged = caplog.text
        assert "[MCPSyncService] 查询 CLI 范围失败" in logged
        assert "agentpass_mcp_scope_snapshot_failed" in logged
        assert "error_type=RuntimeError" in logged
        assert "duration_ms" in logged
        assert "passport-token-secret" not in logged

    @pytest.mark.asyncio
    async def test_passport_update_failure_logs_error_type_without_secret(self, caplog):
        """The overwrite failure remains diagnosable without external details."""
        passport_update = MagicMock()
        passport_update.update_passport.side_effect = RuntimeError(
            "passport-token-secret"
        )
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
        )

        with caplog.at_level("INFO", logger="start"):
            result = await service.refresh_mcp_scope(
                user_id="user1",
                entity_id="100",
                bot_id="bot1",
                entity_type="staff",
                engine_type="openclaw",
            )

        assert result == {"success": False, "error": "更新 passport 失败"}
        assert "[MCPSyncService] 更新 passport 失败" in caplog.text
        assert "agentpass_mcp_scope_update_requested" in caplog.text
        assert "agentpass_mcp_scope_update_failed" in caplog.text
        assert "stage=update" in caplog.text
        assert "error_type=RuntimeError" in caplog.text
        assert "duration_ms=" in caplog.text
        assert "passport-token-secret" not in caplog.text

    @pytest.mark.asyncio
    async def test_scope_builder_failure_logs_error_type_without_secret(self, caplog):
        """A malformed complete snapshot aborts before any overwrite request."""
        passport_update = MagicMock()
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
        )

        with patch(
            "agentclaw.community.core.mcp.services.sync_service.build_passport_resource_scope",
            side_effect=RuntimeError("passport-token-secret"),
        ), caplog.at_level("INFO", logger="start"):
            result = await service.refresh_mcp_scope(
                user_id="user1",
                entity_id="100",
                bot_id="bot1",
                entity_type="staff",
                engine_type="openclaw",
            )

        assert result == {"success": False, "error": "构建 Passport 完整范围失败"}
        passport_update.update_passport.assert_not_called()
        assert "agentpass_mcp_scope_snapshot_failed" in caplog.text
        assert "stage=build" in caplog.text
        assert "error_type=RuntimeError" in caplog.text
        assert "duration_ms=" in caplog.text
        assert "passport-token-secret" not in caplog.text

    @pytest.mark.asyncio
    async def test_returns_error_when_scope_fails(self):
        """When the device rejects the scope declaration, result reflects
        failure and passport is NOT updated."""
        passport_update = MagicMock()
        plugin = _make_plugin(sync_all_mcp_servers=MagicMock(return_value=False))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
            resolver=resolver,
            dispatcher=dispatcher,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result["success"] is False
        assert "白名单失败" in result["error"]
        passport_update.update_passport.assert_not_called()

    @pytest.mark.asyncio
    async def test_scope_returns_error_when_no_device(self):
        """No syncable device → the scope-declare leg surfaces the missing-conn
        error and passport is not updated."""
        passport_update = MagicMock()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(unavailable=True)
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.1"}]),
            passport_update=passport_update,
            resolver=resolver,
            dispatcher=dispatcher,
        )
        result = await service.refresh_mcp_scope(
            user_id="u1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="openclaw",
        )
        assert result["success"] is False
        assert "缺少设备连接信息" in result["error"]
        passport_update.update_passport.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_active_mcps_still_updates_passport(self):
        """Even with no active MCPs, scope_ok should clear passport."""
        passport_update = MagicMock()
        passport_update.query_passport_clis.return_value = []
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[]),
            passport_update=passport_update,
        )

        result = await service.refresh_mcp_scope(
            user_id="user1",
            entity_id="100",
            bot_id="bot1",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result["success"] is True
        passport_update.update_passport.assert_called_once()
        assert passport_update.update_passport.call_args.kwargs["resource_scope"] == {
            "mcp_codes": [],
            "mcp_items": [],
            "cli_items": [],
        }

    @pytest.mark.asyncio
    async def test_scope_routes_through_resolver_with_entity_id(self):
        """The scope-declare leg resolves the plugin via resolver keyed on
        entity_id (compose/collect entity), and passport still updates."""
        passport_update = MagicMock()
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(mcps=[{"server_code": "mcp.t.1"}]),
            passport_update=passport_update,
            resolver=resolver,
            dispatcher=dispatcher,
        )
        result = await service.refresh_mcp_scope(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="teclaw",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "100")
        dispatcher.dispatch.assert_called_once()
        plugin.sync_all_mcp_servers.assert_called_once()
        passport_update.update_passport.assert_called_once()


class TestPushMcpConfigs:
    """Test sync_mcp_details: detail sync only, no passport update."""

    @pytest.mark.asyncio
    async def test_never_updates_passport(self):
        """sync_mcp_details should NEVER touch passport — that's refresh_mcp_scope's job."""
        passport_update = MagicMock()
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(all_mcps=[{"server_code": "mcp.test.1"}]),
            passport_update=passport_update,
        )

        result = await service.sync_mcp_details(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="openclaw",
        )

        assert result["success"] is True
        passport_update.update_passport.assert_not_called()

    @pytest.mark.asyncio
    async def test_reports_failures(self):
        """When a per-MCP detail push fails, result reflects failures.

        Keyed on server_code (not call order) — the loop runs concurrently."""
        mcps = [
            {"server_code": "mcp.test.1", "name": "MCP 1"},
            {"server_code": "mcp.test.2", "name": "MCP 2"},
        ]

        def _push(mcp_data, **kwargs):
            return mcp_data.get("server_code") != "mcp.test.2"

        plugin = _make_plugin(sync_single_mcp=MagicMock(side_effect=_push))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(all_mcps=mcps),
            resolver=resolver,
            dispatcher=dispatcher,
        )

        result = await service.sync_mcp_details(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="openclaw",
        )

        assert result["success"] is False
        assert result["success_count"] == 1
        assert result["failed_count"] == 1
        assert "mcp.test.1" in result["synced_server_codes"]

    @pytest.mark.asyncio
    async def test_bulk_routes_through_resolver_with_entity_id(self):
        """The bulk detail push resolves one plugin per bulk call (resolver keyed
        on entity_id) and pushes each MCP through it."""
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(all_mcps=[{"server_code": "mcp.t.1"}]),
            resolver=resolver,
            dispatcher=dispatcher,
        )
        result = await service.sync_mcp_details(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="teclaw",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "100")
        dispatcher.dispatch.assert_called_once()
        plugin.sync_single_mcp.assert_called_once()

    @pytest.mark.asyncio
    async def test_enriches_local_stdio_mcp_before_detail_push(self):
        """Bulk detail push preserves local stdio metadata from MCP lookup."""
        captured = {}

        def _push(mcp_data, **kwargs):
            captured.update(mcp_data)
            return True

        plugin = _make_plugin(sync_single_mcp=MagicMock(side_effect=_push))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        mcp_center = MagicMock()
        mcp_center.get_mcp_list.return_value = {
            "success": True,
            "data": [
                {
                    "serverCode": "mcp.local.demo",
                    "server_code": "mcp.local.demo",
                    "name": "Local Demo",
                    "runMode": "LOCAL",
                    "stdioConfigs": [{"command": "node", "arguments": ["server.js"]}],
                }
            ],
        }

        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(all_mcps=[{"server_code": "mcp.local.demo"}]),
            mcp_center=mcp_center,
            resolver=resolver,
            dispatcher=dispatcher,
        )

        result = await service.sync_mcp_details(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="openclaw",
        )

        assert result["success"] is True
        assert captured["runMode"] == "LOCAL"
        assert captured["stdioConfigs"][0]["command"] == "node"

    @pytest.mark.asyncio
    async def test_offline_device_raises_to_error(self):
        """No syncable device → sync_mcp_details returns a missing-connection error."""
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(unavailable=True)
        service = _make_sync_service(
            mcp_provider=_make_mcp_provider(all_mcps=[{"server_code": "mcp.t.1"}]),
            resolver=resolver,
            dispatcher=dispatcher,
        )
        result = await service.sync_mcp_details(
            user_id="user1", entity_id="100", bot_id="bot1",
            entity_type="staff", engine_type="openclaw",
        )
        assert result["success"] is False
        assert "缺少设备连接信息" in result["error"]


class TestPushMcpConfig:
    """Test sync_mcp_detail (single MCP to single bot)."""

    @pytest.mark.asyncio
    async def test_success(self):
        service = _make_sync_service()
        result = await service.sync_mcp_detail(
            user_id="u1", mcp_data={"server_code": "mcp.test.1"}, bot_id="bot1",
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_offline_device_returns_error(self):
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(unavailable=True)
        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.sync_mcp_detail(
            user_id="u1", mcp_data={"server_code": "mcp.test.1"}, bot_id="bot1",
        )
        assert result["success"] is False
        assert "缺少设备连接信息" in result["error"]

    @pytest.mark.asyncio
    async def test_routes_through_resolver(self):
        """Single add resolves the plugin via resolver keyed on user_id and pushes
        the MCP through it (the plugin decides per-container delivery)."""
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.sync_mcp_detail(
            user_id="u1", mcp_data={"server_code": "mcp.x"}, bot_id="bot1",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "u1")
        dispatcher.dispatch.assert_called_once()
        plugin.sync_single_mcp.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolves_plugin_by_entity_id_when_given(self):
        """When entity_id is supplied (shared/org bot), resolver receives
        entity_id — so the teclaw whole-artifact compose targets the bot owner's
        entity, not the (possibly different) caller user_id. Consistent with the
        remove/scope/bulk/batch paths."""
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.sync_mcp_detail(
            user_id="viewer", entity_id="owner_entity",
            mcp_data={"server_code": "mcp.x"}, bot_id="bot1",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "owner_entity")
        # user_id (not entity_id) still drives the per-user payload merge.
        assert service.mcp_config_service.build_mcp_sync_payload.call_args.kwargs["user_id"] == "viewer"

    @pytest.mark.asyncio
    async def test_delivery_failure_surfaces_error(self):
        """A failed device push surfaces success=False so add_mcp_to_skill_set
        rolls back."""
        plugin = _make_plugin(sync_single_mcp=MagicMock(return_value=False))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.sync_mcp_detail(
            user_id="u1", mcp_data={"server_code": "mcp.x"}, bot_id="bot1",
        )

        assert result["success"] is False
        assert "推送 MCP 配置失败" in result["error"]


class TestRemoveMcpConfig:
    """Test remove_mcp_detail (single MCP removal from bot)."""

    @pytest.mark.asyncio
    async def test_success(self):
        service = _make_sync_service()
        result = await service.remove_mcp_detail(
            server_code="mcp.test.1", bot_id="bot1", user_id="u1",
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_offline_device_returns_error(self):
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(unavailable=True)
        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.remove_mcp_detail(
            server_code="mcp.test.1", bot_id="bot1", user_id="u1",
        )
        assert result["success"] is False
        assert "缺少设备连接信息" in result["error"]

    @pytest.mark.asyncio
    async def test_routes_through_resolver(self):
        """Remove resolves the plugin via resolver(bot_id, user_id) and calls
        sync_remove_mcp on it."""
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)

        service = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        result = await service.remove_mcp_detail(
            server_code="mcp.x", bot_id="bot1", user_id="u1",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "u1")
        dispatcher.dispatch.assert_called_once()
        plugin.sync_remove_mcp.assert_called_once_with("mcp.x")


class TestSyncMcpDetailToAllBots:
    """Multi-bot batch: uniform probe→push, provider-blind, Option B rollback."""

    def _service_with_one_bot(self, *, resolver, dispatcher):
        bot_repo = MagicMock()
        bot_repo.list_by_entity.return_value = (1, [{"bot_id": "bot1"}])
        return _make_sync_service(
            bot_repository=bot_repo,
            resolver=resolver,
            dispatcher=dispatcher,
        )

    @pytest.mark.asyncio
    async def test_routes_each_bot_through_resolver(self):
        """Each bot is resolved via resolver(bot_id, entity_id), probed, pushed."""
        plugin = _make_plugin()
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        service = self._service_with_one_bot(resolver=resolver, dispatcher=dispatcher)

        result = await service.sync_mcp_detail_to_all_bots(
            user_id="u1", server_code="mcp.x", mcp_data={"server_code": "mcp.x"},
            entity_id="100", entity_type="staff",
        )

        assert result["success"] is True
        assert result["sync_results"][0]["bot_id"] == "bot1"
        assert result["sync_results"][0]["synced"] is True
        resolver.resolve_for_bot.assert_called_once_with("bot1", "100")
        dispatcher.dispatch.assert_called_once()
        plugin.has_mcp.assert_called_once_with("mcp.x")
        plugin.sync_single_mcp.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_devices_fail_returns_failure(self):
        """When every device that has the MCP fails to sync, the batch reports
        failure (→ caller rolls back). Uniform across providers (Option B)."""
        plugin = _make_plugin(sync_single_mcp=MagicMock(return_value=False))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        service = self._service_with_one_bot(resolver=resolver, dispatcher=dispatcher)

        result = await service.sync_mcp_detail_to_all_bots(
            user_id="u1", server_code="mcp.x", mcp_data={"server_code": "mcp.x"},
            entity_id="100", entity_type="staff",
        )

        assert result["success"] is False
        assert result["sync_results"][0]["synced"] is False

    @pytest.mark.asyncio
    async def test_device_without_mcp_is_skipped(self):
        """A device that doesn't report the MCP is skipped and does not count
        toward the rollback gate (success stays True)."""
        plugin = _make_plugin(has_mcp=MagicMock(return_value=False))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        service = self._service_with_one_bot(resolver=resolver, dispatcher=dispatcher)

        result = await service.sync_mcp_detail_to_all_bots(
            user_id="u1", server_code="mcp.x", mcp_data={"server_code": "mcp.x"},
            entity_id="100", entity_type="staff",
        )

        assert result["success"] is True
        assert result["sync_results"][0]["synced"] is False
        plugin.sync_single_mcp.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_without_device_is_skipped(self):
        """A bot with no syncable device is skipped (best-effort) and does not
        fail the batch."""
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(unavailable=True)
        service = self._service_with_one_bot(resolver=resolver, dispatcher=dispatcher)

        result = await service.sync_mcp_detail_to_all_bots(
            user_id="u1", server_code="mcp.x", mcp_data={"server_code": "mcp.x"},
            entity_id="100", entity_type="staff",
        )

        assert result["success"] is True
        assert result["sync_results"][0]["reason"] == "缺少设备连接信息"


class TestSyncMcpDetailsForBot:
    """sync_mcp_details_for_bot: resolve once, then fan out — and stay off the loop.

    The bug this entrypoint exists for: looping ``sync_mcp_detail`` re-resolved the
    device per MCP, and ``resolve_for_bot`` is synchronous with a blocking ws-info
    round trip inside. Running it on the event loop serialized the caller's fan-out
    no matter how wide the semaphore was.
    """

    def _entries(self, n):
        return [{"server_code": f"mcp.s{i}"} for i in range(n)]

    @pytest.mark.asyncio
    async def test_device_is_resolved_once_for_the_whole_batch(self):
        resolver, dispatcher, plugin = _make_resolver_and_dispatcher()
        svc = _make_sync_service(resolver=resolver, dispatcher=dispatcher)

        result = await svc.sync_mcp_details_for_bot(
            user_id="u1", mcp_entries=self._entries(14), bot_id="bot1",
        )

        assert result["success"] is True
        # 14 MCPs, one device resolution — not 14.
        assert resolver.resolve_for_bot.call_count == 1
        assert dispatcher.dispatch.call_count == 1
        assert plugin.sync_single_mcp.call_count == 14

    @pytest.mark.asyncio
    async def test_blocking_work_is_kept_off_the_event_loop(self):
        """resolve_for_bot and build_mcp_sync_payload both block; if either ran
        inline the fan-out below could never overlap."""
        import threading

        loop_thread = threading.get_ident()
        threads: dict[str, int] = {}

        ctx = _make_ctx()

        def record_resolve(*_a, **_k):
            threads["resolve"] = threading.get_ident()
            return ctx

        def record_payload(**_k):
            threads["payload"] = threading.get_ident()
            return (None, {}, "PROD", None)

        resolver, dispatcher, plugin = _make_resolver_and_dispatcher()
        resolver.resolve_for_bot.side_effect = record_resolve
        svc = _make_sync_service(resolver=resolver, dispatcher=dispatcher)
        svc.mcp_config_service.build_mcp_sync_payload.side_effect = record_payload

        await svc.sync_mcp_details_for_bot(
            user_id="u1", mcp_entries=self._entries(2), bot_id="bot1",
        )

        assert threads["resolve"] != loop_thread
        assert threads["payload"] != loop_thread

    @pytest.mark.asyncio
    async def test_deliveries_overlap_up_to_the_bound(self):
        import threading
        import time

        from agentclaw.community.core.mcp.services import sync_service as mod

        lock = threading.Lock()
        inflight = peak = 0

        def slow_push(*_a, **_k):
            nonlocal inflight, peak
            with lock:
                inflight += 1
                peak = max(peak, inflight)
            time.sleep(0.02)
            with lock:
                inflight -= 1
            return True

        plugin = _make_plugin(sync_single_mcp=MagicMock(side_effect=slow_push))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        svc = _make_sync_service(resolver=resolver, dispatcher=dispatcher)

        bound = mod._DESIRED_STATE_DETAIL_CONCURRENCY
        result = await svc.sync_mcp_details_for_bot(
            user_id="u1", mcp_entries=self._entries(bound * 2), bot_id="bot1",
        )

        assert result["success"] is True
        # Serial delivery would peak at 1. The bound caps it; the shared
        # to_thread pool may cap it lower on a small box, hence the range.
        assert 1 < peak <= bound

    @pytest.mark.asyncio
    async def test_failed_entry_reports_its_server_code(self):
        pushed = []

        def push(mcp_data, **_k):
            code = mcp_data["server_code"]
            pushed.append(code)
            return code != "mcp.s1"

        plugin = _make_plugin(sync_single_mcp=MagicMock(side_effect=push))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        svc = _make_sync_service(resolver=resolver, dispatcher=dispatcher)

        result = await svc.sync_mcp_details_for_bot(
            user_id="u1", mcp_entries=self._entries(3), bot_id="bot1",
        )

        assert result["success"] is False
        assert "mcp.s1" in result["error"]
        # Fan-out attempts every entry; only the verdict is negative.
        assert sorted(pushed) == ["mcp.s0", "mcp.s1", "mcp.s2"]

    @pytest.mark.asyncio
    async def test_raising_entry_fails_the_batch_without_escaping(self):
        def push(mcp_data, **_k):
            if mcp_data["server_code"] == "mcp.s1":
                raise RuntimeError("device refused the payload")
            return True

        plugin = _make_plugin(sync_single_mcp=MagicMock(side_effect=push))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        svc = _make_sync_service(resolver=resolver, dispatcher=dispatcher)

        result = await svc.sync_mcp_details_for_bot(
            user_id="u1", mcp_entries=self._entries(3), bot_id="bot1",
        )

        assert result["success"] is False
        assert "mcp.s1" in result["error"]

    @pytest.mark.asyncio
    async def test_cancellation_propagates_instead_of_reporting_failure(self):
        import asyncio

        def push(mcp_data, **_k):
            if mcp_data["server_code"] == "mcp.s1":
                raise asyncio.CancelledError()
            return True

        plugin = _make_plugin(sync_single_mcp=MagicMock(side_effect=push))
        resolver, dispatcher, _ = _make_resolver_and_dispatcher(plugin=plugin)
        svc = _make_sync_service(resolver=resolver, dispatcher=dispatcher)

        with pytest.raises(asyncio.CancelledError):
            await svc.sync_mcp_details_for_bot(
                user_id="u1", mcp_entries=self._entries(2), bot_id="bot1",
            )

    @pytest.mark.asyncio
    async def test_empty_batch_does_not_touch_the_device(self):
        resolver, dispatcher, plugin = _make_resolver_and_dispatcher()
        svc = _make_sync_service(resolver=resolver, dispatcher=dispatcher)

        result = await svc.sync_mcp_details_for_bot(
            user_id="u1", mcp_entries=[], bot_id="bot1",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_not_called()
        plugin.sync_single_mcp.assert_not_called()

    @pytest.mark.asyncio
    async def test_unbound_device_reports_the_missing_connection_error(self):
        resolver, dispatcher, plugin = _make_resolver_and_dispatcher(unavailable=True)
        svc = _make_sync_service(resolver=resolver, dispatcher=dispatcher)

        result = await svc.sync_mcp_details_for_bot(
            user_id="u1", mcp_entries=self._entries(2), bot_id="bot1",
        )

        assert result["success"] is False
        assert "缺少设备连接信息" in result["error"]
        plugin.sync_single_mcp.assert_not_called()

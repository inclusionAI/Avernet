"""Tests for the engine abort path: BotService.abort -> AsyncChatClient.chat_abort.

These tests verify the incremental wiring that makes ``BotRequestWorker``'
``_engine_abort_notifier`` actually reach the engine via ``chat.abort``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotChatContext,
    WsConnectionInfo,
)
from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
    NotConnectedError,
)
from secbaas.community.core.service.bot_run._baas_service import (
    BaasBotService,
    BaasBotServiceConfig,
)
from secbaas.community.core.service.bot_run._claw_service import (
    BotServiceConfig,
    ClawBotService,
)
from secbaas.community.core.service.bot_run._engine_adapter_registry import (
    BotEngineAdapterRegistry,
)
from secbaas.community.plugins.eval_env.stub import NoopEvalConsistencyCheck
from secbaas.community.spi.secret import SecretStorePlugin


@pytest.fixture
def binding_info() -> BotBindingInfo:
    return BotBindingInfo(
        bot_id="bot-1",
        entity_id="entity-1",
        sandbox_id="sandbox-1",
        device_id="device-1",
        device_provider="baas",
        binding_id=1,
        device_props={"tenant": "test-tenant"},
        bot_type="personal",
        engine_type="openclaw",
        baas_session_id=None,
    )


def _conn_info() -> WsConnectionInfo:
    return WsConnectionInfo(
        ws_url="wss://proxy/proxypass/target/api/openclaw/ws",
        token="token-1",
        target="target",
        expires_at=datetime.now(),
    )


async def test_async_chat_client_chat_abort_delegates_to_ws_client():
    """AsyncChatClient.chat_abort delegates to BotWebSocketClient.chat_abort."""
    client = AsyncChatClient(uri="wss://example.com/ws")
    client._client = MagicMock()
    client._client.connected = True
    client._client.chat_abort = AsyncMock(return_value={"ok": True})

    result = await client.chat_abort(session_key="sess-1", run_id="run-1")

    assert result == {"ok": True}
    client._client.chat_abort.assert_awaited_once_with(
        session_key="sess-1",
        run_id="run-1",
    )


async def test_async_chat_client_chat_abort_raises_when_not_connected():
    """AsyncChatClient.chat_abort raises NotConnectedError when not connected."""
    client = AsyncChatClient(uri="wss://example.com/ws")

    with pytest.raises(NotConnectedError, match="Not connected"):
        await client.chat_abort(session_key="sess-1")


async def test_baas_bot_service_abort_sends_chat_abort(binding_info: BotBindingInfo):
    """BaasBotService.abort resolves connection and sends chat.abort."""
    pool = MagicMock()
    ws_client = MagicMock()
    ws_client.chat_abort = AsyncMock(return_value={"ok": True})
    pool.get = AsyncMock(return_value=ws_client)

    resolver = MagicMock()
    resolver.dispatch_bot_ws_conn_info = AsyncMock(return_value=_conn_info())

    session_service = MagicMock()
    service = BaasBotService(
        config=BaasBotServiceConfig(),
        client_pool=pool,
        wss_resolver=resolver,
        session_service=session_service,
        engine_adapter_registry=BotEngineAdapterRegistry({}),
        eval_consistency_check=NoopEvalConsistencyCheck(),
    )

    await service.abort(
        session_id="sess-1",
        binding_info=binding_info,
    )

    pool.get.assert_awaited_once_with(
        "target",
        "wss://proxy/proxypass/target/api/openclaw/ws",
        {"x-proxypass-token": "token-1"},
        session_key="sess-1",
        token_minter=ANY,
    )
    ws_client.chat_abort.assert_awaited_once_with(
        session_key="sess-1",
    )


async def test_baas_bot_service_abort_uses_context_tenant(
    binding_info: BotBindingInfo,
):
    """context.tenant 被透传到 WS resolver，避免 tenant 为空导致 BotNotFound。"""
    pool = MagicMock()
    ws_client = MagicMock()
    ws_client.chat_abort = AsyncMock(return_value={"ok": True})
    pool.get = AsyncMock(return_value=ws_client)

    resolver = MagicMock()
    resolver.dispatch_bot_ws_conn_info = AsyncMock(return_value=_conn_info())

    session_service = MagicMock()
    service = BaasBotService(
        config=BaasBotServiceConfig(),
        client_pool=pool,
        wss_resolver=resolver,
        session_service=session_service,
        engine_adapter_registry=BotEngineAdapterRegistry({}),
        eval_consistency_check=NoopEvalConsistencyCheck(),
    )

    no_tenant_binding = replace(binding_info, device_props={})
    context = BotChatContext.from_api_key(
        api_key_prefix="key-1",
        app_id="app-1",
        app_type="baas",
        tenant="run-tenant",
    )

    await service.abort(
        session_id="sess-1",
        binding_info=no_tenant_binding,
        context=context,
    )

    resolver.dispatch_bot_ws_conn_info.assert_awaited_once()
    call_kw = resolver.dispatch_bot_ws_conn_info.await_args.kwargs
    assert call_kw["tenant"] == "run-tenant"


async def test_baas_bot_service_abort_logs_on_resolution_failure(
    binding_info: BotBindingInfo,
):
    """BaasBotService.abort is best-effort: resolution failure is logged."""
    pool = MagicMock()
    resolver = MagicMock()
    resolver.dispatch_bot_ws_conn_info = AsyncMock(side_effect=RuntimeError("boom"))
    session_service = MagicMock()
    service = BaasBotService(
        config=BaasBotServiceConfig(),
        client_pool=pool,
        wss_resolver=resolver,
        session_service=session_service,
        engine_adapter_registry=BotEngineAdapterRegistry({}),
        eval_consistency_check=NoopEvalConsistencyCheck(),
    )

    await service.abort(
        session_id="sess-1",
        binding_info=binding_info,
    )

    assert pool.get.called is False


async def test_baas_bot_service_abort_logs_on_chat_abort_failure(
    binding_info: BotBindingInfo,
):
    """BaasBotService.abort swallows engine errors."""
    pool = MagicMock()
    ws_client = MagicMock()
    ws_client.chat_abort = AsyncMock(side_effect=RuntimeError("engine boom"))
    pool.get = AsyncMock(return_value=ws_client)

    resolver = MagicMock()
    resolver.dispatch_bot_ws_conn_info = AsyncMock(return_value=_conn_info())
    session_service = MagicMock()
    service = BaasBotService(
        config=BaasBotServiceConfig(),
        client_pool=pool,
        wss_resolver=resolver,
        session_service=session_service,
        engine_adapter_registry=BotEngineAdapterRegistry({}),
        eval_consistency_check=NoopEvalConsistencyCheck(),
    )

    await service.abort(
        session_id="sess-1",
        binding_info=binding_info,
    )

    ws_client.chat_abort.assert_awaited_once()


async def test_claw_bot_service_abort_sends_chat_abort(binding_info: BotBindingInfo):
    """ClawBotService.abort resolves connection and sends chat.abort."""
    secret_store = MagicMock(spec=SecretStorePlugin)
    secret_store.generate_proxy_token = MagicMock()
    secret_store.generate_proxy_token.return_value = "test-token"
    pool = MagicMock()
    ws_client = MagicMock()
    ws_client.chat_abort = AsyncMock(return_value={"ok": True})
    pool.get = AsyncMock(return_value=ws_client)

    service = ClawBotService(
        config=BotServiceConfig(
            proxy_base_url="https://proxy",
            proxy_ws_base_url="wss://proxy",
            adapter_port=20003,
            request_timeout=30,
        ),
        client_pool=pool,
        secret_store=secret_store,
        engine_adapter_registry=BotEngineAdapterRegistry({}),
    )

    await service.abort(
        session_id="sess-1",
        binding_info=binding_info,
    )

    pool.get.assert_awaited_once_with(
        "sandbox-1",
        "wss://proxy/proxypass/ARCA_sandbox-1:20003/api/openclaw/ws",
        {"x-proxypass-token": "test-token"},
        session_key="sess-1",
        token_minter=ANY,
    )
    ws_client.chat_abort.assert_awaited_once_with(
        session_key="sess-1",
    )


async def test_claw_bot_service_abort_without_sandbox_id_is_noop(
    binding_info: BotBindingInfo,
):
    """ClawBotService.abort requires sandbox_id; missing it is a no-op."""
    secret_store = MagicMock(spec=SecretStorePlugin)
    pool = MagicMock()
    service = ClawBotService(
        config=BotServiceConfig(
            proxy_base_url="https://proxy",
            proxy_ws_base_url="wss://proxy",
            adapter_port=20003,
        ),
        client_pool=pool,
        secret_store=secret_store,
        engine_adapter_registry=BotEngineAdapterRegistry({}),
    )
    no_sandbox = replace(binding_info, sandbox_id=None)

    await service.abort(
        session_id="sess-1",
        binding_info=no_sandbox,
    )

    assert pool.get.called is False


async def test_claw_bot_service_abort_swallows_engine_error(
    binding_info: BotBindingInfo,
):
    """ClawBotService.abort swallows engine errors."""
    secret_store = MagicMock(spec=SecretStorePlugin)
    secret_store.generate_proxy_token = MagicMock()
    secret_store.generate_proxy_token.return_value = "test-token"
    pool = MagicMock()
    ws_client = MagicMock()
    ws_client.chat_abort = AsyncMock(side_effect=RuntimeError("engine boom"))
    pool.get = AsyncMock(return_value=ws_client)

    service = ClawBotService(
        config=BotServiceConfig(
            proxy_base_url="https://proxy",
            proxy_ws_base_url="wss://proxy",
            adapter_port=20003,
        ),
        client_pool=pool,
        secret_store=secret_store,
        engine_adapter_registry=BotEngineAdapterRegistry({}),
    )

    await service.abort(
        session_id="sess-1",
        binding_info=binding_info,
    )

    ws_client.chat_abort.assert_awaited_once()


# ── R3-3（PR #2743）：engineAbort 结局的调用方消费 ──────────────────────────
# ok=True 的含义限定：A2A 传输下只代表传输任务已取消；引擎停止确认以
# engineAbort 为准——非 confirmed 必须落结构化告警（可观测），v3 形态
# 返回（无 a2a/engineAbort 键）不告警，行为零变化。


async def test_baas_bot_service_abort_warns_on_unconfirmed_engine_abort(
    binding_info: BotBindingInfo, caplog
):
    """验收 6：传输取消成功但引擎停止未确认→ 结构化告警。"""
    import logging

    pool = MagicMock()
    ws_client = MagicMock()
    ws_client.chat_abort = AsyncMock(
        return_value={
            "ok": True,
            "a2a": True,
            "task": {"id": "t", "state": "TASK_STATE_CANCELED"},
            "engineAbort": "timeout",
        }
    )
    pool.get = AsyncMock(return_value=ws_client)

    resolver = MagicMock()
    resolver.dispatch_bot_ws_conn_info = AsyncMock(return_value=_conn_info())
    service = BaasBotService(
        config=BaasBotServiceConfig(),
        client_pool=pool,
        wss_resolver=resolver,
        session_service=MagicMock(),
        engine_adapter_registry=BotEngineAdapterRegistry({}),
        eval_consistency_check=NoopEvalConsistencyCheck(),
    )

    with caplog.at_level(logging.WARNING):
        await service.abort(session_id="sess-1", binding_info=binding_info)

    warned = [r for r in caplog.records if "engine stop unconfirmed" in r.message]
    assert warned, caplog.text
    assert (
        "engineAbort" in warned[0].getMessage() or "timeout" in warned[0].getMessage()
    )


async def test_baas_bot_service_abort_confirmed_and_v3_shapes_do_not_warn(
    binding_info: BotBindingInfo, caplog
):
    """confirmed 结局与 v3 形态返回（无 a2a 键）都不告警。"""
    import logging

    resolver = MagicMock()
    resolver.dispatch_bot_ws_conn_info = AsyncMock(return_value=_conn_info())

    for shape in ({"ok": True}, {"ok": True, "a2a": True, "engineAbort": "confirmed"}):
        pool = MagicMock()
        ws_client = MagicMock()
        ws_client.chat_abort = AsyncMock(return_value=shape)
        pool.get = AsyncMock(return_value=ws_client)
        service = BaasBotService(
            config=BaasBotServiceConfig(),
            client_pool=pool,
            wss_resolver=resolver,
            session_service=MagicMock(),
            engine_adapter_registry=BotEngineAdapterRegistry({}),
            eval_consistency_check=NoopEvalConsistencyCheck(),
        )
        caplog.clear()
        await service.abort(session_id="sess-1", binding_info=binding_info)
        assert not [
            r for r in caplog.records if "engine stop unconfirmed" in r.message
        ], shape


async def test_claw_bot_service_abort_warns_on_unconfirmed_engine_abort(
    binding_info: BotBindingInfo, caplog
):
    """验收 6：Claw 侧同样消费 engineAbort——非 confirmed 落告警。"""
    import logging

    secret_store = MagicMock(spec=SecretStorePlugin)
    secret_store.generate_proxy_token = MagicMock(return_value="test-token")
    pool = MagicMock()
    ws_client = MagicMock()
    ws_client.chat_abort = AsyncMock(
        return_value={
            "ok": True,
            "a2a": True,
            "task": {"id": "t", "state": "TASK_STATE_CANCELED"},
            "engineAbort": "refused",
        }
    )
    pool.get = AsyncMock(return_value=ws_client)

    service = ClawBotService(
        config=BotServiceConfig(
            proxy_base_url="https://proxy",
            proxy_ws_base_url="wss://proxy",
            adapter_port=20003,
        ),
        client_pool=pool,
        secret_store=secret_store,
        engine_adapter_registry=BotEngineAdapterRegistry({}),
    )

    with caplog.at_level(logging.WARNING):
        await service.abort(session_id="sess-1", binding_info=binding_info)

    warned = [r for r in caplog.records if "engine stop unconfirmed" in r.message]
    assert warned, caplog.text
    assert "refused" in warned[0].getMessage()

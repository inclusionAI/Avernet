"""Conformance suite for SessionInitiator (Rule 25).

2026-09-15 统一化: session 创建链路归一为 ``OpenApiBotSessionInitiator``
(自 corp 列下沉, BaaS Open API)。Conformance 直接验证:
- 唯一实现以 stub ``OpenApiBotPort`` 经 Protocol 完成发现→session 流程;
- 社区/单机 profile 下 (无 OpenApiBotPort) 组合根注入
  ``UnavailableSessionInitiator`` fail-closed 占位 — 同样满足 Protocol,
  调用即抛可读错误。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    OpenApiBotSessionInitiator,
    SessionInitiator,
    UnavailableSessionInitiator,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
)

_DT = "2026-09-14"


def _make_task(*, title: str = "Contract task") -> DiscoveredTask:
    return DiscoveredTask(
        task_id="td-contract-1",
        bot_id="bot-1",
        owner_id="owner-1",
        dt=_DT,
        title=title,
        instruction="Do the thing",
        background="ctx",
        discovery_basis="basis",
    )


def test_openapi_bot_impl_satisfies_protocol():
    """The unified impl satisfies the SessionInitiator Plugin Protocol."""
    impl = OpenApiBotSessionInitiator(openapi_bot=MagicMock())
    assert isinstance(impl, SessionInitiator)


def test_openapi_bot_impl_creates_session_via_port():
    """Consumer ↔ Protocol conformance: with a stubbed OpenApiBotPort
    (send_message returns a session id) and engine-target resolution
    disabled, the unified impl returns a DiscoverySession tied to the
    first task."""
    stub_port = MagicMock()
    stub_port.send_message = AsyncMock(
        return_value=BotSendResult(run_id="run-1", session_id="sess-contract-1")
    )
    impl = OpenApiBotSessionInitiator(
        openapi_bot=stub_port,
        frontend_url_provider=None,
    )
    # Skip engine-target resolution → title update path is skipped (non-fatal).
    impl._resolve_engine_target = AsyncMock(return_value=None)  # noqa: SLF001

    result = asyncio.run(
        impl.initiate_session(
            [_make_task()],
            bot_id="bot-1",
            owner_id="owner-1",
            agent_id="agent-1",
        )
    )
    assert isinstance(result, DiscoverySession)
    assert result.session_id == "sess-contract-1"
    assert result.task_id == "td-contract-1"
    stub_port.send_message.assert_awaited_once()


def test_unavailable_placeholder_satisfies_protocol_and_fails_closed(world):
    """社区/单机 profile (无 OpenApiBotPort): 组合根注入 fail-closed 占位 —
    满足 Protocol, 调用即抛可读错误。(corp 装配世界绑定了真实 port 时跳过
    占位路径断言。)
    """
    initiator = world.get(SessionInitiator)
    assert isinstance(
        initiator, (UnavailableSessionInitiator, OpenApiBotSessionInitiator)
    )
    assert isinstance(initiator, SessionInitiator)
    if isinstance(initiator, OpenApiBotSessionInitiator):
        pytest.skip("world binds OpenApiBotPort (corp-ish profile) — placeholder path N/A")
    with pytest.raises(RuntimeError, match="SessionInitiator unavailable"):
        asyncio.run(
            initiator.initiate_session(
                [_make_task()],
                bot_id="bot-1",
                owner_id="owner-1",
                agent_id="agent-1",
            )
        )
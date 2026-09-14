"""Unit tests for AICodingAdapter —— WS path /api/ws 等。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from secbaas.community.plugins.bot.engine_adapter.aicoding.real import AICodingAdapter
from secbaas.community.plugins.bot.engine_adapter.aicoding.stub import (
    MockAICodingAdapter,
    NoopAICodingAdapter,
)
from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

ADAPTERS = [AICodingAdapter]


class _FakeSessionClient:
    """Duck-typed AsyncSessionClient stub recording create_session calls."""

    def __init__(self, created_id: str = "created-sess") -> None:
        self._created_id = created_id
        self.create_calls: list[dict] = []

    async def create_session(self, **kwargs: object) -> SimpleNamespace:
        self.create_calls.append(dict(kwargs))
        return SimpleNamespace(id=self._created_id)


@pytest.mark.parametrize("factory", ADAPTERS)
def test_is_bot_engine_adapter(factory: type) -> None:
    adapter = factory()
    assert isinstance(adapter, BotEngineAdapter)
    assert adapter.engine_type == "aicoding"


@pytest.mark.parametrize("factory", ADAPTERS)
def test_ws_path_is_api_ws(factory: type) -> None:
    assert factory().ws_path() == "/api/ws"


@pytest.mark.parametrize("factory", ADAPTERS)
def test_session_consistency_key_returns_structured_key(factory: type) -> None:
    """real aicoding 覆写返回结构化亲和键（与 claude_code/hermes 同形）。"""
    key = factory().session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
    assert key == "agent:b1:session:r1:user:u1"


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", ADAPTERS)
async def test_create_adapter_session_resolves_key_and_creates(
    factory: type,
) -> None:
    """从 planned_id 解析裸 key 以 uuid 新建（无前缀）。"""
    client = _FakeSessionClient(created_id="new-sess")
    sid, reused = await factory().create_adapter_session(
        session_client=client,
        planned_id="agent:b1:session:run-1:user:u1",
        user_id="u1",
        metadata={"title": "t", "model": "m"},
        bot_id="agent-1",
    )
    assert (sid, reused) == ("new-sess", False)
    assert len(client.create_calls) == 1
    call = client.create_calls[0]
    assert call["engine"] == "aicoding"
    assert call["agent_id"] == "agent-1"
    assert call["uuid"] == "run-1"
    # aicoding 不加 openclaw 的 agent:main: 前缀
    assert not sid.startswith("agent:main:")


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", ADAPTERS)
async def test_create_adapter_session_plain_id_used_as_uuid(factory: type) -> None:
    """非 planned 格式的显式 id 原样作为 uuid 创建（引擎侧幂等）。"""
    client = _FakeSessionClient()
    sid, reused = await factory().create_adapter_session(
        session_client=client,
        planned_id="existing-sess",
        user_id="u1",
        metadata={},
        bot_id="agent-1",
    )
    assert (sid, reused) == ("created-sess", False)
    assert client.create_calls[0]["uuid"] == "existing-sess"


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", ADAPTERS)
async def test_create_adapter_session_explicit_id_reuses(factory: type) -> None:
    """session_pending=False（显式 id，会话已存在）：直接复用，不触发创建。"""
    client = _FakeSessionClient()
    sid, reused = await factory().create_adapter_session(
        session_client=client,
        planned_id="agent:main:session:existing-key:user:u1",
        user_id="u1",
        metadata={},
        bot_id="agent-1",
        session_pending=False,
    )
    assert (sid, reused) == ("agent:main:session:existing-key:user:u1", True)
    assert client.create_calls == []


@pytest.mark.parametrize("noop_cls", [NoopAICodingAdapter])
def test_noop_returns_safe_zero_values(noop_cls: type) -> None:
    """Noop 不抛异常、返回安全零值。"""
    a = noop_cls()
    assert isinstance(a.ws_path(), str)
    assert (
        a.session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
        == "agent:b1:session:r1:user:u1"
    )


class TestNoopAICodingAdapterCreateSession:
    @pytest.mark.asyncio
    async def test_session_error_env_var(self, monkeypatch):
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_ERROR", "1")
        adapter = NoopAICodingAdapter()

        with pytest.raises(RuntimeError, match="simulated session creation failure"):
            await adapter.create_adapter_session(
                session_client=_FakeSessionClient(),
                planned_id="p1",
                user_id="u1",
                metadata={},
                bot_id="agent-1",
            )

    @pytest.mark.asyncio
    async def test_session_slow_env_var(self, monkeypatch):
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_SLOW", "1")
        adapter = NoopAICodingAdapter()

        sid, reused = await adapter.create_adapter_session(
            session_client=_FakeSessionClient(),
            planned_id="p1",
            user_id="u1",
            metadata={},
            bot_id="agent-1",
        )

        assert (sid, reused) == ("", True)

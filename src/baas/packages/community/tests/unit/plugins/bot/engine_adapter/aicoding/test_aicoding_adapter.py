"""Unit tests for AICodingAdapter —— WS path /api/ws 等。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from secbaas.plugins.bot.engine_adapter.aicoding.real import AICodingAdapter
from secbaas.plugins.bot.engine_adapter.aicoding.stub import (
    MockAICodingAdapter,
    NoopAICodingAdapter,
)
from secbaas.spi.bot.engine_adapter import BotEngineAdapter

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
def test_session_consistency_key_none_without_session_id(factory: type) -> None:
    key = factory().session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
    assert key is None


@pytest.mark.parametrize("factory", ADAPTERS)
def test_session_consistency_key_prefers_session_id(factory: type) -> None:
    key = factory().session_consistency_key(
        tc_bot_id="b1", user_id="u1", run_id="r1", session_id="s1"
    )
    assert key == "s1"


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", ADAPTERS)
async def test_create_adapter_session_reuses_existing(factory: type) -> None:
    client = _FakeSessionClient()
    sid, reused = await factory().create_adapter_session(
        session_client=client,
        session_id="existing-sess",
        user_id="u1",
        metadata={},
        bot_id="agent-1",
        run_id="run-1",
    )
    assert (sid, reused) == ("existing-sess", True)
    assert client.create_calls == []  # 复用不触发创建


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", ADAPTERS)
async def test_create_adapter_session_creates_new(factory: type) -> None:
    client = _FakeSessionClient(created_id="new-sess")
    sid, reused = await factory().create_adapter_session(
        session_client=client,
        session_id=None,
        user_id="u1",
        metadata={"title": "t", "model": "m"},
        bot_id="agent-1",
        run_id="run-1",
    )
    assert (sid, reused) == ("new-sess", False)
    assert len(client.create_calls) == 1
    call = client.create_calls[0]
    assert call["engine"] == "aicoding"
    assert call["agent_id"] == "agent-1"
    assert call["uuid"] == "run-1"
    # aicoding 不加 openclaw 的 agent:main: 前缀
    assert not sid.startswith("agent:main:")


@pytest.mark.parametrize("noop_cls", [NoopAICodingAdapter])
def test_noop_returns_safe_zero_values(noop_cls: type) -> None:
    """Noop 不抛异常、返回安全零值。"""
    a = noop_cls()
    assert isinstance(a.ws_path(), str)
    assert (
        a.session_consistency_key(
            tc_bot_id="b1", user_id="u1", run_id="r1", session_id="s1"
        )
        is None
    )

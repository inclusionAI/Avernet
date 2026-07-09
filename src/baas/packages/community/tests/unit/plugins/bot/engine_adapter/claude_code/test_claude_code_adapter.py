"""Unit tests for ClaudeCodeAdapter —— consistency_key + ws_path + create。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from secbaas.plugins.bot.engine_adapter.claude_code.real import ClaudeCodeAdapter
from secbaas.plugins.bot.engine_adapter.claude_code.stub import (
    MockClaudeCodeAdapter,
    NoopClaudeCodeAdapter,
)
from secbaas.spi.bot.engine_adapter import BotEngineAdapter

ADAPTER_CLASSES = [ClaudeCodeAdapter]


class _FakeSessionClient:
    def __init__(self, created_id: str = "cc-sess") -> None:
        self._created_id = created_id
        self.create_calls: list[dict] = []

    async def create_session(self, **kwargs: object) -> SimpleNamespace:
        self.create_calls.append(dict(kwargs))
        return SimpleNamespace(id=self._created_id)


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_is_bot_engine_adapter(cls: type) -> None:
    adapter = cls()
    assert isinstance(adapter, BotEngineAdapter)
    assert adapter.engine_type == "claude_code"


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_ws_path(cls: type) -> None:
    assert cls().ws_path() == "/api/claude_code/ws"


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_session_consistency_key(cls: type) -> None:
    adapter = cls()
    assert (
        adapter.session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
        == "agent:b1:session:r1:user:u1"
    )
    # session_id 非空优先透传
    assert (
        adapter.session_consistency_key(
            tc_bot_id="b1", user_id="u1", run_id="r1", session_id="s1"
        )
        == "s1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_adapter_session_creates_new(cls: type) -> None:
    client = _FakeSessionClient(created_id="new-sess")
    sid, reused = await cls().create_adapter_session(
        session_client=client,
        session_id=None,
        user_id="u1",
        metadata={},
        bot_id="agent-1",
        run_id="run-1",
    )
    assert (sid, reused) == ("new-sess", False)
    assert client.create_calls[0]["engine"] == "claude_code"
    # 不加 openclaw 的 agent:main: 前缀
    assert not sid.startswith("agent:main:")


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_adapter_session_reuses_existing(cls: type) -> None:
    client = _FakeSessionClient()
    sid, reused = await cls().create_adapter_session(
        session_client=client,
        session_id="existing-sess",
        user_id="u1",
        metadata={},
        bot_id="agent-1",
        run_id="run-1",
    )
    assert (sid, reused) == ("existing-sess", True)
    assert client.create_calls == []


@pytest.mark.parametrize("noop_cls", [NoopClaudeCodeAdapter])
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

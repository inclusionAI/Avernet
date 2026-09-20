"""Unit tests for ClaudeCodeAdapter —— consistency_key + ws_path + create。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from secbaas.community.plugins.bot.engine_adapter.claude_code.real import (
    ClaudeCodeAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.claude_code.stub import (
    MockClaudeCodeAdapter,
    NoopClaudeCodeAdapter,
)
from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

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


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_adapter_session_resolves_key_and_creates(cls: type) -> None:
    """从 planned_id 解析裸 key 以 uuid 新建（无前缀）。"""
    client = _FakeSessionClient(created_id="new-sess")
    sid, reused = await cls().create_adapter_session(
        session_client=client,
        planned_id="agent:cc-bot:session:run-1:user:u1",
        user_id="u1",
        metadata={},
        bot_id="agent-1",
    )
    assert (sid, reused) == ("new-sess", False)
    assert client.create_calls[0]["engine"] == "claude_code"
    assert client.create_calls[0]["uuid"] == "run-1"
    # 不加 openclaw 的 agent:main: 前缀
    assert not sid.startswith("agent:main:")


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_adapter_session_plain_id_used_as_uuid(cls: type) -> None:
    """非 planned 格式的显式 id 原样作为 uuid 创建（引擎侧幂等）。"""
    client = _FakeSessionClient()
    sid, reused = await cls().create_adapter_session(
        session_client=client,
        planned_id="existing-sess",
        user_id="u1",
        metadata={},
        bot_id="agent-1",
    )
    assert (sid, reused) == ("cc-sess", False)
    assert client.create_calls[0]["uuid"] == "existing-sess"


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_adapter_session_explicit_id_reuses(cls: type) -> None:
    """session_pending=False（显式 id，会话已存在）：直接复用，不触发创建。"""
    client = _FakeSessionClient()
    sid, reused = await cls().create_adapter_session(
        session_client=client,
        planned_id="agent:main:session:existing-key:user:u1",
        user_id="u1",
        metadata={},
        bot_id="agent-1",
        session_pending=False,
    )
    assert (sid, reused) == ("agent:main:session:existing-key:user:u1", True)
    assert client.create_calls == []


@pytest.mark.parametrize("noop_cls", [NoopClaudeCodeAdapter])
def test_noop_returns_safe_zero_values(noop_cls: type) -> None:
    """Noop 不抛异常、返回安全零值。"""
    a = noop_cls()
    assert isinstance(a.ws_path(), str)
    assert (
        a.session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
        == "agent:b1:session:r1:user:u1"
    )


class TestNoopClaudeCodeAdapterCreateSession:
    @pytest.mark.asyncio
    async def test_session_error_env_var(self, monkeypatch):
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_ERROR", "1")
        adapter = NoopClaudeCodeAdapter()

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
        adapter = NoopClaudeCodeAdapter()

        sid, reused = await adapter.create_adapter_session(
            session_client=_FakeSessionClient(),
            planned_id="p1",
            user_id="u1",
            metadata={},
            bot_id="agent-1",
        )

        assert (sid, reused) == ("", True)

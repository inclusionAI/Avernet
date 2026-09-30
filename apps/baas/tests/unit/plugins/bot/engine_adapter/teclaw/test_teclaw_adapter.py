"""Unit tests for TeClawAdapter —— consistency_key + ws_path + create。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from secbaas.community.plugins.bot.engine_adapter.teclaw.real import TeClawAdapter
from secbaas.community.plugins.bot.engine_adapter.teclaw.stub import (
    MockTeClawAdapter,
    NoopTeClawAdapter,
)
from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

ADAPTER_CLASSES = [TeClawAdapter]


class _FakeSessionClient:
    """teclaw 语义桩：get_session 可配置存在/不存在，create_session 记录调用。"""

    def __init__(
        self,
        created_id: str = "tc-sess",
        existing: bool = True,
    ) -> None:
        self._created_id = created_id
        self._existing = existing
        self.create_calls: list[dict] = []
        self.get_calls: list[tuple] = []

    async def get_session(self, session_id: str, engine_type: str) -> SimpleNamespace:
        self.get_calls.append((session_id, engine_type))
        if self._existing:
            return SimpleNamespace(id=session_id)
        raise RuntimeError(f"session not found: {session_id}")

    async def create_session(self, **kwargs: object) -> SimpleNamespace:
        self.create_calls.append(dict(kwargs))
        return SimpleNamespace(id=self._created_id)


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_is_bot_engine_adapter(cls: type) -> None:
    adapter = cls()
    assert isinstance(adapter, BotEngineAdapter)
    assert adapter.engine_type == "teclaw"


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_ws_path(cls: type) -> None:
    assert cls().ws_path() == "/api/teclaw/ws"


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_session_consistency_key(cls: type) -> None:
    adapter = cls()
    assert (
        adapter.session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
        == "agent:main:default:r1:user:u1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_adapter_session_reuses_existing(cls: type) -> None:
    """探测命中：直接复用传入 planned_id，不触发创建。"""
    client = _FakeSessionClient(existing=True)
    sid, reused = await cls().create_adapter_session(
        session_client=client,
        planned_id="tc-existing",
        user_id="u1",
        metadata={},
        bot_id="bot-1",
    )
    assert (sid, reused) == ("tc-existing", True)
    assert client.get_calls == [("tc-existing", "teclaw")]
    assert client.create_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_adapter_session_creates_with_planned_id(cls: type) -> None:
    """探测未命中：以传入 planned_id 作为 sessionKey 创建新会话。"""
    client = _FakeSessionClient(created_id="tc-new", existing=False)
    sid, reused = await cls().create_adapter_session(
        session_client=client,
        planned_id="tc-planned",
        user_id="u1",
        metadata={"title": "t"},
        bot_id="bot-1",
    )
    assert (sid, reused) == ("tc-new", False)
    assert client.create_calls[0]["session_id"] == "tc-planned"
    assert client.create_calls[0]["engine"] == "teclaw"
    assert client.create_calls[0]["agent_id"] == "bot-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_explicit_id_still_probes_and_creates(cls: type) -> None:
    """teclaw 忽略 session_pending：显式 id 未落库时同样探测补建。"""
    client = _FakeSessionClient(created_id="tc-explicit", existing=False)
    sid, reused = await cls().create_adapter_session(
        session_client=client,
        planned_id="tc-explicit",
        user_id="u1",
        metadata={},
        bot_id="bot-1",
        session_pending=False,
    )
    assert (sid, reused) == ("tc-explicit", False)
    assert client.create_calls[0]["session_id"] == "tc-explicit"


@pytest.mark.parametrize("noop_cls", [NoopTeClawAdapter])
def test_noop_returns_safe_zero_values(noop_cls: type) -> None:
    """Noop 不抛异常、key 返回通用兜底格式（恒非 None）。"""
    a = noop_cls()
    assert isinstance(a.ws_path(), str)
    assert (
        a.session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
        == "agent:main:default:r1:user:u1"
    )


class TestNoopTeClawAdapterCreateSession:
    @pytest.mark.asyncio
    async def test_session_error_env_var(self, monkeypatch):
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_ERROR", "1")
        adapter = NoopTeClawAdapter()

        with pytest.raises(RuntimeError, match="simulated session creation failure"):
            await adapter.create_adapter_session(
                session_client=_FakeSessionClient(),
                planned_id="tc-p",
                user_id="u1",
                metadata={},
                bot_id="bot-1",
            )

    @pytest.mark.asyncio
    async def test_session_slow_env_var(self, monkeypatch):
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_SLOW", "1")
        adapter = NoopTeClawAdapter()

        sid, reused = await adapter.create_adapter_session(
            session_client=_FakeSessionClient(),
            planned_id="tc-p",
            user_id="u1",
            metadata={},
            bot_id="bot-1",
        )

        assert (sid, reused) == ("", True)


def test_mock_records_calls() -> None:
    adapter = MockTeClawAdapter()
    assert adapter.ws_path() == "/api/teclaw/ws"
    assert (
        adapter.session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
        == "agent:main:default:r1:user:u1"
    )
    assert ("session_consistency_key", "b1", "u1", "r1") in adapter.calls

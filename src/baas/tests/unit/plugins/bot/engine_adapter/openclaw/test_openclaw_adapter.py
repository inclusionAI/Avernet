"""Unit tests for OpenClawAdapter —— consistency_key + ws_path + create。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from secbaas.community.plugins.bot.engine_adapter.openclaw.real import (
    OpenClawAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.openclaw.stub import (
    MockOpenClawAdapter,
    NoopOpenClawAdapter,
)
from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

ADAPTER_CLASSES = [OpenClawAdapter]

PLANNED_ID = "agent:main:session:oc-key:user:u1"


class _FakeSessionClient:
    def __init__(self, created_id: str = "oc-sess") -> None:
        self._created_id = created_id
        self.create_calls: list[dict] = []

    async def create_session(self, **kwargs: object) -> SimpleNamespace:
        self.create_calls.append(dict(kwargs))
        return SimpleNamespace(id=self._created_id)


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_is_bot_engine_adapter(cls: type) -> None:
    adapter = cls()
    assert isinstance(adapter, BotEngineAdapter)
    assert adapter.engine_type == "openclaw"


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_ws_path(cls: type) -> None:
    assert cls().ws_path() == "/api/openclaw/ws"


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_session_consistency_key(cls: type) -> None:
    adapter = cls()
    assert (
        adapter.session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
        == "agent:main:session:r1:user:u1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_adapter_session_creates_with_prefix(cls: type) -> None:
    """从 planned_id 解析裸 key 以 uuid 新建，返回 id 补 agent:main: 前缀。"""
    client = _FakeSessionClient(created_id="sess-raw")
    sid, reused = await cls().create_adapter_session(
        session_client=client,
        planned_id=PLANNED_ID,
        user_id="u1",
        metadata={},
        bot_id="agent-1",
    )
    assert (sid, reused) == ("agent:main:sess-raw", False)
    assert client.create_calls[0]["engine"] == "openclaw"
    assert client.create_calls[0]["uuid"] == "oc-key"
    assert client.create_calls[0]["agent_id"] == "agent-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_adapter_session_keeps_existing_prefix(cls: type) -> None:
    """adapter 返回的 id 已带 agent:main: 前缀时不重复添加。"""
    client = _FakeSessionClient(created_id="agent:main:sess-x")
    sid, reused = await cls().create_adapter_session(
        session_client=client,
        planned_id=PLANNED_ID,
        user_id="u1",
        metadata={},
        bot_id="agent-1",
    )
    assert (sid, reused) == ("agent:main:sess-x", False)


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_adapter_session_plain_id_used_as_uuid(cls: type) -> None:
    """非 planned 格式的显式 id 原样作为 uuid 创建（引擎侧幂等）。"""
    client = _FakeSessionClient(created_id="sess-plain")
    sid, _ = await cls().create_adapter_session(
        session_client=client,
        planned_id="existing-sess",
        user_id="u1",
        metadata={},
        bot_id="agent-1",
    )
    assert client.create_calls[0]["uuid"] == "existing-sess"


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_adapter_session_explicit_id_reuses(cls: type) -> None:
    """session_pending=False（显式 id，会话已存在）：直接复用，不创建。"""
    client = _FakeSessionClient(created_id="sess-raw")
    sid, reused = await cls().create_adapter_session(
        session_client=client,
        planned_id=PLANNED_ID,
        user_id="u1",
        metadata={},
        bot_id="agent-1",
        session_pending=False,
    )
    assert (sid, reused) == (PLANNED_ID, True)
    assert client.create_calls == []


@pytest.mark.parametrize("noop_cls", [NoopOpenClawAdapter])
def test_noop_returns_safe_zero_values(noop_cls: type) -> None:
    """Noop 不抛异常、key 返回 openclaw 格式（恒非 None）。"""
    a = noop_cls()
    assert isinstance(a.ws_path(), str)
    assert (
        a.session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
        == "agent:main:session:r1:user:u1"
    )


class TestNoopOpenClawAdapterCreateSession:
    @pytest.mark.asyncio
    async def test_session_error_env_var(self, monkeypatch):
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_ERROR", "1")
        adapter = NoopOpenClawAdapter()

        with pytest.raises(RuntimeError, match="simulated session creation failure"):
            await adapter.create_adapter_session(
                session_client=_FakeSessionClient(),
                planned_id="oc-p",
                user_id="u1",
                metadata={},
                bot_id="agent-1",
            )

    @pytest.mark.asyncio
    async def test_session_slow_env_var(self, monkeypatch):
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_SLOW", "1")
        adapter = NoopOpenClawAdapter()

        sid, reused = await adapter.create_adapter_session(
            session_client=_FakeSessionClient(),
            planned_id="oc-p",
            user_id="u1",
            metadata={},
            bot_id="agent-1",
        )

        assert (sid, reused) == ("", True)


def test_mock_records_calls() -> None:
    adapter = MockOpenClawAdapter()
    assert adapter.ws_path() == "/api/openclaw/ws"
    assert (
        adapter.session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
        == "agent:main:session:r1:user:u1"
    )
    assert ("session_consistency_key", "b1", "u1", "r1") in adapter.calls

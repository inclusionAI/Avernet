"""Unit tests for HermesAdapter —— 持久化等待 + 亲和 key。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from secbaas.community.api.bot_runtime import BotNotAvailableError
from secbaas.community.plugins.bot.engine_adapter.hermes.real import HermesAdapter
from secbaas.community.plugins.bot.engine_adapter.hermes.stub import (
    MockHermesAdapter,
    NoopHermesAdapter,
)
from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

ADAPTER_CLASSES = [HermesAdapter]
_PERSISTENT_ID = "20260701_120000_abcdef"
_API_ID = "api-xyz123"


class _FakeSessionClient:
    """Duck-typed AsyncSessionClient stub with scriptable persistence."""

    def __init__(self, created_id: str, get_ids: list[str] | None = None) -> None:
        self._created_id = created_id
        self._get_ids = list(get_ids or [])
        self.create_calls: list[dict] = []
        self.get_calls: list[tuple] = []

    async def create_session(self, **kwargs: object) -> SimpleNamespace:
        self.create_calls.append(dict(kwargs))
        return SimpleNamespace(id=self._created_id)

    async def get_session(
        self, session_id: str, engine: str | None = None
    ) -> SimpleNamespace:
        self.get_calls.append((session_id, engine))
        if self._get_ids:
            return SimpleNamespace(id=self._get_ids.pop(0))
        return SimpleNamespace(id=session_id)


def _fast_adapter(cls: type):
    """Adapter with tiny timeout/poll so timeout tests stay fast."""
    return HermesAdapter(
        session_persist_timeout_seconds=0.1, poll_interval_seconds=0.01
    )


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_is_bot_engine_adapter(cls: type) -> None:
    adapter = cls()
    assert isinstance(adapter, BotEngineAdapter)
    assert adapter.engine_type == "hermes"


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_ws_path(cls: type) -> None:
    assert cls().ws_path() == "/api/hermes/ws"


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_session_consistency_key_non_none(cls: type) -> None:
    key = cls().session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
    assert key == "agent:b1:session:r1:user:u1"


@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
def test_session_consistency_key_prefers_session_id(cls: type) -> None:
    key = cls().session_consistency_key(
        tc_bot_id="b1", user_id="u1", run_id="r1", session_id="s1"
    )
    assert key == "s1"


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_returns_immediate_persistent_id(cls: type) -> None:
    client = _FakeSessionClient(created_id=_PERSISTENT_ID)
    sid, reused = await _fast_adapter(cls).create_adapter_session(
        session_client=client,
        session_id=None,
        user_id="u1",
        metadata={},
        bot_id="agent-1",
        run_id="run-1",
    )
    assert (sid, reused) == (_PERSISTENT_ID, False)
    assert client.get_calls == []  # 无需轮询
    assert client.create_calls[0]["engine"] == "hermes"


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_accepts_api_format_id(cls: type) -> None:
    client = _FakeSessionClient(created_id=_API_ID)
    sid, _ = await _fast_adapter(cls).create_adapter_session(
        session_client=client,
        session_id=None,
        user_id="u1",
        metadata={},
        bot_id="agent-1",
        run_id="run-1",
    )
    assert sid == _API_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_create_polls_until_persistent(cls: type) -> None:
    client = _FakeSessionClient(
        created_id="gw-temp-1", get_ids=["gw-temp-1", _PERSISTENT_ID]
    )
    sid, reused = await _fast_adapter(cls).create_adapter_session(
        session_client=client,
        session_id=None,
        user_id="u1",
        metadata={},
        bot_id="agent-1",
        run_id="run-1",
    )
    assert (sid, reused) == (_PERSISTENT_ID, False)
    assert len(client.get_calls) >= 1  # 轮询过


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_persistence_timeout_raises(cls: type) -> None:
    client = _FakeSessionClient(created_id="gw-temp")  # get 恒返回非持久 id
    with pytest.raises(
        BotNotAvailableError, match="hermes session persistence timeout"
    ):
        await _fast_adapter(cls).create_adapter_session(
            session_client=client,
            session_id=None,
            user_id="u1",
            metadata={},
            bot_id="agent-1",
            run_id="run-1",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", ADAPTER_CLASSES)
async def test_reuse_existing_session_skips_persistence(cls: type) -> None:
    client = _FakeSessionClient(created_id=_PERSISTENT_ID)
    sid, reused = await _fast_adapter(cls).create_adapter_session(
        session_client=client,
        session_id="existing-sess",
        user_id="u1",
        metadata={},
        bot_id="agent-1",
        run_id="run-1",
    )
    assert (sid, reused) == ("existing-sess", True)
    assert client.create_calls == []


@pytest.mark.parametrize("noop_cls", [NoopHermesAdapter])
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


class TestNoopHermesAdapterCreateSession:
    @pytest.mark.asyncio
    async def test_session_error_env_var(self, monkeypatch):
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_ERROR", "1")
        adapter = NoopHermesAdapter()

        with pytest.raises(RuntimeError, match="simulated session creation failure"):
            await adapter.create_adapter_session(
                session_client=_FakeSessionClient("mock-hermes-session"),
                session_id=None,
                user_id="u1",
                metadata={},
                bot_id="agent-1",
                run_id="run-1",
            )

    @pytest.mark.asyncio
    async def test_session_slow_env_var(self, monkeypatch):
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_SLOW", "1")
        adapter = NoopHermesAdapter()

        sid, reused = await adapter.create_adapter_session(
            session_client=_FakeSessionClient("mock-hermes-session"),
            session_id=None,
            user_id="u1",
            metadata={},
            bot_id="agent-1",
            run_id="run-1",
        )

        assert (sid, reused) == ("", True)

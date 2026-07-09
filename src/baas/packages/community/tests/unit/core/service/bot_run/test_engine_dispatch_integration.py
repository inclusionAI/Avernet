"""引擎分流集成回归测试。

穿透 ``BaasBotService.create_session`` 全链路(真 registry + 真 adapter),只把最下游的
``wss_resolver`` 与 session_client 换成 mock,断言每个 engine_type 的可观测分流结果:
WS path、device 亲和 key、session_client.create_session 的 engine 入参、openclaw 前缀。

作用:后人改 `_baas_service` 接缝 / registry 装配 / 任一 adapter 导致引擎走错路时,
本测试立刻变红——尤其锁死 openclaw / teclaw 老引擎行为不被带偏。默认 CI 即运行。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.api.bot_runtime import BotBindingInfo, WsConnectionInfo
from secbaas.bootstrap._core_services import _real_engine_adapter_registry
from secbaas.core.service.bot_run import (
    BaasBotService,
    BaasBotServiceConfig,
)

_TC_BOT_ID = "20260701_dispatch"
_USER_ID = "374193"
_RUN_ID = "run-1"
_AGENT_KEY = f"agent:{_TC_BOT_ID}:session:{_RUN_ID}:user:{_USER_ID}"


def _make_conn_info() -> WsConnectionInfo:
    return WsConnectionInfo(
        ws_url="wss://gw/proxypass/ARCA_sb:20003/api/ws",
        token="tok",
        target="ARCA_sb:20003",
        expires_at=datetime.now(tz=UTC),
    )


def _make_binding(engine_type: str) -> BotBindingInfo:
    return BotBindingInfo(
        bot_id=_TC_BOT_ID,
        entity_id=_USER_ID,
        sandbox_id=None,
        device_id="dev-1",
        device_provider="baas",
        binding_id=1,
        device_props={"tenant": "t1"},
        bot_type="personal",  # personal bot -> resolve_user_id 直接用 entity_id
        engine_type=engine_type,
    )


class _FakeSessionClient:
    """记录 create/get 调用的假 session_client(支持 async with)。"""

    def __init__(self, created_id: str) -> None:
        self._created_id = created_id
        self.create_kwargs: dict | None = None

    async def __aenter__(self) -> _FakeSessionClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def create_session(self, **kwargs: object) -> SimpleNamespace:
        self.create_kwargs = dict(kwargs)
        return SimpleNamespace(id=self._created_id)

    async def get_session(
        self, session_id: str, engine: str | None = None
    ) -> SimpleNamespace:
        return SimpleNamespace(id=session_id)


def _make_service(wss_resolver: AsyncMock) -> BaasBotService:
    return BaasBotService(
        config=BaasBotServiceConfig(
            adapter_port=20003,
            ws_path="/api/openclaw/ws",
            connect_timeout=10,
            request_timeout=30,
        ),
        client_pool=MagicMock(),
        wss_resolver=wss_resolver,
        session_service=MagicMock(),
        engine_adapter_registry=_real_engine_adapter_registry(),
    )


# (engine_type, 下游返回的 created_id, 期望 ws path, 期望 device_affinity, 期望返回 session_id)
_CASES = [
    ("aicoding", "sess-aic", "/api/ws", None, "sess-aic"),
    (
        "hermes",
        "20260701_120000_abcdef",
        "/api/hermes/ws",
        _AGENT_KEY,
        "20260701_120000_abcdef",
    ),
    ("claude_code", "sess-cc", "/api/claude_code/ws", _AGENT_KEY, "sess-cc"),
    # openclaw / teclaw 不在 registry(走 else 原始分支)—— 锁死老引擎行为
    (
        "openclaw",
        "sess-oc",
        "/api/openclaw/ws",
        f"agent:main:session:{_RUN_ID}:user:{_USER_ID}",
        "agent:main:sess-oc",
    ),
    ("teclaw", "sess-tc", "/api/teclaw/ws", None, "sess-tc"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine, created_id, exp_path, exp_affinity, exp_session_id",
    _CASES,
    ids=[c[0] for c in _CASES],
)
async def test_engine_dispatch(
    engine: str,
    created_id: str,
    exp_path: str,
    exp_affinity: str | None,
    exp_session_id: str,
) -> None:
    resolver = AsyncMock()
    resolver.dispatch_bot_ws_conn_info = AsyncMock(return_value=_make_conn_info())
    svc = _make_service(resolver)
    fake_client = _FakeSessionClient(created_id)

    with patch.object(svc, "_create_session_client", return_value=fake_client):
        info = await svc.create_session(
            bot_id=_TC_BOT_ID,
            session_id=None,
            metadata={"tenant": "t1"},  # 有 tenant、无 invoker -> 跳过持久化
            binding_info=_make_binding(engine),
            context=None,
            run_id=_RUN_ID,
        )

    # 接缝②:WS path 传给 resolver
    dispatch_kwargs = resolver.dispatch_bot_ws_conn_info.call_args.kwargs
    assert dispatch_kwargs["path"] == exp_path
    # 接缝①:device 亲和 key
    assert dispatch_kwargs["device_affinity"] == exp_affinity
    # 接缝③:session_client.create_session 收到正确 engine
    assert fake_client.create_kwargs is not None
    assert fake_client.create_kwargs["engine"] == engine
    # 返回 session_id(openclaw 带 agent:main: 前缀,其余不带)
    assert info.session_id == exp_session_id


@pytest.mark.asyncio
async def test_openclaw_gets_agent_main_prefix_but_aicoding_does_not() -> None:
    """显式对比:同样的下游 id,openclaw 加前缀、aicoding 不加。"""
    results = {}
    for engine in ("openclaw", "aicoding"):
        resolver = AsyncMock()
        resolver.dispatch_bot_ws_conn_info = AsyncMock(return_value=_make_conn_info())
        svc = _make_service(resolver)
        with patch.object(
            svc, "_create_session_client", return_value=_FakeSessionClient("raw-id")
        ):
            info = await svc.create_session(
                bot_id=_TC_BOT_ID,
                session_id=None,
                metadata={"tenant": "t1"},
                binding_info=_make_binding(engine),
                context=None,
                run_id=_RUN_ID,
            )
        results[engine] = info.session_id
    assert results["openclaw"] == "agent:main:raw-id"
    assert results["aicoding"] == "raw-id"

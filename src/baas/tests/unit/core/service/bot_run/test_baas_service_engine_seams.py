"""§6.6 — BaasBotService 引擎接缝分流测试。

验证 registry 注入 + `_adapter_for` 门控：新引擎走 adapter，openclaw/teclaw 走 else
（`_adapter_for` 返回 None）。字节级不变性由 else 分支结构保证 + 现有 test_baas_service 回归。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from secbaas.community.core.service.bot_run import (
    BaasBotService,
    BaasBotServiceConfig,
    BotEngineAdapterRegistry,
)
from secbaas.community.plugins.bot.engine_adapter.aicoding import MockAICodingAdapter


def _service(registry: BotEngineAdapterRegistry | None) -> BaasBotService:
    return BaasBotService(
        config=BaasBotServiceConfig(
            adapter_port=20003,
            ws_path="/api/openclaw/ws",
            connect_timeout=10,
            request_timeout=30,
        ),
        client_pool=MagicMock(),
        wss_resolver=MagicMock(),
        session_service=MagicMock(),
        engine_adapter_registry=registry,
    )


@pytest.fixture
def registry() -> BotEngineAdapterRegistry:
    return BotEngineAdapterRegistry({"aicoding": MockAICodingAdapter()})


def test_adapter_for_new_engine_returns_adapter(registry) -> None:
    svc = _service(registry)
    adapter = svc._adapter_for("aicoding")
    assert adapter is not None
    assert adapter.engine_type == "aicoding"


def test_adapter_for_legacy_engines_return_none(registry) -> None:
    svc = _service(registry)
    # openclaw / teclaw 不注册 → None → 走 else 原始分支
    assert svc._adapter_for("openclaw") is None
    assert svc._adapter_for("teclaw") is None
    assert svc._adapter_for(None) is None
    assert svc._adapter_for("unknown") is None


def test_adapter_for_none_registry_returns_none() -> None:
    svc = _service(None)
    assert svc._adapter_for("aicoding") is None


def test_strip_ws_url_to_base_aicoding_path() -> None:
    # aicoding: strip /api/ws 得到干净 base_url
    base = BaasBotService._strip_ws_url_to_base(
        "wss://host/proxypass/tgt/api/ws", "/api/ws"
    )
    assert base == "https://host/proxypass/tgt"


def test_build_base_url_openclaw_regression() -> None:
    # openclaw 静态 _build_base_url 行为保持不变
    conn = MagicMock()
    conn.ws_url = "wss://host/proxypass/tgt/api/openclaw/ws"
    assert (
        BaasBotService._build_base_url(conn, "openclaw") == "https://host/proxypass/tgt"
    )


def test_create_session_client_aicoding_strips_api_ws(registry) -> None:
    svc = _service(registry)
    conn = MagicMock()
    conn.ws_url = "wss://host/proxypass/tgt/api/ws"
    conn.token = "tok"
    client = svc._create_session_client(conn, "aicoding")
    # aicoding 用 adapter.ws_path()=/api/ws strip → base 干净
    assert client.base_url == "https://host/proxypass/tgt"


def test_create_session_client_openclaw_uses_legacy_suffix(registry) -> None:
    svc = _service(registry)
    conn = MagicMock()
    conn.ws_url = "wss://host/proxypass/tgt/api/openclaw/ws"
    conn.token = "tok"
    client = svc._create_session_client(conn, "openclaw")
    assert client.base_url == "https://host/proxypass/tgt"

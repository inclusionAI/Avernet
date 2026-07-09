"""Unit tests for BotEngineAdapterRegistry + build_engine_adapter_registry。"""

from __future__ import annotations

import pytest

from secbaas.bootstrap._core_services import _real_engine_adapter_registry
from secbaas.core.service.bot_run import BotEngineAdapterRegistry
from secbaas.plugins.bot.engine_adapter.aicoding.stub import MockAICodingAdapter
from secbaas.spi.bot.engine_adapter import BotEngineAdapter

# ── Registry.get / has ────────────────────────────────────────────────────


def test_get_returns_registered_adapter() -> None:
    adapter = MockAICodingAdapter()
    registry = BotEngineAdapterRegistry({"aicoding": adapter})
    assert registry.get("aicoding") is adapter


def test_get_unregistered_raises_key_error() -> None:
    registry = BotEngineAdapterRegistry({})
    with pytest.raises(KeyError):
        registry.get("openclaw")


def test_has_true_for_registered() -> None:
    registry = BotEngineAdapterRegistry({"aicoding": MockAICodingAdapter()})
    assert registry.has("aicoding") is True


def test_has_false_for_unregistered_no_raise() -> None:
    registry = BotEngineAdapterRegistry({"aicoding": MockAICodingAdapter()})
    # openclaw / teclaw 不注册 → has 返回 False(BaasBotService 走 else 分支)
    assert registry.has("openclaw") is False
    assert registry.has("teclaw") is False


# ── _real_engine_adapter_registry(bootstrap 装配) ────────────────────────


def test_build_registers_three_engines() -> None:
    registry = _real_engine_adapter_registry()
    for engine in ("aicoding", "hermes", "claude_code"):
        assert registry.has(engine) is True
        adapter = registry.get(engine)
        assert isinstance(adapter, BotEngineAdapter)
        assert adapter.engine_type == engine


def test_build_does_not_register_legacy_engines() -> None:
    registry = _real_engine_adapter_registry()
    assert registry.has("openclaw") is False
    assert registry.has("teclaw") is False


def test_build_aicoding_ws_path_is_api_ws() -> None:
    registry = _real_engine_adapter_registry()
    assert registry.get("aicoding").ws_path() == "/api/ws"


def test_build_hermes_ws_path() -> None:
    registry = _real_engine_adapter_registry()
    assert registry.get("hermes").ws_path() == "/api/hermes/ws"


def test_build_claude_code_ws_path() -> None:
    registry = _real_engine_adapter_registry()
    assert registry.get("claude_code").ws_path() == "/api/claude_code/ws"

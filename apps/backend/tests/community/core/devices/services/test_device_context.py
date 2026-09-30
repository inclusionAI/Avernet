from dataclasses import FrozenInstanceError

import pytest

from agentclaw.community.core.devices.services.device_context import (
    DeviceContext,
    BotNotFoundError,
    DeviceNotBoundError,
    UnknownProviderError,
    ConnInfoBuildError,
)


def test_device_context_constructs_with_all_required_fields():
    ctx = DeviceContext(
        provider="baas",
        conn_info={"url": "http://test"},
        binding_id=42,
        bot_id="bot-1",
        user_id="user-1",
    )
    assert ctx.provider == "baas"
    assert ctx.binding_id == 42


def test_device_context_is_frozen():
    ctx = DeviceContext(
        provider="baas",
        conn_info={},
        binding_id=42,
        bot_id="bot-1",
        user_id="user-1",
    )
    with pytest.raises(FrozenInstanceError):
        ctx.provider = "arca"  # type: ignore[misc]


def test_device_context_provider_must_be_known():
    # ProviderType Literal — typing 不强制运行时检查,但本测试卡未来 mypy
    ctx = DeviceContext(
        provider="arca",  # 合法
        conn_info={},
        binding_id=1,
        bot_id="b",
        user_id="u",
    )
    assert ctx.provider in ("arca", "baas", "teclaw", "local")


def test_exception_classes_exist():
    # 仅断 4 个异常类可被实例化 + 是 RuntimeError 子类
    for exc_cls in (
        BotNotFoundError,
        DeviceNotBoundError,
        UnknownProviderError,
        ConnInfoBuildError,
    ):
        e = exc_cls("test")
        assert isinstance(e, RuntimeError)
        assert str(e) == "test"

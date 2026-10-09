"""BotEngineAdapter SPI 契约测试。

断言 Protocol 形状(4 个公共成员 + @runtime_checkable)与各 adapter 实现满足 isinstance。
"""

from __future__ import annotations

import pytest

from secbaas.community.plugins.bot.engine_adapter.aicoding.real import AICodingAdapter
from secbaas.community.plugins.bot.engine_adapter.aicoding.stub import (
    MockAICodingAdapter,
    NoopAICodingAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.claude_code.real import (
    ClaudeCodeAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.claude_code.stub import (
    MockClaudeCodeAdapter,
    NoopClaudeCodeAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.hermes.real import HermesAdapter
from secbaas.community.plugins.bot.engine_adapter.hermes.stub import (
    MockHermesAdapter,
    NoopHermesAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.openclaw.real import (
    OpenClawAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.openclaw.stub import (
    MockOpenClawAdapter,
    NoopOpenClawAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.teclaw.real import TeClawAdapter
from secbaas.community.plugins.bot.engine_adapter.teclaw.stub import (
    MockTeClawAdapter,
    NoopTeClawAdapter,
)
from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

EXPECTED_MEMBERS = {
    "engine_type",
    "ws_path",
    "session_consistency_key",
    "create_adapter_session",
}

# (factory, expected_engine_type) — 覆盖 5 引擎 × {real, noop, mock}
ADAPTER_CASES = [
    (OpenClawAdapter, "openclaw"),
    (NoopOpenClawAdapter, "openclaw"),
    (MockOpenClawAdapter, "openclaw"),
    (TeClawAdapter, "teclaw"),
    (NoopTeClawAdapter, "teclaw"),
    (MockTeClawAdapter, "teclaw"),
    (AICodingAdapter, "aicoding"),
    (NoopAICodingAdapter, "aicoding"),
    (MockAICodingAdapter, "aicoding"),
    (HermesAdapter, "hermes"),
    (NoopHermesAdapter, "hermes"),
    (MockHermesAdapter, "hermes"),
    (ClaudeCodeAdapter, "claude_code"),
    (NoopClaudeCodeAdapter, "claude_code"),
    (MockClaudeCodeAdapter, "claude_code"),
]


def test_protocol_exposes_exactly_expected_members() -> None:
    """Protocol 只暴露 4 个约定公共成员，私有辅助方法不进契约。"""
    public = {m for m in dir(BotEngineAdapter) if not m.startswith("_")}
    assert public == EXPECTED_MEMBERS


def test_protocol_is_runtime_checkable() -> None:
    """@runtime_checkable：duck-typed 对象可通过 isinstance（否则会 TypeError）。"""

    class _Dummy:
        engine_type = "dummy"

        def ws_path(self) -> str:
            return "/api/ws"

        def session_consistency_key(self, **_: object) -> str | None:
            return None

        async def create_adapter_session(self, **_: object) -> tuple[str, bool]:
            return ("", True)

    assert isinstance(_Dummy(), BotEngineAdapter)


@pytest.mark.parametrize(
    "factory, engine_type",
    ADAPTER_CASES,
    ids=[f"{f.__name__}" for f, _ in ADAPTER_CASES],
)
def test_adapter_satisfies_protocol(factory: type, engine_type: str) -> None:
    adapter = factory()
    assert isinstance(adapter, BotEngineAdapter)
    assert adapter.engine_type == engine_type


@pytest.mark.parametrize(
    "factory",
    [f for f, _ in ADAPTER_CASES],
    ids=[f"{f.__name__}" for f, _ in ADAPTER_CASES],
)
def test_ws_path_returns_str(factory: type) -> None:
    assert isinstance(factory().ws_path(), str)


@pytest.mark.parametrize(
    "factory",
    [f for f, _ in ADAPTER_CASES],
    ids=[f"{f.__name__}" for f, _ in ADAPTER_CASES],
)
def test_session_consistency_key_returns_str_or_none(factory: type) -> None:
    key = factory().session_consistency_key(tc_bot_id="b1", user_id="u1", run_id="r1")
    assert key is None or isinstance(key, str)

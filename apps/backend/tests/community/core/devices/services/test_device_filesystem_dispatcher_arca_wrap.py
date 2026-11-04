"""Tests for DeviceFilesystemDispatcher arca error-classifier wrapping."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.arca_error_classifier import (
    ArcaErrorClassifier,
)
from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem
from agentclaw.community.core.devices.services.device_filesystem_dispatcher import (
    DeviceFilesystemDispatcher,
)


def _make_ctx(provider: str, *, sandbox_id: str | None = "test-sandbox") -> DeviceContext:
    conn_info: dict = {}
    if sandbox_id is not None:
        conn_info["sandbox_id"] = sandbox_id
    return DeviceContext(
        provider=provider,
        conn_info=conn_info,
        binding_id=1,
        bot_id="bot-1",
        user_id="user-1",
    )


def _dispatcher(resolve_fn=None):
    """Build a dispatcher with a controllable resolver."""
    device_plugin = MagicMock()
    if resolve_fn is None:
        resolve_fn = MagicMock(return_value=MagicMock(spec=DeviceFileSystem))
    return DeviceFilesystemDispatcher(
        device_plugin=device_plugin,
        resolve=resolve_fn,
    )


def test_build_arca_provider_wraps_with_classifier():
    inner_fs = MagicMock(spec=DeviceFileSystem)
    resolve = MagicMock(return_value=inner_fs)
    dispatcher = _dispatcher(resolve_fn=resolve)
    ctx = _make_ctx("arca", sandbox_id="ARCA-SANDBOX-1")

    result = dispatcher._build(ctx, lambda p: p)

    assert isinstance(result, ArcaErrorClassifier)
    assert result._inner is inner_fs
    assert result._sandbox_id == "ARCA-SANDBOX-1"


def test_build_non_arca_provider_does_not_wrap():
    inner_fs = MagicMock(spec=DeviceFileSystem)
    resolve = MagicMock(return_value=inner_fs)
    dispatcher = _dispatcher(resolve_fn=resolve)

    for provider in ("baas", "teclaw", "local"):
        ctx = _make_ctx(provider)
        result = dispatcher._build(ctx, lambda p: p)
        assert not isinstance(result, ArcaErrorClassifier)
        assert result is inner_fs


def test_build_arca_missing_sandbox_id_fallback():
    inner_fs = MagicMock(spec=DeviceFileSystem)
    resolve = MagicMock(return_value=inner_fs)
    dispatcher = _dispatcher(resolve_fn=resolve)
    ctx = _make_ctx("arca", sandbox_id=None)

    result = dispatcher._build(ctx, lambda p: p)

    assert isinstance(result, ArcaErrorClassifier)
    assert result._sandbox_id == "unknown"


def test_dispatch_arca_uses_build_and_wraps():
    inner_fs = MagicMock(spec=DeviceFileSystem)
    resolve = MagicMock(return_value=inner_fs)
    dispatcher = _dispatcher(resolve_fn=resolve)
    ctx = _make_ctx("arca")

    result = dispatcher.dispatch(ctx)

    assert isinstance(result, ArcaErrorClassifier)
    resolve.assert_called_once()


def test_dispatch_non_arca_does_not_wrap():
    inner_fs = MagicMock(spec=DeviceFileSystem)
    resolve = MagicMock(return_value=inner_fs)
    dispatcher = _dispatcher(resolve_fn=resolve)

    for provider in ("baas", "teclaw", "local"):
        ctx = _make_ctx(provider)
        result = dispatcher.dispatch(ctx)
        assert not isinstance(result, ArcaErrorClassifier)

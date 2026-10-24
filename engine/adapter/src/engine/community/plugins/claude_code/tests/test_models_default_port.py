"""Port-impl tests for ``ClaudeCodePluginImpl.models_default`` (``models.default`` RPC).

Kept out of ``test_mixins_coverage.py`` (already past the 1,000-line limit).
Pins the RPC name, params, timeout, and the response → dict | None mapping.
"""
from __future__ import annotations

from typing import Any

from engine.community.kernel.frames import ErrorShape, ResponseFrame
from engine.community.plugins.claude_code.plugin_impl import ClaudeCodePluginImpl
from engine.community.plugins.skills_pool.center_content import MountedCenterContentAdapter


class _FakeRelayClient:
    def __init__(self, response: ResponseFrame) -> None:
        self.connected = True
        self.hello = None
        self._response = response
        self.calls: list[tuple[str, dict | None, float]] = []

    async def send_request(self, method: str, params: dict | None = None,
                           timeout: float = 30.0) -> Any:
        self.calls.append((method, params, timeout))
        return self._response


def _impl(response: ResponseFrame) -> tuple[ClaudeCodePluginImpl, _FakeRelayClient]:
    client = _FakeRelayClient(response)
    return ClaudeCodePluginImpl(center_content_adapter=MountedCenterContentAdapter(), client=client), client


async def test_models_default_returns_payload_and_pins_rpc_shape():
    payload = {"model": "claude-sonnet-4-5", "source": "settings.json", "path": "/x/settings.json"}
    impl, client = _impl(ResponseFrame(id="1", ok=True, payload=payload))
    assert await impl.models_default() == payload
    assert client.calls == [("models.default", {}, 5.0)]


async def test_models_default_none_on_error_frame():
    impl, _ = _impl(ResponseFrame(
        id="1", ok=False, error=ErrorShape(code="UNKNOWN_METHOD", message="unsupported"),
    ))
    assert await impl.models_default() is None


async def test_models_default_none_without_model_string():
    impl, _ = _impl(ResponseFrame(id="1", ok=True, payload={"model": "  ", "source": "env"}))
    assert await impl.models_default() is None


async def test_models_default_none_when_payload_not_dict():
    impl, _ = _impl(ResponseFrame(id="1", ok=True, payload=["not", "a", "dict"]))
    assert await impl.models_default() is None

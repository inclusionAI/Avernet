"""Port-impl tests for OpenClawPluginImpl.node_list (transport — node.list RPC).

Preserves the legacy engines/openclaw/tests/test_node.py transport coverage at the
port layer: asserts the RAW node dicts + error/payload-shape behavior. The dict→Node
DTO build is covered by core/adapters/openclaw/tests/test_node.py.
"""
from __future__ import annotations

from typing import Any

from engine.community.kernel.frames import ErrorShape, ResponseFrame
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


class _FakeClient:
    def __init__(self, response: ResponseFrame | None = None, raises: Exception | None = None):
        self.connected = True
        self._response = response
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    async def send_request(self, method: str, params: dict | None = None, timeout: Any = None):
        self.calls.append((method, params or {}))
        if self._raises:
            raise self._raises
        return self._response


def _impl(**kw) -> tuple[OpenClawPluginImpl, _FakeClient]:
    client = _FakeClient(**kw)
    return OpenClawPluginImpl(client=client), client


async def test_node_list_returns_raw_dicts_from_nodes_key():
    nodes = [{"nodeId": "n1", "connected": True}, {"nodeId": "n2", "paired": True}]
    impl, client = _impl(response=ResponseFrame(id="1", ok=True, payload={"nodes": nodes}))
    out = await impl.node_list()
    assert out == nodes
    assert client.calls == [("node.list", {})]


async def test_node_list_accepts_bare_list_payload():
    nodes = [{"nodeId": "n1"}]
    impl, _ = _impl(response=ResponseFrame(id="1", ok=True, payload=nodes))
    assert await impl.node_list() == nodes


async def test_node_list_empty_on_not_ok():
    impl, _ = _impl(response=ResponseFrame(id="1", ok=False, error=ErrorShape(code="X", message="boom")))
    assert await impl.node_list() == []


async def test_node_list_empty_on_connection_error():
    impl, _ = _impl(raises=ConnectionError("down"))
    assert await impl.node_list() == []


async def test_node_list_empty_on_unexpected_payload():
    impl, _ = _impl(response=ResponseFrame(id="1", ok=True, payload="not-a-list-or-dict"))
    assert await impl.node_list() == []

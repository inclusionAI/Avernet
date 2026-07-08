"""Unit tests for the OpenClaw node ACL adapter.

Drives `OpenClawNodeAdapter` against a fake `OpenClawNodePort` (a plain object
returning canned raw dicts) — the adapter's job is dict→Node translation +
filter/paging, with no gateway involved. Mirrors the behavior the legacy
`OpenClawNodeService.list_nodes` produced.
"""
from __future__ import annotations

import pytest

from engine.community.core.adapters.openclaw.node import OpenClawNodeAdapter
from engine.community.core.node.models import NodeListRequest


class _FakePort:
    def __init__(self, raw: list[dict]) -> None:
        self._raw = raw
        self.calls = 0

    async def node_list(self) -> list[dict]:
        self.calls += 1
        return self._raw


def _raw(node_id: str, *, paired=False, connected=False, platform=None) -> dict:
    return {
        "nodeId": node_id,
        "platform": platform,
        "caps": ["a"],
        "commands": ["x"],
        "paired": paired,
        "connected": connected,
    }


@pytest.mark.asyncio
async def test_maps_raw_dicts_to_nodes_with_derived_status():
    port = _FakePort([
        _raw("n1", connected=True),
        _raw("n2", paired=True),
        _raw("n3"),
    ])
    adapter = OpenClawNodeAdapter(port)
    nodes = await adapter.list_nodes(NodeListRequest())
    assert port.calls == 1
    assert [(n.nodeId, n.status) for n in nodes] == [
        ("n1", "online"), ("n2", "paired"), ("n3", "offline"),
    ]
    assert nodes[0].capabilities == ["a"]
    assert nodes[0].metadata["raw"]["nodeId"] == "n1"


@pytest.mark.asyncio
async def test_status_filter():
    port = _FakePort([_raw("n1", connected=True), _raw("n2", paired=True)])
    adapter = OpenClawNodeAdapter(port)
    nodes = await adapter.list_nodes(NodeListRequest(status="online"))
    assert [n.nodeId for n in nodes] == ["n1"]


@pytest.mark.asyncio
async def test_platform_filter():
    port = _FakePort([
        _raw("n1", connected=True, platform="linux"),
        _raw("n2", connected=True, platform="darwin"),
    ])
    adapter = OpenClawNodeAdapter(port)
    nodes = await adapter.list_nodes(NodeListRequest(platform="darwin"))
    assert [n.nodeId for n in nodes] == ["n2"]


@pytest.mark.asyncio
async def test_paging_offset_and_limit():
    port = _FakePort([_raw(f"n{i}", connected=True) for i in range(5)])
    adapter = OpenClawNodeAdapter(port)
    nodes = await adapter.list_nodes(NodeListRequest(offset=1, limit=2))
    assert [n.nodeId for n in nodes] == ["n1", "n2"]


@pytest.mark.asyncio
async def test_bad_entry_is_skipped_not_fatal():
    # A malformed entry (not a dict) is skipped with a warning, not raised.
    port = _FakePort([_raw("n1", connected=True), 12345])  # type: ignore[list-item]
    adapter = OpenClawNodeAdapter(port)
    nodes = await adapter.list_nodes(NodeListRequest())
    assert [n.nodeId for n in nodes] == ["n1"]

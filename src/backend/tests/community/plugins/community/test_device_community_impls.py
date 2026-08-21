"""Unit tests for the B6 community device impls (real, vendor-free, no-op-by-design).

Covers:
- ``CommunityHealthProbe`` — direct-HTTP /readiness probe (all branches: no
  bindings, no-url binding, healthy/unhealthy/exception probes, payload parsing,
  list_bindings failure, sandbox unsupported).
- ``CommunityDeviceSyncDispatcher`` / ``CommunityDeviceSyncPlugin`` — no-op sync.
- ``CommunityDeviceAdapterTransport`` — no-op relay transport.

These ship in the community distribution, so they must be exercised directly
(the contract/wiring suites only prove they're *bound*).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from agentclaw.community.plugins.community.device_adapter_transport import (
    CommunityDeviceAdapterTransport,
)
from agentclaw.community.plugins.community.device_sync import (
    CommunityDeviceSyncDispatcher,
    CommunityDeviceSyncPlugin,
)
from agentclaw.community.plugins.community.health_probe import CommunityHealthProbe


# ── helpers ──────────────────────────────────────────────────────────────────

def _binding(props: dict | None, device_id: str = "dev-1", bid: int = 1):
    return SimpleNamespace(device_props=props, device_id=device_id, id=bid)


def _repo(bindings=None, *, raises: bool = False):
    repo = MagicMock()
    if raises:
        repo.list_bindings.side_effect = RuntimeError("db down")
    else:
        repo.list_bindings.return_value = (len(bindings or []), list(bindings or []))
    return repo


def _resp(status: int = 200, json_body=None, content: bytes = b"{}"):
    r = MagicMock()
    r.status_code = status
    r.content = content
    if isinstance(json_body, Exception):
        r.json.side_effect = json_body
    else:
        r.json.return_value = json_body
    return r


def _client(*, response=None, get_error: Exception | None = None):
    """A fake async ``httpx.AsyncClient`` context manager."""
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.get = AsyncMock(side_effect=get_error) if get_error else AsyncMock(return_value=response)
    return c


# ── CommunityHealthProbe ─────────────────────────────────────────────────────

def test_health_probe_mode_label():
    assert CommunityHealthProbe(binding_repo=_repo([])).mode_label == "community"


def test_engine_health_no_bindings_returns_empty():
    probe = CommunityHealthProbe(binding_repo=_repo([]))
    assert asyncio.run(probe.engine_health("staff-1")) == []


def test_list_bindings_failure_degrades_to_empty():
    probe = CommunityHealthProbe(binding_repo=_repo(raises=True))
    assert asyncio.run(probe.engine_health("staff-1")) == []
    assert asyncio.run(probe.readiness("staff-1", 0)) == []
    assert asyncio.run(probe.bots_health("staff-1")) == []


def test_engine_health_probes_ready_with_payload():
    b = _binding({"bolt_id": "bot-9", "url": "http://engine:20003/"})
    probe = CommunityHealthProbe(binding_repo=_repo([b]))
    resp = _resp(200, {"state": "ready", "version": "1.4.2"})
    with patch("httpx.AsyncClient", return_value=_client(response=resp)) as ac:
        out = asyncio.run(probe.engine_health("staff-1"))
    # URL is built from device_props url + /readiness (trailing slash stripped)
    ac.return_value.get.assert_awaited_once_with("http://engine:20003/readiness")
    assert out[0]["bot_id"] == "bot-9"
    assert out[0]["state"] == "ready"
    assert out[0]["version"] == "1.4.2"
    assert out[0]["engine"] == "openclaw"


def test_probe_non_200_is_unhealthy():
    b = _binding({"http_url": "http://engine:20003"})  # http_url fallback key
    probe = CommunityHealthProbe(binding_repo=_repo([b]))
    with patch("httpx.AsyncClient", return_value=_client(response=_resp(503, None, content=b""))):
        out = asyncio.run(probe.engine_health("staff-1"))
    assert out[0]["state"] == "unhealthy"
    assert out[0]["message"] == "container probe failed"


def test_probe_transport_error_is_unhealthy_not_raised():
    b = _binding({"url": "http://engine:20003"})
    probe = CommunityHealthProbe(binding_repo=_repo([b]))
    with patch("httpx.AsyncClient", return_value=_client(get_error=httpx.ConnectError("boom"))):
        out = asyncio.run(probe.engine_health("staff-1"))
    assert out[0]["state"] == "unhealthy"
    assert "container probe failed" in out[0]["reason"]


def test_probe_non_dict_json_falls_back_to_ready():
    b = _binding({"url": "http://engine:20003"})
    probe = CommunityHealthProbe(binding_repo=_repo([b]))
    resp = _resp(200, [1, 2, 3])  # JSON array, not a dict
    with patch("httpx.AsyncClient", return_value=_client(response=resp)):
        out = asyncio.run(probe.engine_health("staff-1"))
    assert out[0]["state"] == "ready"
    assert out[0]["version"] is None


def test_probe_bad_json_swallowed():
    b = _binding({"url": "http://engine:20003"})
    probe = CommunityHealthProbe(binding_repo=_repo([b]))
    resp = _resp(200, ValueError("not json"), content=b"<html>")
    with patch("httpx.AsyncClient", return_value=_client(response=resp)):
        out = asyncio.run(probe.engine_health("staff-1"))
    assert out[0]["state"] == "ready"  # healthy=200, payload {} → "ready"


def test_probe_no_url_is_unknown():
    b = _binding({"bolt_id": "bot-x"})  # no url / http_url
    probe = CommunityHealthProbe(binding_repo=_repo([b]))
    out = asyncio.run(probe.engine_health("staff-1"))
    assert out[0]["state"] == "unknown"
    assert out[0]["reason"] == "no engine url in device_props"


def test_probe_none_device_props_is_unknown():
    b = _binding(None)  # device_props is None → {} → no url
    probe = CommunityHealthProbe(binding_repo=_repo([b]))
    out = asyncio.run(probe.engine_health("staff-1"))
    assert out[0]["bot_id"] == "unknown"
    assert out[0]["state"] == "unknown"


def test_bots_health_formats_healthy_flag():
    ready = _binding({"url": "http://e:1"}, device_id="d-ok")
    probe = CommunityHealthProbe(binding_repo=_repo([ready]))
    with patch("httpx.AsyncClient", return_value=_client(response=_resp(200, {"state": "ready"}))):
        out = asyncio.run(probe.bots_health("staff-1"))
    assert out == [{
        "bot_id": "unknown", "device_id": "d-ok", "healthy": True,
        "engine_type": "openclaw", "error": None,
    }]


def test_readiness_probes_bindings():
    b = _binding({"url": "http://e:1"})
    probe = CommunityHealthProbe(binding_repo=_repo([b]))
    with patch("httpx.AsyncClient", return_value=_client(response=_resp(200, {"state": "ready"}))):
        out = asyncio.run(probe.readiness("staff-1", grace_seconds=5))
    assert out[0]["state"] == "ready"


def test_sandbox_health_unsupported():
    probe = CommunityHealthProbe(binding_repo=_repo([]))
    out = asyncio.run(probe.sandbox_health("bot-1", "owner-1"))
    assert out["code"] == 1
    assert out["instances"] == []
    assert "no sandbox runtime" in out["message"]


# ── CommunityDeviceSyncDispatcher / CommunityDeviceSyncPlugin ─────────────────

def test_device_sync_dispatcher_returns_noop_plugin():
    ctx = SimpleNamespace(bot_id="bot-1", provider="baas")
    plugin = CommunityDeviceSyncDispatcher().dispatch(ctx)
    assert isinstance(plugin, CommunityDeviceSyncPlugin)


def test_device_sync_plugin_noop_results():
    p = CommunityDeviceSyncPlugin()
    assert p.sync_symlinks([{"source": "a", "target": "b"}])["success"] is False
    assert p.sync_bot_config("bot", 1, "1", "OWNER", "u", "nick")["success"] is False
    # MCP bool methods return True (Option B: counted, no network call)
    assert p.sync_all_mcp_servers([{"server_code": "x"}]) is True
    assert p.sync_single_mcp({"server_code": "x"}, api_key="k") is True
    assert p.sync_remove_mcp("x") is True
    assert p.has_mcp("x") is True


# ── CommunityDeviceAdapterTransport ──────────────────────────────────────────

def test_adapter_transport_invoke_is_noop_envelope():
    out = asyncio.run(
        CommunityDeviceAdapterTransport().invoke(
            conn_info={"url": "http://x"}, method="POST", path="/api/cron", body={"a": 1},
        )
    )
    assert out["success"] is False
    assert "no device adapter" in out["message"]

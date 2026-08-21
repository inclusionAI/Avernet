"""Shared BaasDeviceSyncService behavior over an injected transport.

ctor 现在接 transport(strategy pattern,见
docs/superpowers/specs/2026-06-17-baas-transport-bot-type-strategy-design.md)。
业务逻辑(body 拼接、错误处理)完全共享,只通过 dispatcher 注入不同 transport
区分 desktop / cloud baas。

sync_symlinks 必须是 sync — 11 个 call site 不 await(见
docs/superpowers/plans/2026-05-18-fix-baas-device-sync-async-mismatch.md)。
"""
from unittest.mock import MagicMock, patch

import httpx


def _conn_info() -> dict:
    return {
        "device_provider": "baas",
        "bind_id": 42,
        "paas_device_id": "BOT-abc",
        "baas_base_url": "https://secbaas-prod.alipay.com",
        "engine_port": 20003,
        "tenant": "team_claw",
        "headers": {"x-proxypass-token": "tok-xyz"},
    }


def _ok_response(*, json_body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=200,
        json=json_body or {"ok": True},
        request=httpx.Request("POST", "http://fake/"),
    )


def _make_transport(*, response: httpx.Response | None = None, side_effect=None):
    """Build a transport mock with .post / .post_multipart configured."""
    transport = MagicMock()
    if side_effect is not None:
        transport.post.side_effect = side_effect
        transport.post_multipart.side_effect = side_effect
    else:
        resp = response if response is not None else _ok_response()
        transport.post.return_value = resp
        transport.post_multipart.return_value = resp
    return transport


# ── sync_symlinks ────────────────────────────────────────────────────────


def test_sync_symlinks_empty_calls_clean_endpoint_via_transport():
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    transport = _make_transport()
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())

    result = plugin.sync_symlinks([])

    assert result["success"] is True
    transport.post.assert_called_once_with(
        "/api/skills/symlink/clean",
        json={"directories": ["/home/admin/.openclaw/workspace/skills"]},
    )


def test_sync_symlinks_nonempty_calls_bindpath_endpoint_via_transport():
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    transport = _make_transport()
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())

    symlinks = [
        {"source": "./skills-repo/foo", "target": "foo"},
        {"source": "./skills-repo/bar", "target": "bar"},
    ]
    result = plugin.sync_symlinks(symlinks)

    assert result["success"] is True
    transport.post.assert_called_once_with(
        "/api/skills/symlink/bindpath",
        json={"symlinks": symlinks, "clean_target_dir": True},
    )


def test_sync_symlinks_returns_failure_on_http_error():
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    err_resp = httpx.Response(
        status_code=500,
        content=b"server error",
        request=httpx.Request("POST", "http://fake/"),
    )
    transport = _make_transport(response=err_resp)
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())

    result = plugin.sync_symlinks([{"source": "x", "target": "y"}])

    assert result["success"] is False
    assert "500" in result["message"] or "HTTP" in result["message"]


def test_sync_symlinks_returns_failure_on_request_error():
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    transport = _make_transport(
        side_effect=httpx.RequestError("connection refused")
    )
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())

    result = plugin.sync_symlinks([])

    assert result["success"] is False
    assert "请求失败" in result["message"] or "connection" in result["message"]


def test_sync_symlinks_is_not_async():
    """Regression guard — 11 call sites 不 await。"""
    import inspect
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    assert not inspect.iscoroutinefunction(BaasDeviceSyncService.sync_symlinks)


# ── sync_bot_config ──────────────────────────────────────────────────────


def test_sync_bot_config_calls_transport_with_role_visibility():
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    transport = _make_transport(
        response=_ok_response(json_body={"message": "ok", "data": {"x": 1}})
    )
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())

    result = plugin.sync_bot_config(
        bot_id="bot_xyz",
        binding_id=99,
        public="1",
        permission_owner="owner",
        user_id="u_1",
        nick_name="Alice",
    )

    assert result["success"] is True
    transport.post.assert_called_once_with(
        "/api/bot/config",
        json={"role": "OWNER", "visibility": "PUBLIC"},
    )


def test_sync_bot_config_returns_failure_on_empty_binding_id():
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    transport = _make_transport()
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())

    result = plugin.sync_bot_config(
        bot_id="bot_xyz", binding_id=0, public="1",
        permission_owner="owner", user_id="u_1", nick_name="Alice",
    )

    assert result["success"] is False
    transport.post.assert_not_called()


def test_sync_bot_config_returns_failure_on_http_error():
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    err_resp = httpx.Response(
        status_code=500, content=b"server error",
        request=httpx.Request("POST", "http://fake/"),
    )
    transport = _make_transport(response=err_resp)
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())

    result = plugin.sync_bot_config(
        bot_id="bot_xyz", binding_id=99, public="1",
        permission_owner="owner", user_id="u_1", nick_name="Alice",
    )

    assert result["success"] is False


def test_sync_bot_config_returns_failure_on_request_error():
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    transport = _make_transport(
        side_effect=httpx.RequestError("connection refused")
    )
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())

    result = plugin.sync_bot_config(
        bot_id="bot_xyz", binding_id=99, public="1",
        permission_owner="owner", user_id="u_1", nick_name="Alice",
    )

    assert result["success"] is False


def test_sync_bot_config_visibility_private_when_public_zero():
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    transport = _make_transport()
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())

    plugin.sync_bot_config(
        bot_id="bot_xyz", binding_id=99, public="0",
        permission_owner="caller", user_id="u_1", nick_name="Alice",
    )

    call_kwargs = transport.post.call_args.kwargs
    assert call_kwargs["json"] == {"role": "CALLER", "visibility": "PRIVATE"}


# ── MCP delegation(收编后走注入的 transport，与 symlinks 同款平台分流） ─


def test_mcp_delegations_go_through_transport():
    """MCP 4 方法把注入的 transport 传给 mcp_device_transport.*，让平台自决。"""
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    transport = _make_transport()
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())

    with patch("agentclaw.community.core.devices.services.baas_device_sync.mcp_transport") as mt:
        mt.filter_servers.return_value = True
        mt.push_single_mcp.return_value = True
        mt.remove_mcp.return_value = True
        mt.probe_mcp.return_value = True
        assert plugin.sync_all_mcp_servers([]) is True
        assert plugin.sync_single_mcp({"server_code": "a"}) is True
        assert plugin.sync_remove_mcp("a") is True
        assert plugin.has_mcp("a") is True

    mt.probe_mcp.assert_called_once_with(transport, "a")
    mt.remove_mcp.assert_called_once_with(transport, "a")
    mt.filter_servers.assert_called_once_with(transport, [])


# ── Desktop parity (single BaasDeviceSyncService, transport-only differential)


def test_desktop_baas_runs_same_business_logic_via_single_service():
    """one ``BaasDeviceSyncService`` covers cloud + desktop; the desktop
    differential is transport-only (no ``DesktopBaasDeviceSyncService``
    subclass). The single service runs the same business logic regardless of
    the injected transport."""
    from agentclaw.community.core.devices.services.baas_device_sync import (
        BaasDeviceSyncService,
    )

    transport = _make_transport()
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())
    plugin.sync_symlinks([])

    transport.post.assert_called_once_with(
        "/api/skills/symlink/clean",
        json={"directories": ["/home/admin/.openclaw/workspace/skills"]},
    )


def test_sync_symlinks_returns_failure_on_generic_exception():
    """transport 抛非 httpx 通用 Exception → sync_symlinks 返 success=False(L109-111 兜底)。"""
    from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService

    transport = MagicMock()
    transport.post.side_effect = RuntimeError("unexpected boom")
    plugin = BaasDeviceSyncService(transport=transport, conn_info=_conn_info())
    result = plugin.sync_symlinks([])

    assert result["success"] is False
    assert "同步失败" in result["message"]

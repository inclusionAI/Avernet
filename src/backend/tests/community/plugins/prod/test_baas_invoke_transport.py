"""BaasInvokeTransport / DesktopBaasInvokeTransport — 2 个 transport 类的契约测试。

设计意图见 docs/superpowers/specs/2026-06-17-baas-transport-bot-type-strategy-design.md。

- BaasInvokeTransport 走 BaasService.invoke_http(get_http_info → http_url → POST),
  适用云上 baas 容器(personal+baas / service+baas)。
- DesktopBaasInvokeTransport 自拼 secbaas transparent proxy URL 直接 httpx POST,
  适用 desktop bot(VM 在用户机器,BaaS get_http_info 返 localhost 拨不通)。
"""
from unittest.mock import MagicMock, patch

import httpx
import pytest


def _ok_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        json={"ok": True},
        request=httpx.Request("POST", "http://fake/"),
    )


# ── BaasInvokeTransport ──────────────────────────────────────────────────


def test_baas_invoke_transport_post_calls_baas_service_invoke_http():
    """BaasInvokeTransport.post 委托给 baas_service.invoke_http 带正确参数。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.return_value = _ok_response()

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc
    )
    resp = transport.post("/api/skills/symlink/bindpath", json={"x": 1})

    assert resp.status_code == 200
    svc.invoke_http.assert_called_once_with(
        bind_id=42,
        port=20003,
        path="/api/skills/symlink/bindpath",
        json={"x": 1},
        tenant="team_claw",
        # cloud baas containers reach the engine via the agentclawproxy /proxypass
        # gateway, which authenticates with x-proxypass-token (openclawToken → 401).
        auth_header="x-proxypass-token",
        # default (no multi-instance lock) → device_uuid=None, BaaS auto-picks active
        device_uuid=None,
    )


def test_baas_invoke_transport_post_multipart_calls_invoke_http_with_files():
    """post_multipart 走 invoke_http 的 files+data 路径(对应 BaasDeviceFileSystem.write_file)。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.return_value = _ok_response()

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc
    )
    resp = transport.post_multipart(
        "/api/file/upload",
        files={"file": ("foo.txt", b"hello")},
        data={"target_path": "foo.txt"},
    )

    assert resp.status_code == 200
    call_kwargs = svc.invoke_http.call_args.kwargs
    assert call_kwargs["bind_id"] == 42
    assert call_kwargs["path"] == "/api/file/upload"
    assert call_kwargs["files"] == {"file": ("foo.txt", b"hello")}
    assert call_kwargs["data"] == {"target_path": "foo.txt"}
    # multipart upload goes through the same proxypass gateway → needs the token header.
    assert call_kwargs["auth_header"] == "x-proxypass-token"


def test_baas_invoke_transport_all_http_methods_use_proxypass_token_header():
    """BaaS MCP probe/update/delete 也走 agentclawproxy proxypass，不能退回 openclawToken。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.return_value = _ok_response()

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc
    )

    transport.get("/api/mcp/a")
    transport.put("/api/mcp/a", json={"server_code": "a"})
    transport.delete("/api/mcp/a")

    calls = svc.invoke_http.call_args_list
    assert [call.kwargs["method"] for call in calls] == ["GET", "PUT", "DELETE"]
    assert all(call.kwargs["auth_header"] == "x-proxypass-token" for call in calls)


def test_baas_invoke_transport_propagates_request_error():
    """invoke_http 抛 RequestError 时 transport.post 透传。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.side_effect = httpx.RequestError("connection refused")

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc
    )
    with pytest.raises(httpx.RequestError):
        transport.post("/api/x", json={})


# ── DesktopBaasInvokeTransport ───────────────────────────────────────────


def test_desktop_baas_invoke_transport_post_uses_self_built_url():
    """DesktopBaasInvokeTransport.post 自拼 secbaas wrapper URL,headers 来自 conn_info。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import DesktopBaasInvokeTransport

    transport = DesktopBaasInvokeTransport(
        baas_base_url="https://secbaas-prod.teamclaw.com",
        tenant="team_claw",
        bot_uuid="BOT-abc",
        engine_port=20003,
        headers={"x-proxypass-token": "tok-xyz"},
    )

    with patch(
        "agentclaw.community.core.devices.services.baas_invoke_transport.httpx.Client"
    ) as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _ok_response()

        resp = transport.post("/api/skills/symlink/bindpath", json={"x": 1})

    assert resp.status_code == 200
    expected_url = (
        "https://secbaas-prod.teamclaw.com/api/v1/bots/team_claw/"
        "BOT-abc/invoke-http/20003/api/skills/symlink/bindpath"
    )
    mock_client.post.assert_called_once()
    args, kwargs = mock_client.post.call_args.args, mock_client.post.call_args.kwargs
    assert args[0] == expected_url
    assert kwargs["json"] == {"x": 1}
    assert kwargs["headers"] == {"x-proxypass-token": "tok-xyz"}


def test_desktop_baas_invoke_transport_invoke_url_adds_leading_slash():
    """_invoke_url 对没有 leading slash 的 path 也能正确拼接。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import DesktopBaasInvokeTransport

    transport = DesktopBaasInvokeTransport(
        baas_base_url="https://b", tenant="t", bot_uuid="u",
        engine_port=20003, headers={},
    )

    assert transport._invoke_url("api/x") == (
        "https://b/api/v1/bots/t/u/invoke-http/20003/api/x"
    )
    assert transport._invoke_url("/api/x") == (
        "https://b/api/v1/bots/t/u/invoke-http/20003/api/x"
    )


def test_desktop_baas_invoke_transport_post_multipart_uses_self_built_url():
    """post_multipart 同样自拼 URL,但传 files+data 而不是 json。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import DesktopBaasInvokeTransport

    transport = DesktopBaasInvokeTransport(
        baas_base_url="https://b", tenant="t", bot_uuid="u",
        engine_port=20003, headers={"h": "1"},
    )

    with patch(
        "agentclaw.community.core.devices.services.baas_invoke_transport.httpx.Client"
    ) as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _ok_response()

        transport.post_multipart(
            "/api/file/upload",
            files={"file": ("foo.txt", b"hi")},
            data={"target_path": "foo.txt"},
        )

    call_args = mock_client.post.call_args
    assert call_args.args[0] == "https://b/api/v1/bots/t/u/invoke-http/20003/api/file/upload"
    assert call_args.kwargs["files"] == {"file": ("foo.txt", b"hi")}
    assert call_args.kwargs["data"] == {"target_path": "foo.txt"}
    assert call_args.kwargs["headers"] == {"h": "1"}


def test_desktop_baas_invoke_transport_post_propagates_request_error():
    """httpx 抛 RequestError 时 transport.post 透传(不吞)。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import DesktopBaasInvokeTransport

    transport = DesktopBaasInvokeTransport(
        baas_base_url="https://b", tenant="t", bot_uuid="u",
        engine_port=20003, headers={},
    )

    with patch(
        "agentclaw.community.core.devices.services.baas_invoke_transport.httpx.Client"
    ) as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.RequestError("connection refused")

        with pytest.raises(httpx.RequestError):
            transport.post("/api/x", json={})


def test_baas_invoke_get_put_delete_pass_method():
    """BaasInvokeTransport get/put/delete 走 invoke_http 对应 method。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport
    svc = MagicMock()
    svc.invoke_http.return_value = _ok_response()
    t = BaasInvokeTransport(bind_id=1, engine_port=20003, tenant="t", baas_service=svc)
    t.get("/api/mcp/x")
    assert svc.invoke_http.call_args.kwargs["method"] == "GET"
    t.put("/api/mcp/x", json={"a": 1})
    assert svc.invoke_http.call_args.kwargs["method"] == "PUT"
    t.delete("/api/mcp/x")
    assert svc.invoke_http.call_args.kwargs["method"] == "DELETE"


def test_desktop_invoke_get_put_delete_hit_wrapper_url():
    from agentclaw.community.core.devices.services.baas_invoke_transport import DesktopBaasInvokeTransport
    t = DesktopBaasInvokeTransport(baas_base_url="https://b", tenant="t", bot_uuid="u",
                                   engine_port=20003, headers={"h": "1"})
    expected = "https://b/api/v1/bots/t/u/invoke-http/20003/api/mcp/x"
    for verb in ("get", "put", "delete"):
        with patch("agentclaw.community.core.devices.services.baas_invoke_transport.httpx.Client") as mc:
            mock = MagicMock()
            mc.return_value.__enter__.return_value = mock
            getattr(mock, verb).return_value = _ok_response()
            getattr(t, verb)("/api/mcp/x")
            assert getattr(mock, verb).call_args.args[0] == expected


# ── device_uuid 透传(多实例 service bot) ───────────────────────────────


@pytest.mark.parametrize(
    "method,call_extra",
    [
        ("post", {"json": {"x": 1}}),
        ("get", {}),
        ("put", {"json": {"a": 1}}),
        ("delete", {}),
    ],
)
def test_baas_invoke_transport_passes_device_uuid_to_invoke_http(method, call_extra):
    """BaasInvokeTransport(device_uuid=...) 的 post/get/put/delete 把 device_uuid 透传给 invoke_http。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.return_value = _ok_response()
    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw",
        baas_service=svc, device_uuid="DEV-xyz",
    )

    getattr(transport, method)("/api/mcp/x", **call_extra)

    kwargs = svc.invoke_http.call_args.kwargs
    assert kwargs["device_uuid"] == "DEV-xyz"
    assert kwargs["bind_id"] == 42
    assert kwargs["port"] == 20003
    assert kwargs["path"] == "/api/mcp/x"
    assert kwargs["auth_header"] == "x-proxypass-token"


def test_baas_invoke_transport_post_multipart_passes_device_uuid_to_invoke_http():
    """post_multipart 也把 device_uuid 透传给 invoke_http(写文件 multipart 链路)."""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.return_value = _ok_response()
    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw",
        baas_service=svc, device_uuid="DEV-xyz",
    )

    transport.post_multipart(
        "/api/file/upload",
        files={"file": ("foo.txt", b"hi")},
        data={"target_path": "foo.txt"},
    )

    kwargs = svc.invoke_http.call_args.kwargs
    assert kwargs["device_uuid"] == "DEV-xyz"
    assert kwargs["files"] == {"file": ("foo.txt", b"hi")}
    assert kwargs["data"] == {"target_path": "foo.txt"}


def test_baas_invoke_transport_default_device_uuid_is_none():
    """不传 device_uuid 时 invoke_http 收到 device_uuid=None(单实例 / 未指定)。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.return_value = _ok_response()
    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc,
    )

    transport.post("/api/x", json={})

    assert svc.invoke_http.call_args.kwargs["device_uuid"] is None

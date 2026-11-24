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


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json={"ok": status_code == 200},
        request=httpx.Request("POST", "http://fake/"),
    )


def _desktop(**overrides):
    """Build a desktop transport over a mock pooled client.

    The transport no longer opens an ``httpx.Client`` per call — the resolver
    injects one pooled client so a multi-file write does not pay a TLS handshake
    per file — so tests hand it a mock directly instead of patching module state.
    Returns ``(transport, client)``.
    """
    from agentclaw.community.core.devices.services.baas_invoke_transport import (
        DesktopBaasInvokeTransport,
    )

    client = MagicMock()
    kwargs = {
        "baas_base_url": "https://b",
        "tenant": "t",
        "bot_uuid": "u",
        "engine_port": 20003,
        "headers": {"h": "1"},
    }
    kwargs.update(overrides)
    return DesktopBaasInvokeTransport(client=client, **kwargs), client


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
        method="POST",
        json={"x": 1},
        tenant="team_claw",
        # cloud baas containers reach the engine via the agentclawproxy /proxypass
        # gateway, which authenticates with x-proxypass-token (openclawToken → 401).
        auth_header="x-proxypass-token",
        # default (no multi-instance lock) → device_uuid=None, BaaS auto-picks active
        device_uuid=None,
        # the transport resolves http_info itself and hands it to invoke_http, so a
        # batch of same-path calls shares one get_http_info round trip
        http_info=svc.get_http_info.return_value,
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
    transport, mock_client = _desktop(
        baas_base_url="https://secbaas-prod.teamclaw.com",
        tenant="team_claw",
        bot_uuid="BOT-abc",
        headers={"x-proxypass-token": "tok-xyz"},
    )
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
    transport, _ = _desktop(headers={})

    assert transport._invoke_url("api/x") == (
        "https://b/api/v1/bots/t/u/invoke-http/20003/api/x"
    )
    assert transport._invoke_url("/api/x") == (
        "https://b/api/v1/bots/t/u/invoke-http/20003/api/x"
    )


def test_desktop_baas_invoke_transport_post_multipart_uses_self_built_url():
    """post_multipart 同样自拼 URL,但传 files+data 而不是 json。"""
    transport, mock_client = _desktop()
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
    transport, mock_client = _desktop(headers={})
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
    expected = "https://b/api/v1/bots/t/u/invoke-http/20003/api/mcp/x"
    for verb in ("get", "put", "delete"):
        t, mock = _desktop()
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


# ── get_http_info sharing (per-path cache) ───────────────────────────────


def test_repeated_same_path_calls_resolve_http_info_once():
    """一次 package upload 的 N 个 /api/file/upload 只解析一次 http_info。

    Resolution is a binding DB lookup plus a BaaS round trip, so doing it per file
    doubled the request count of every multi-file write.
    """
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.return_value = _ok_response()
    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc
    )

    for name in ("a.md", "b.md", "c.md"):
        transport.post_multipart(
            "/api/file/upload",
            files={"file": (name, b"x")},
            data={"target_path": name},
        )

    assert svc.get_http_info.call_count == 1
    assert svc.invoke_http.call_count == 3
    # every upload still went out against that one resolution
    assert all(
        call.kwargs["http_info"] is svc.get_http_info.return_value
        for call in svc.invoke_http.call_args_list
    )


def test_http_info_cache_is_keyed_by_path():
    """``http_url`` 里含 path，所以不同 path 必须各自解析，不能串用缓存。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.return_value = _ok_response()
    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc
    )

    transport.post("/api/file/read", json={})
    transport.post("/api/file/list", json={})
    transport.post("/api/file/read", json={})

    resolved_paths = [c.kwargs["path"] for c in svc.get_http_info.call_args_list]
    assert resolved_paths == ["/api/file/read", "/api/file/list"]


def test_http_info_resolution_carries_the_instance_identity():
    """缓存 key 只有 path，因为其余解析输入都是实例固定的 —— 这里把它钉住。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.return_value = _ok_response()
    transport = BaasInvokeTransport(
        bind_id=7, engine_port=20003, tenant="team_claw",
        baas_service=svc, device_uuid="DEV-xyz",
    )

    transport.post("/api/file/read", json={})

    svc.get_http_info.assert_called_once_with(
        bind_id=7,
        port=20003,
        path="/api/file/read",
        tenant="team_claw",
        device_uuid="DEV-xyz",
    )


def test_rejected_token_refreshes_http_info_and_retries_once():
    """缓存的 token 过期时刷新重试，调用方不该看到假 401。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    stale, fresh = MagicMock(name="stale"), MagicMock(name="fresh")
    svc.get_http_info.side_effect = [stale, fresh]
    svc.invoke_http.side_effect = [_response(401), _ok_response()]

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc
    )
    resp = transport.post_multipart(
        "/api/file/upload", files={"file": ("a", b"x")}, data={"target_path": "a"}
    )

    assert resp.status_code == 200
    assert svc.get_http_info.call_count == 2
    assert [c.kwargs["http_info"] for c in svc.invoke_http.call_args_list] == [
        stale, fresh,
    ]
    # the refreshed entry replaces the rejected one for subsequent calls
    svc.invoke_http.side_effect = None
    svc.invoke_http.return_value = _ok_response()
    transport.post_multipart(
        "/api/file/upload", files={"file": ("b", b"y")}, data={"target_path": "b"}
    )
    assert svc.get_http_info.call_count == 2
    assert svc.invoke_http.call_args.kwargs["http_info"] is fresh


def test_a_403_is_the_device_answer_and_is_never_replayed():
    """403 不是网关拒 token —— engine 把 PermissionError 映射成 403，proxy 原样透传。

    重放会把一次 mutating 的 upload/delete 再跑一遍（无 device_uuid 时还可能打到
    另一台设备），并且掩盖 engine 真正的鉴权结论。
    """
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.return_value = _response(403)
    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc
    )

    resp = transport.post_multipart(
        "/api/file/upload", files={"file": ("a", b"x")}, data={"target_path": "a"}
    )

    assert resp.status_code == 403
    # sent exactly once, and the http_info was never re-resolved
    assert svc.invoke_http.call_count == 1
    assert svc.get_http_info.call_count == 1


def test_a_persistently_rejected_token_stops_after_one_retry():
    """真配置错时不无限重试 —— 刷新一次后如实返回 401。"""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.invoke_http.return_value = _response(401)
    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc
    )

    resp = transport.post("/api/x", json={})

    assert resp.status_code == 401
    assert svc.invoke_http.call_count == 2
    assert svc.get_http_info.call_count == 2


def test_concurrent_rejections_share_one_refresh():
    """一批并发写同时被拒时只刷新一次，而不是每个请求各刷一次。

    无条件 refresh 会在失败路径上把本 cache 消除掉的「每文件一次解析」原样带回来。
    """
    import threading

    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    # Distinct objects per call, like the real get_http_info, which builds a fresh
    # HttpConnectionInfo every time. A shared MagicMock return_value would hand back
    # one identical object and make the compare-and-swap unobservable.
    svc.get_http_info.side_effect = [MagicMock(name=f"info-{i}") for i in range(20)]
    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc
    )
    # prime the cache so every worker starts from the same (about to be rejected) entry
    primed = transport._http_info("/api/file/upload")
    assert svc.get_http_info.call_count == 1

    barrier = threading.Barrier(8)

    def refresh_once():
        barrier.wait()
        transport._http_info("/api/file/upload", stale=primed)

    workers = [threading.Thread(target=refresh_once) for _ in range(8)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    # one thread re-resolved; the other seven took the entry it installed
    assert svc.get_http_info.call_count == 2


def test_expired_http_info_cache_entry_is_re_resolved(monkeypatch):
    """TTL 过期后重新解析，避免长批次里一直用老 token。"""
    import agentclaw.community.core.devices.services.baas_invoke_transport as mod

    svc = MagicMock()
    svc.invoke_http.return_value = _ok_response()
    transport = mod.BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc
    )

    clock = {"now": 1_000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["now"])

    transport.post("/api/x", json={})
    clock["now"] += mod._HTTP_INFO_TTL_SECONDS / 2
    transport.post("/api/x", json={})
    assert svc.get_http_info.call_count == 1

    clock["now"] += mod._HTTP_INFO_TTL_SECONDS
    transport.post("/api/x", json={})
    assert svc.get_http_info.call_count == 2


# ── desktop connection reuse ─────────────────────────────────────────────


def test_desktop_transport_sends_every_call_through_the_one_injected_client():
    """一个 package 的多次写共用同一个 client，而不是各自新建。"""
    transport, pooled = _desktop()
    pooled.post.return_value = _ok_response()

    with patch(
        "agentclaw.community.core.devices.services.baas_invoke_transport.httpx.Client"
    ) as mock_cls:
        for name in ("a.md", "b.md", "c.md"):
            transport.post_multipart(
                "/api/file/upload",
                files={"file": (name, b"x")},
                data={"target_path": name},
            )

    # all three writes landed on the one pooled client ...
    assert pooled.post.call_count == 3
    # ... and the transport never built a client of its own, which is what used to
    # force a fresh TCP + TLS handshake for every file in a package.
    assert mock_cls.call_count == 0


def test_build_desktop_client_returns_a_bounded_pool():
    """连接池有上限，且高于设备 I/O 并发度，不会自己变成瓶颈。"""
    import agentclaw.community.core.devices.services.baas_invoke_transport as mod

    with patch.object(mod.httpx, "Client") as mock_cls:
        mod.build_desktop_client()

    limits = mock_cls.call_args.kwargs["limits"]
    assert limits.max_connections == 64
    assert limits.max_keepalive_connections == 32


def test_the_resolver_shares_one_pooled_client_across_desktop_transports():
    """池由单例 resolver 持有 —— 每次 dispatch 不该新建一个池。"""
    from agentclaw.community.core.devices.services.device_filesystem_resolver import (
        DefaultDeviceFileSystemResolver,
    )

    resolver = DefaultDeviceFileSystemResolver(
        baas_service=MagicMock(),
        bot_repo=MagicMock(),
        binding_repo=MagicMock(),
        sandbox_client=MagicMock(),
    )
    conn_info = {
        "baas_base_url": "https://b",
        "tenant": "t",
        "paas_device_id": "BOT-abc",
        "engine_port": 20003,
        "headers": {},
    }
    ctx = MagicMock(provider="baas", bot_type="desktop", conn_info=conn_info)

    first = resolver(ctx, lambda path: path)
    second = resolver(ctx, lambda path: path)

    assert first._transport._client is second._transport._client


@pytest.mark.parametrize("cls_name", ["BaasInvokeTransport", "DesktopBaasInvokeTransport"])
def test_transports_explicitly_extend_the_baas_transport_contract(cls_name):
    """Nominal, not just structural: both strategies name ``BaasTransport`` as a base,
    so dropping one of its methods fails at instantiation rather than at first call."""
    from agentclaw.community.core.devices.services import baas_invoke_transport as mod

    assert mod.BaasTransport in getattr(mod, cls_name).__mro__


def test_baas_transport_methods_are_abstract():
    from agentclaw.community.core.devices.services.baas_invoke_transport import (
        BaasTransport,
    )

    assert BaasTransport.__abstractmethods__ == frozenset({"post", "post_multipart"})

    class _PostOnly(BaasTransport):
        def post(self, path, *, json=None):
            return _ok_response()

    with pytest.raises(TypeError, match="post_multipart"):
        _PostOnly()


# ── gateway-unavailable read failover ─────────────────────────────────────


def _http_info_with_target(target: str):
    """A resolved connection info whose ``target`` names one device instance."""
    from agentclaw.community.core.service_bot.services.baas_service import (
        HttpConnectionInfo,
    )

    return HttpConnectionInfo(
        http_url=f"https://gw/proxypass/{target}/api/file/read",
        token="tok",
        target=target,
    )


def _devices(*rows):
    return [
        {
            "device_uuid": uuid,
            "status": status,
            "provider_device_id": paas_id,
        }
        for uuid, status, paas_id in rows
    ]


def test_read_502_rotates_once_to_another_active_device():
    """Gateway 502 on an auto-selected read hands the read to another instance."""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    dead = _http_info_with_target("ARCA_ARCA-SANDBOX-dead@0:20003")
    alive = _http_info_with_target("ARCA_ARCA-SANDBOX-alive@0:20003")
    svc.get_http_info.side_effect = [dead, alive]
    svc.invoke_http.side_effect = [_response(502), _ok_response()]
    svc.list_devices_by_bot_uuid.return_value = _devices(
        ("DEVICE-1", "ACTIVE", "ARCA-SANDBOX-dead@0"),
        ("DEVICE-2", "ACTIVE", "ARCA-SANDBOX-alive@0"),
    )

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw",
        baas_service=svc, bot_uuid="BOT-1",
    )
    resp = transport.post("/api/file/read", json={"file_path": "AGENTS.md"})

    assert resp.status_code == 200
    assert svc.invoke_http.call_count == 2
    first, second = svc.invoke_http.call_args_list
    assert first.kwargs["http_info"] is dead
    assert second.kwargs["http_info"] is alive
    # the retry names the surviving instance, not the failed one
    assert second.kwargs["device_uuid"] == "DEVICE-2"
    # the alternate was resolved through BaaS's targeted device selection
    assert svc.get_http_info.call_args_list[1].kwargs["device_uuid"] == "DEVICE-2"
    # the rotation excluded the failed instance by its provider device id
    svc.list_devices_by_bot_uuid.assert_called_once_with(
        "BOT-1", tenant="team_claw"
    )


def test_rotated_read_replaces_cached_info_for_the_rest_of_the_batch():
    """After a rotation, subsequent reads go straight to the surviving device."""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    dead = _http_info_with_target("ARCA_ARCA-SANDBOX-dead@0:20003")
    alive = _http_info_with_target("ARCA_ARCA-SANDBOX-alive@0:20003")
    svc.get_http_info.side_effect = [dead, alive]
    svc.invoke_http.side_effect = [_response(502), _ok_response(), _ok_response()]
    svc.list_devices_by_bot_uuid.return_value = _devices(
        ("DEVICE-1", "ACTIVE", "ARCA-SANDBOX-dead@0"),
        ("DEVICE-2", "ACTIVE", "ARCA-SANDBOX-alive@0"),
    )

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw",
        baas_service=svc, bot_uuid="BOT-1",
    )
    assert transport.post("/api/file/read", json={}).status_code == 200
    # second read of the batch: no new resolution, no new failure hop
    assert transport.post("/api/file/read", json={}).status_code == 200

    assert svc.get_http_info.call_count == 2
    assert svc.invoke_http.call_count == 3


def test_mutation_paths_are_never_rotated():
    """upload/remove/rmtree stay single-shot — replaying a mutation can double-apply."""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    for path, call in (
        ("/api/file/upload", {"files": {"f": ("a", b"x")}, "data": {"p": "a"}}),
        ("/api/file/remove", {"json": {"file_path": "a"}}),
        ("/api/file/rmtree", {"json": {"dir_path": "d"}}),
    ):
        svc = MagicMock()
        svc.get_http_info.return_value = _http_info_with_target(
            "ARCA_ARCA-SANDBOX-x@0:20003"
        )
        svc.invoke_http.return_value = _response(502)

        transport = BaasInvokeTransport(
            bind_id=42, engine_port=20003, tenant="team_claw",
            baas_service=svc, bot_uuid="BOT-1",
        )
        if "files" in call:
            resp = transport.post_multipart(path, **call)
        else:
            resp = transport.post(path, **call)

        assert resp.status_code == 502
        assert svc.invoke_http.call_count == 1
        svc.list_devices_by_bot_uuid.assert_not_called()


@pytest.mark.parametrize("status", [502, 503, 504])
def test_read_failover_covers_all_gateway_unavailable_status(status):
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    dead = _http_info_with_target("ARCA_ARCA-SANDBOX-dead@0:20003")
    alive = _http_info_with_target("ARCA_ARCA-SANDBOX-alive@0:20003")
    svc.get_http_info.side_effect = [dead, alive]
    svc.invoke_http.side_effect = [_response(status), _ok_response()]
    svc.list_devices_by_bot_uuid.return_value = _devices(
        ("DEVICE-1", "ACTIVE", "ARCA-SANDBOX-dead@0"),
        ("DEVICE-2", "ACTIVE", "ARCA-SANDBOX-alive@0"),
    )

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw",
        baas_service=svc, bot_uuid="BOT-1",
    )
    assert transport.post("/api/file/read", json={}).status_code == 200


def test_read_failover_normalizes_device_status_case():
    """A BaaS device list whose status casing varies still rotates.

    Mirrors ``baas_device_header_updater``'s case-normalized status read: a
    lowercase status must not silently disable the failover.
    """
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    dead = _http_info_with_target("ARCA_ARCA-SANDBOX-dead@0:20003")
    alive = _http_info_with_target("ARCA_ARCA-SANDBOX-alive@0:20003")
    svc.get_http_info.side_effect = [dead, alive]
    svc.invoke_http.side_effect = [_response(502), _ok_response()]
    svc.list_devices_by_bot_uuid.return_value = _devices(
        ("DEVICE-1", "active", "ARCA-SANDBOX-dead@0"),
        ("DEVICE-2", "Active", "ARCA-SANDBOX-alive@0"),
    )

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw",
        baas_service=svc, bot_uuid="BOT-1",
    )
    resp = transport.post("/api/file/read", json={})

    assert resp.status_code == 200
    assert svc.invoke_http.call_count == 2
    assert svc.invoke_http.call_args_list[1].kwargs["device_uuid"] == "DEVICE-2"


def test_read_failover_without_another_active_device_returns_the_verdict():
    """Only-instance bot: the 502 stands, and nothing extra was attempted."""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.get_http_info.return_value = _http_info_with_target(
        "ARCA_ARCA-SANDBOX-solo@0:20003"
    )
    svc.invoke_http.return_value = _response(502)
    svc.list_devices_by_bot_uuid.return_value = _devices(
        ("DEVICE-1", "ACTIVE", "ARCA-SANDBOX-solo@0"),
    )

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw",
        baas_service=svc, bot_uuid="BOT-1",
    )
    resp = transport.post("/api/file/read", json={})

    assert resp.status_code == 502
    assert svc.invoke_http.call_count == 1


def test_read_failover_is_disabled_without_a_bot_uuid():
    """Teclaw-style callers pass no bot uuid — rotation must not fire."""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.get_http_info.return_value = _http_info_with_target(
        "ARCA_ARCA-SANDBOX-x@0:20003"
    )
    svc.invoke_http.return_value = _response(502)

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw", baas_service=svc,
    )
    resp = transport.post("/api/file/read", json={})

    assert resp.status_code == 502
    assert svc.invoke_http.call_count == 1
    svc.list_devices_by_bot_uuid.assert_not_called()


def test_read_failover_is_disabled_when_the_caller_pinned_a_device():
    """An explicitly targeted instance is the caller's decision, never rotated."""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.get_http_info.return_value = _http_info_with_target(
        "ARCA_ARCA-SANDBOX-pinned@0:20003"
    )
    svc.invoke_http.return_value = _response(502)

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw",
        baas_service=svc, device_uuid="DEVICE-pinned", bot_uuid="BOT-1",
    )
    resp = transport.post("/api/file/read", json={})

    assert resp.status_code == 502
    assert svc.invoke_http.call_count == 1
    svc.list_devices_by_bot_uuid.assert_not_called()


def test_read_failover_skips_when_the_failed_instance_is_not_identifiable():
    """No parseable target → the retry could land on the same dead device, so it doesn't run."""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.get_http_info.return_value = _http_info_with_target("")
    svc.invoke_http.return_value = _response(502)

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw",
        baas_service=svc, bot_uuid="BOT-1",
    )
    resp = transport.post("/api/file/read", json={})

    assert resp.status_code == 502
    assert svc.invoke_http.call_count == 1
    # without a failed-instance identity there is nothing to exclude, so the
    # device list is never even consulted
    svc.list_devices_by_bot_uuid.assert_not_called()


def test_device_404_is_still_the_devices_answer_and_never_rotates():
    """A device-authored 404 (file absent) is answered, not retried elsewhere."""
    from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport

    svc = MagicMock()
    svc.get_http_info.return_value = _http_info_with_target(
        "ARCA_ARCA-SANDBOX-x@0:20003"
    )
    svc.invoke_http.return_value = _response(404)

    transport = BaasInvokeTransport(
        bind_id=42, engine_port=20003, tenant="team_claw",
        baas_service=svc, bot_uuid="BOT-1",
    )
    resp = transport.post("/api/file/read", json={})

    assert resp.status_code == 404
    assert svc.invoke_http.call_count == 1


def test_resolver_passes_the_baas_bot_uuid_to_the_cloud_transport():
    """The cloud baas transport must be able to name the bot for read failover."""
    from agentclaw.community.core.devices.services.device_filesystem_resolver import (
        DefaultDeviceFileSystemResolver,
    )
    from agentclaw.community.core.devices.services.baas_invoke_transport import (
        BaasInvokeTransport,
    )

    resolver = DefaultDeviceFileSystemResolver(
        baas_service=MagicMock(),
        bot_repo=MagicMock(),
        binding_repo=MagicMock(),
        sandbox_client=MagicMock(),
    )
    conn_info = {
        "bind_id": 7,
        "tenant": "team_claw",
        "paas_device_id": "BOT-uuid-1",
        "engine_port": 20003,
    }
    ctx = MagicMock(provider="baas", bot_type="service", conn_info=conn_info)

    fs = resolver(ctx, lambda path: path)

    assert isinstance(fs._transport, BaasInvokeTransport)
    assert fs._transport._bot_uuid == "BOT-uuid-1"

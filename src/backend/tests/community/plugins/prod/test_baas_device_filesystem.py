"""BaasDeviceFileSystem / DesktopBaasDeviceFileSystem — transport-based.

8 个 file 方法走 transport.post / transport.post_multipart。write_file 是
multipart,其他都是 json POST。
"""
from unittest.mock import MagicMock

import httpx
import pytest


def _conn_info() -> dict:
    return {
        "bind_id": 42,
        "paas_device_id": "BOT-abc",
        "engine_port": 20003,
        "tenant": "team_claw",
    }


def _ok_response(*, json_body=None, content=None):
    if content is not None:
        return httpx.Response(
            status_code=200, content=content,
            request=httpx.Request("POST", "http://fake/"),
        )
    return httpx.Response(
        status_code=200, json=json_body or {"ok": True},
        request=httpx.Request("POST", "http://fake/"),
    )


def _make_transport(*, response=None, side_effect=None):
    t = MagicMock()
    if side_effect is not None:
        t.post.side_effect = side_effect
        t.post_multipart.side_effect = side_effect
    else:
        resp = response if response is not None else _ok_response()
        t.post.return_value = resp
        t.post_multipart.return_value = resp
    return t


@pytest.mark.asyncio
async def test_read_file_calls_transport_post():
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    transport = _make_transport(response=_ok_response(content=b"hello world"))
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    result = await fs.read_file("/path/to/foo")
    assert result == b"hello world"
    transport.post.assert_called_once_with("/api/file/read", json={"file_path": "/path/to/foo"})


@pytest.mark.asyncio
async def test_read_file_raises_on_404():
    """A failed read must NOT be swallowed — surface every HTTP error (404 included)
    so a silent 401 can't masquerade as an empty/missing file (the file-loss bug)."""
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    err_resp = httpx.Response(
        status_code=404, content=b"not found",
        request=httpx.Request("POST", "http://fake/"),
    )
    transport = _make_transport(response=err_resp)
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    with pytest.raises(httpx.HTTPStatusError):
        await fs.read_file("/missing")


@pytest.mark.asyncio
async def test_read_file_raises_on_401():
    """The actual bug: proxypass 401 (missing x-proxypass-token) must propagate."""
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    err_resp = httpx.Response(
        status_code=401, content=b"Unauthorized",
        request=httpx.Request("POST", "http://fake/"),
    )
    transport = _make_transport(response=err_resp)
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    with pytest.raises(httpx.HTTPStatusError):
        await fs.read_file("/foo")


@pytest.mark.asyncio
async def test_write_file_calls_transport_post_multipart():
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    transport = _make_transport()
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    await fs.write_file("/path/to/foo.txt", b"hello")
    transport.post_multipart.assert_called_once_with(
        "/api/file/upload",
        files={"file": ("/path/to/foo.txt", b"hello")},
        data={"target_path": "/path/to/foo.txt"},
    )


@pytest.mark.asyncio
async def test_write_file_raises_on_http_error():
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    err_resp = httpx.Response(
        status_code=500, content=b"err",
        request=httpx.Request("POST", "http://fake/"),
    )
    transport = _make_transport(response=err_resp)
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    with pytest.raises(httpx.HTTPStatusError):
        await fs.write_file("/foo.txt", b"x")


@pytest.mark.asyncio
async def test_delete_tree_calls_transport_post():
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    transport = _make_transport()
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    assert await fs.delete_tree("/dir") is True
    transport.post.assert_called_once_with("/api/file/rmtree", json={"target_path": "/dir"})


@pytest.mark.asyncio
async def test_delete_file_calls_transport_post():
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    transport = _make_transport()
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    assert await fs.delete_file("/foo.txt") is True
    transport.post.assert_called_once_with("/api/file/remove", json={"target_path": "/foo.txt"})


@pytest.mark.asyncio
async def test_list_dir_calls_transport_post():
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    transport = _make_transport(
        response=_ok_response(json_body={"data": {"files": [{"name": "a"}]}})
    )
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    files = await fs.list_dir("/dir", recursive=True)
    assert files == [{"name": "a"}]
    transport.post.assert_called_once_with("/api/file/list", json={"dir_path": "/dir", "recursive": True})


@pytest.mark.asyncio
async def test_exists_returns_true_when_read_succeeds():
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    transport = _make_transport(response=_ok_response(content=b"data"))
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    assert await fs.exists("/foo") is True


def test_desktop_baas_device_filesystem_inherits_from_baas():
    from agentclaw.community.core.devices.services.baas_device_filesystem import (
        BaasDeviceFileSystem, DesktopBaasDeviceFileSystem,
    )
    assert issubclass(DesktopBaasDeviceFileSystem, BaasDeviceFileSystem)


@pytest.mark.asyncio
async def test_desktop_baas_device_filesystem_uses_same_logic():
    """Desktop 子类业务逻辑跟父类一致。"""
    from agentclaw.community.core.devices.services.baas_device_filesystem import DesktopBaasDeviceFileSystem
    transport = _make_transport(response=_ok_response(content=b"x"))
    fs = DesktopBaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    await fs.read_file("/foo")
    transport.post.assert_called_once_with("/api/file/read", json={"file_path": "/foo"})


# ── reads surface errors (no swallow); deletes stay best-effort idempotent ──────


@pytest.mark.asyncio
async def test_read_file_raises_on_generic_exception():
    """transport 抛通用 Exception → read_file 透传(不再吞成 None)。"""
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    transport = _make_transport(side_effect=RuntimeError("network down"))
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    with pytest.raises(RuntimeError):
        await fs.read_file("/foo")


@pytest.mark.asyncio
async def test_delete_tree_returns_false_on_generic_exception():
    """transport 抛通用 Exception → delete_tree 返 False(幂等删除,保持兜底)。"""
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    transport = _make_transport(side_effect=RuntimeError("boom"))
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    assert await fs.delete_tree("/dir") is False


@pytest.mark.asyncio
async def test_delete_file_returns_false_on_generic_exception():
    """transport 抛通用 Exception → delete_file 返 False(幂等删除,保持兜底)。"""
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    transport = _make_transport(side_effect=RuntimeError("boom"))
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    assert await fs.delete_file("/foo") is False


@pytest.mark.asyncio
async def test_list_dir_raises_on_generic_exception():
    """transport 抛通用 Exception → list_dir 透传(不再吞成 None,避免被当成空目录)。"""
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    transport = _make_transport(side_effect=RuntimeError("boom"))
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    with pytest.raises(RuntimeError):
        await fs.list_dir("/dir")


@pytest.mark.asyncio
async def test_exists_returns_false_on_404():
    """exists 是布尔探测:404 即「不存在」→ False(不抛)。"""
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    err_resp = httpx.Response(
        status_code=404, content=b"nope",
        request=httpx.Request("POST", "http://fake/"),
    )
    transport = _make_transport(response=err_resp)
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    assert await fs.exists("/missing") is False


@pytest.mark.asyncio
async def test_exists_raises_on_401():
    """exists 仍不吞鉴权错误:401 必须透传,而不是误报「不存在」。"""
    from agentclaw.community.core.devices.services.baas_device_filesystem import BaasDeviceFileSystem
    err_resp = httpx.Response(
        status_code=401, content=b"Unauthorized",
        request=httpx.Request("POST", "http://fake/"),
    )
    transport = _make_transport(response=err_resp)
    fs = BaasDeviceFileSystem(transport=transport, conn_info=_conn_info(), path_mapper=lambda p: p)
    with pytest.raises(httpx.HTTPStatusError):
        await fs.exists("/foo")

"""Unit tests for LocalDeviceFileSystem BaaS mode (plan-02).

Verifies the option-A dual-mode ctor: when both baas_service and binding_ctx
are provided, the plugin routes file ops through BaaS get_http_info + httpx
direct to container adapter. Pathlib fallback is covered by the existing
test_local_device_filesystem.py (untouched).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentclaw.community.core.devices.models import DeviceBindingContext
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasServiceError,
    HttpConnectionInfo,
)
from agentclaw.community.core.devices.services.local_device_filesystem import LocalDeviceFileSystem


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def binding_ctx() -> DeviceBindingContext:
    return DeviceBindingContext(
        binding_id=42,
        device_id="bot-uuid-001",
        entity_id="staff_u001",
        adapter_port=20010,
        tenant="team_claw",
    )


@pytest.fixture
def mock_baas() -> MagicMock:
    """BaasService stub returning a fresh HttpConnectionInfo per call."""
    m = MagicMock()

    def _http_info_side(*, path="", **kwargs):
        # Simulate real BaaS behavior: the path parameter is blended into http_url
        return HttpConnectionInfo(
            http_url=f"http://10.0.0.1:20010{path}",
            token="abc-token",
        )

    m.get_http_info.side_effect = _http_info_side
    return m


@pytest.fixture
def fs_baas(mock_baas, binding_ctx) -> LocalDeviceFileSystem:
    """LocalDeviceFileSystem in BaaS mode."""
    return LocalDeviceFileSystem(
        baas_service=mock_baas, binding_ctx=binding_ctx
    )


def _mock_httpx_response(
    status_code: int = 200,
    content: bytes = b"",
    json_body: dict | None = None,
):
    """Build a canned httpx.Response."""
    if json_body is not None:
        return httpx.Response(
            status_code=status_code,
            json=json_body,
            request=httpx.Request("POST", "http://10.0.0.1:20010/api/file/x"),
        )
    return httpx.Response(
        status_code=status_code,
        content=content,
        request=httpx.Request("POST", "http://10.0.0.1:20010/api/file/x"),
    )


def _patch_async_client(response: httpx.Response):
    """Patch httpx.AsyncClient to return a canned response on .post()."""
    client = MagicMock()
    client.__aenter__ = MagicMock(return_value=_AsyncMock(client))
    client.__aexit__ = MagicMock(return_value=_AsyncMock(False))
    client.post = _AsyncMock(response)
    client.get = _AsyncMock(response)
    return client


_UNSET = object()


class _AsyncMock(MagicMock):
    """Awaitable MagicMock so `await client.post(...)` returns the canned value.

    Subclasses MagicMock so callers can inspect ``.call_args``, ``.call_count``
    etc. like a normal mock; the ``__call__`` override records the call via the
    parent then returns a self-awaitable yielding the configured value.
    ``value`` is keyword-style optional so MagicMock's auto-child-mock factory
    (which knows nothing about our ctor) can still spawn instances.
    """

    def __init__(self, value=_UNSET, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._value = value

    def __call__(self, *args, **kwargs):
        super().__call__(*args, **kwargs)
        return self

    def __await__(self):
        async def _coro():
            return self._value

        return _coro().__await__()


# ── ctor smoke ────────────────────────────────────────────────────────


def test_ctor_baas_mode_smoke(mock_baas, binding_ctx):
    """Both params given → no exception, instance ready."""
    fs = LocalDeviceFileSystem(baas_service=mock_baas, binding_ctx=binding_ctx)
    assert fs is not None


def test_ctor_pathlib_mode_smoke():
    """No params → no exception, instance ready (existing contract preserved)."""
    fs = LocalDeviceFileSystem()
    assert fs is not None


def test_ctor_partial_params_falls_back_to_pathlib(mock_baas):
    """Only one of (baas_service, binding_ctx) given → fall back to pathlib mode.

    Partial wiring is treated the same as no wiring: routes through pathlib.
    This keeps the dispatcher safe to evolve incrementally without surprising
    half-baked BaaS calls.
    """
    fs1 = LocalDeviceFileSystem(baas_service=mock_baas, binding_ctx=None)
    fs2 = LocalDeviceFileSystem(baas_service=None, binding_ctx=MagicMock())
    assert fs1 is not None
    assert fs2 is not None


# ── read_file (BaaS mode) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_file_baas_happy_returns_content(fs_baas, mock_baas):
    """BaaS happy: get_http_info → POST /api/file/read → returns 200 bytes."""
    response = _mock_httpx_response(status_code=200, content=b"hello")
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        result = await fs_baas.read_file("/tmp/x")

    assert result == b"hello"
    # get_http_info called exactly once (D3 — one BaaS call per business op)
    mock_baas.get_http_info.assert_called_once()
    call_kwargs = mock_baas.get_http_info.call_args.kwargs
    assert call_kwargs["bind_id"] == 42
    assert call_kwargs["port"] == 20010
    assert call_kwargs["path"] == "/api/file/read"
    assert call_kwargs["device_affinity"] == "staff_u001"

    # httpx POST hit container http_url + carried openclawToken header
    post_kwargs = client.post.call_args.kwargs
    assert client.post.call_args.args[0] == "http://10.0.0.1:20010/api/file/read"
    assert post_kwargs["headers"]["openclawToken"] == "abc-token"
    assert post_kwargs["json"] == {"file_path": "/tmp/x"}


@pytest.mark.asyncio
async def test_read_file_baas_container_404_returns_none(fs_baas):
    """容器 404 → None（保 read_file 原契约：文件不存在）。"""
    response = _mock_httpx_response(status_code=404, json_body={"error": "not_found"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        result = await fs_baas.read_file("/tmp/nope.txt")

    assert result is None


@pytest.mark.asyncio
async def test_read_file_baas_container_5xx_raises(fs_baas):
    """容器 5xx → raise（保 D8）。"""
    response = _mock_httpx_response(status_code=500, json_body={"error": "boom"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await fs_baas.read_file("/tmp/x")


@pytest.mark.asyncio
async def test_read_file_baas_container_401_raises(fs_baas):
    """容器 401 token 失效 → raise（不 refresh / 不 retry，D8）。"""
    response = _mock_httpx_response(status_code=401, json_body={"error": "unauthorized"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await fs_baas.read_file("/tmp/x")


@pytest.mark.asyncio
async def test_read_file_baas_get_http_info_fail_propagates(fs_baas, mock_baas):
    """BaaS get_http_info 失败 → raise BaasServiceError 透传（D8）。"""
    mock_baas.get_http_info.side_effect = BaasServiceError("baas unreachable")

    with pytest.raises(BaasServiceError, match="baas unreachable"):
        await fs_baas.read_file("/tmp/x")


# ── write_file (BaaS mode) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_file_baas_happy_no_raise(fs_baas, mock_baas):
    """BaaS happy: 200 → 不抛、call_args 含 file_path + content base64/raw。"""
    response = _mock_httpx_response(status_code=200, json_body={"code": 0})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        await fs_baas.write_file("/tmp/out.txt", b"payload")

    mock_baas.get_http_info.assert_called_once()
    assert mock_baas.get_http_info.call_args.kwargs["path"] == "/api/file/upload"
    assert client.post.call_args.args[0] == "http://10.0.0.1:20010/api/file/upload"
    post_kwargs = client.post.call_args.kwargs
    assert post_kwargs["headers"]["openclawToken"] == "abc-token"
    # write_file sends multipart (files= + data=), not json=
    assert "json" not in post_kwargs, "write_file should send multipart, not json"
    assert post_kwargs["data"]["target_path"] == "/tmp/out.txt"


@pytest.mark.asyncio
async def test_write_file_baas_5xx_raises(fs_baas):
    """容器 5xx → raise（写失败必抛，否则 caller 误以为 OK）。"""
    response = _mock_httpx_response(status_code=500, json_body={"error": "disk full"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await fs_baas.write_file("/tmp/x", b"data")


@pytest.mark.asyncio
async def test_write_file_baas_404_raises(fs_baas):
    """容器 404 → raise（写时 404 不是"不存在"，是路径无法创建/拒绝，必抛）。"""
    response = _mock_httpx_response(status_code=404, json_body={"error": "not_found"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await fs_baas.write_file("/tmp/x", b"data")


@pytest.mark.asyncio
async def test_write_file_baas_get_http_info_fail_propagates(fs_baas, mock_baas):
    mock_baas.get_http_info.side_effect = BaasServiceError("baas down")
    with pytest.raises(BaasServiceError):
        await fs_baas.write_file("/tmp/x", b"data")


# ── delete_tree (BaaS mode) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_tree_baas_happy_returns_true(fs_baas, mock_baas):
    response = _mock_httpx_response(status_code=200, json_body={"code": 0})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        assert await fs_baas.delete_tree("/tmp/dir") is True

    assert mock_baas.get_http_info.call_args.kwargs["path"] == "/api/file/rmtree"
    # The container adapter expects 'target_path' (the plugin sends this key)
    assert client.post.call_args.kwargs["json"] == {"target_path": "/tmp/dir"}


@pytest.mark.asyncio
async def test_delete_tree_baas_404_returns_true(fs_baas):
    """容器 404（路径已不存在） → True（保 delete_tree 幂等契约）。"""
    response = _mock_httpx_response(status_code=404, json_body={"error": "not_found"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        assert await fs_baas.delete_tree("/tmp/nope") is True


@pytest.mark.asyncio
async def test_delete_tree_baas_5xx_raises(fs_baas):
    """容器 5xx → raise（与 spec §4.2 一致：删失败必告 caller）。"""
    response = _mock_httpx_response(status_code=500, json_body={"error": "boom"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await fs_baas.delete_tree("/tmp/x")


@pytest.mark.asyncio
async def test_delete_tree_baas_get_http_info_fail_propagates(fs_baas, mock_baas):
    mock_baas.get_http_info.side_effect = BaasServiceError("baas down")
    with pytest.raises(BaasServiceError):
        await fs_baas.delete_tree("/tmp/x")


# ── list_dir (BaaS mode) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_dir_baas_happy_returns_entries(fs_baas, mock_baas):
    response = _mock_httpx_response(
        status_code=200,
        json_body={"code": 0, "data": {"files": [
            {"name": "a.txt", "path": "/tmp/d/a.txt", "is_dir": False, "relative_path": "a.txt"},
            {"name": "sub", "path": "/tmp/d/sub", "is_dir": True, "relative_path": "sub"},
        ]}},
    )
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        result = await fs_baas.list_dir("/tmp/d")

    assert result is not None
    assert len(result) == 2
    assert result[0]["name"] == "a.txt"
    assert result[1]["is_dir"] is True
    assert mock_baas.get_http_info.call_args.kwargs["path"] == "/api/file/list"
    # recursive 必默认 false
    assert client.post.call_args.kwargs["json"] == {
        "dir_path": "/tmp/d", "recursive": False
    }


@pytest.mark.asyncio
async def test_list_dir_baas_404_returns_none(fs_baas):
    response = _mock_httpx_response(status_code=404, json_body={"error": "not_found"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        assert await fs_baas.list_dir("/tmp/nope") is None


@pytest.mark.asyncio
async def test_list_dir_baas_5xx_raises(fs_baas):
    response = _mock_httpx_response(status_code=500, json_body={"error": "boom"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await fs_baas.list_dir("/tmp/x")


@pytest.mark.asyncio
async def test_list_dir_baas_get_http_info_fail_propagates(fs_baas, mock_baas):
    mock_baas.get_http_info.side_effect = BaasServiceError("baas down")
    with pytest.raises(BaasServiceError):
        await fs_baas.list_dir("/tmp/x")


@pytest.mark.asyncio
async def test_list_dir_recursive_true_passes_through_to_container(fs_baas):
    """recursive=True 必须真的传给容器请求体——防 plugin 把参数吞了。

    spec §5.2 额外反向 case：FileService.list_files (recursive 递归列树) 是
    plan-01 后 caller 的现实用法（feat 4e95c7d5a），plugin 不能吞这个 flag。
    """
    response = _mock_httpx_response(
        status_code=200,
        json_body={"code": 0, "data": {"files": [
            {"name": "top.txt", "path": "/d/top.txt", "is_dir": False, "relative_path": "top.txt"},
            {"name": "sub", "path": "/d/sub", "is_dir": True, "relative_path": "sub"},
            {"name": "deep.txt", "path": "/d/sub/deep.txt", "is_dir": False, "relative_path": "sub/deep.txt"},
        ]}},
    )
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        result = await fs_baas.list_dir("/d", recursive=True)

    # 关键断言：容器请求体必含 recursive=True
    assert client.post.call_args.kwargs["json"] == {
        "dir_path": "/d", "recursive": True
    }
    # 返回的 entries 形态正确
    assert result is not None
    assert len(result) == 3
    rel_paths = sorted(e["relative_path"] for e in result)
    assert "top.txt" in rel_paths
    assert "sub/deep.txt" in rel_paths


# ── exists (BaaS mode) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exists_baas_file_returns_true(fs_baas):
    """文件存在：read_file 返 bytes → exists True。"""
    response = _mock_httpx_response(status_code=200, content=b"x")
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        assert await fs_baas.exists("/tmp/yes.txt") is True


@pytest.mark.asyncio
async def test_exists_baas_dir_returns_true(fs_baas):
    """目录存在：read_file 返 None（路径不是文件） 但 list_dir 返 list → exists True。

    模拟方式：第一次 POST 返 404（read_file 视作 not-found），第二次 POST 返 list → True。
    """
    responses = [
        _mock_httpx_response(status_code=404, json_body={"error": "not_a_file"}),
        _mock_httpx_response(
            status_code=200,
            json_body={"code": 0, "data": {"files": []}},
        ),
    ]
    client = MagicMock()
    client.__aenter__ = MagicMock(return_value=_AsyncMock(client))
    client.__aexit__ = MagicMock(return_value=_AsyncMock(False))
    # 每次 POST 拉下一个 response
    call_idx = {"n": 0}

    def _post(*args, **kwargs):
        r = responses[call_idx["n"]]
        call_idx["n"] += 1
        return _AsyncMock(r)()

    client.post = _post

    with patch("httpx.AsyncClient", return_value=client):
        assert await fs_baas.exists("/tmp/dir") is True


@pytest.mark.asyncio
async def test_exists_baas_nonexistent_returns_false(fs_baas):
    """两次都 404 → exists False。"""
    response = _mock_httpx_response(status_code=404, json_body={"error": "not_found"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        assert await fs_baas.exists("/tmp/nope") is False


@pytest.mark.asyncio
async def test_exists_baas_swallows_baas_exception_returns_false(fs_baas, mock_baas):
    """BaaS 异常 → exists 吞掉返 False（保 bool 契约，与 spec §4.2 一致）。

    其他方法 raise 透传是因为 caller 需要错误信号；exists 是 yes/no 问题，
    异常等价 "无法确认存在" = False，避免炸 caller。
    """
    mock_baas.get_http_info.side_effect = BaasServiceError("baas down")
    assert await fs_baas.exists("/tmp/x") is False


@pytest.mark.asyncio
async def test_exists_baas_swallows_5xx_returns_false(fs_baas):
    """容器 5xx 同 BaaS 异常 → False。"""
    response = _mock_httpx_response(status_code=500, json_body={"error": "boom"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        assert await fs_baas.exists("/tmp/x") is False


# ── D3: 每业务调用 1 次 get_http_info ─────────────────────────────────


@pytest.mark.asyncio
async def test_each_op_calls_get_http_info_exactly_once(fs_baas, mock_baas):
    """3 个方法各调一次 → 3 次 get_http_info（D3 硬契约：no caching, no batching）。"""
    response = _mock_httpx_response(
        status_code=200,
        json_body={"code": 0, "data": {"files": []}},
    )
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        # read: content 是 json bytes，但 read_file 不解析 json，返回 bytes
        await fs_baas.read_file("/x")
        await fs_baas.delete_tree("/d")
        await fs_baas.list_dir("/d")

    # 3 个方法 = 3 次 get_http_info
    assert mock_baas.get_http_info.call_count == 3


# ── delete_file (BaaS mode keeps the historical local-unlink behavior) ────────


@pytest.mark.asyncio
async def test_delete_file_baas_unlinks_local_path(fs_baas, tmp_path):
    """BaaS-mode delete_file is unchanged from before: a local ``unlink`` of the
    mapped path (container-side removal goes through delete_tree)."""
    f = tmp_path / "gone.txt"
    f.write_text("x")
    assert await fs_baas.delete_file(str(f)) is True
    assert not f.exists()


@pytest.mark.asyncio
async def test_delete_file_baas_returns_false_on_oserror(fs_baas, tmp_path):
    """unlink on a directory raises IsADirectoryError (OSError) → False."""
    d = tmp_path / "adir"
    d.mkdir()
    assert await fs_baas.delete_file(str(d)) is False
    assert d.exists()

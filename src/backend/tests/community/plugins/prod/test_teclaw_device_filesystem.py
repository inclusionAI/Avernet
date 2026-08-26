"""Unit tests for TeclawDeviceFileSystem.

teclaw forwards every read **and write** per-file to the engine over
``BaasService.invoke_http`` (agentclawproxy gateway, ``x-proxypass-token``):
write → ``/api/v1/file/upload`` (multipart), delete → ``/api/v1/file/remove``,
delete_tree → ``/api/v1/file/rmtree`` (404 → list + remove-each), read →
``/api/v1/file/read``, list → ``/api/v1/file/list``. No OSS write and no
whole-artifact redeliver on an edit. These tests inject a stub ``path_mapper``
(its real behavior is covered in ``test_teclaw_paths``) and pin the per-file
calls, the auth header, and the rmtree fallback.
"""
from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.devices.services.teclaw_device_filesystem import TeclawDeviceFileSystem

_DEVICE_PATH = (
    "/aidesktop/aidesktop_prod/bolt_data/staff_u1/bot7/teclaw/workspace/data/foo.csv"
)
_DEVICE_DIR = (
    "/aidesktop/aidesktop_prod/bolt_data/staff_u1/bot7/teclaw/workspace/skills/my-skill"
)
_ENGINE_ROOT = "/aidesktop/aidesktop_prod/bolt_data/staff_u1/bot7/teclaw"


def _map(host_path: str) -> str:
    """Stub path_mapper: strip to the engine-relative form (``/workspace/...``).
    The real mapper (``teclaw_paths.to_engine_relative``) is tested separately;
    here we only verify the plugin routes every op through the injected mapper."""
    if host_path.startswith(_ENGINE_ROOT):
        return host_path[len(_ENGINE_ROOT):]  # e.g. /workspace/data/foo.csv
    return host_path


_ENGINE_PATH = _map(_DEVICE_PATH)  # /workspace/data/foo.csv
_ENGINE_DIR = _map(_DEVICE_DIR)    # /workspace/skills/my-skill

# build_baas_conn_info_for_http output shape: carries bind_id for invoke_http.
_CONN_INFO = {
    "bind_id": 4242,
    "paas_device_id": "BOT-uuid-1",
    "engine_port": 20003,
    "tenant": "team_claw",
}


class _FakeResponse:
    def __init__(self, *, status_code=200, content=b"", json_data=None):
        self.status_code = status_code
        self.content = content
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=httpx.Request("POST", "http://x"),
                response=httpx.Response(self.status_code),
            )


def _fs():
    baas = MagicMock()  # BaasService (sync .invoke_http)
    fs = TeclawDeviceFileSystem(
        conn_info=_CONN_INFO, path_mapper=_map, baas_service=baas,
    )
    return fs, baas


# ── write (per-file upload, no OSS / no redeliver) ────────────────────

@pytest.mark.asyncio
async def test_write_file_uploads_multipart_to_engine():
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse()
    await fs.write_file(_DEVICE_PATH, b"hello")
    kwargs = baas.invoke_http.call_args.kwargs
    assert kwargs["path"] == "/api/v1/file/upload"
    assert kwargs["files"] == {"file": (_ENGINE_PATH, b"hello")}
    assert kwargs["data"] == {"target_path": _ENGINE_PATH}
    assert kwargs["bind_id"] == 4242
    assert kwargs["port"] == 20003
    assert kwargs["tenant"] == "team_claw"
    # reached through the agentclawproxy /proxypass gateway
    assert kwargs["auth_header"] == "x-proxypass-token"


@pytest.mark.asyncio
async def test_write_file_raises_on_non_2xx():
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(status_code=500)
    with pytest.raises(httpx.HTTPStatusError):
        await fs.write_file(_DEVICE_PATH, b"hello")


# ── delete_file (per-file remove) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_file_calls_remove():
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse()
    ok = await fs.delete_file(_DEVICE_PATH)
    assert ok is True
    kwargs = baas.invoke_http.call_args.kwargs
    assert kwargs["path"] == "/api/v1/file/remove"
    assert kwargs["json"] == {"target_path": _ENGINE_PATH}
    assert kwargs["auth_header"] == "x-proxypass-token"


@pytest.mark.asyncio
async def test_delete_file_returns_false_on_error():
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(status_code=500)
    assert await fs.delete_file(_DEVICE_PATH) is False


# ── delete_tree (rmtree, with 404 fallback) ──────────────────────────

@pytest.mark.asyncio
async def test_delete_tree_calls_rmtree():
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse()
    ok = await fs.delete_tree(_DEVICE_DIR)
    assert ok is True
    kwargs = baas.invoke_http.call_args.kwargs
    assert kwargs["path"] == "/api/v1/file/rmtree"
    assert kwargs["json"] == {"target_path": _ENGINE_DIR}


@pytest.mark.asyncio
async def test_delete_tree_returns_false_on_error():
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(status_code=500)
    assert await fs.delete_tree(_DEVICE_DIR) is False


# ── reads (path now /api/v1/file/*) ──────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_invokes_engine_api_and_returns_bytes():
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(content=b"file-bytes")
    out = await fs.read_file(_DEVICE_PATH)
    assert out == b"file-bytes"
    kwargs = baas.invoke_http.call_args.kwargs
    assert kwargs["bind_id"] == 4242
    assert kwargs["port"] == 20003
    assert kwargs["path"] == "/api/v1/file/read"
    assert kwargs["tenant"] == "team_claw"
    assert kwargs["auth_header"] == "x-proxypass-token"
    assert kwargs["json"] == {"file_path": _ENGINE_PATH}


@pytest.mark.asyncio
async def test_read_file_returns_none_on_404():
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(status_code=404)
    assert await fs.read_file(_DEVICE_PATH) is None


@pytest.mark.asyncio
async def test_list_dir_returns_engine_files():
    fs, baas = _fs()
    files = [{"name": "a.csv", "is_dir": False, "size": 3}]
    baas.invoke_http.return_value = _FakeResponse(json_data={"data": {"files": files}})
    out = await fs.list_dir(_DEVICE_PATH)
    assert out == files
    kwargs = baas.invoke_http.call_args.kwargs
    assert kwargs["path"] == "/api/v1/file/list"
    assert kwargs["json"] == {"dir_path": _ENGINE_PATH, "recursive": False}


@pytest.mark.asyncio
async def test_list_dir_returns_none_on_error():
    fs, baas = _fs()
    baas.invoke_http.side_effect = httpx.ConnectError("boom")
    assert await fs.list_dir("/some/dir") is None


@pytest.mark.asyncio
async def test_exists_true_when_read_succeeds():
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(content=b"x")
    assert await fs.exists(_DEVICE_PATH) is True


# ── shared container resolution (one get_http_info per path) ──────────
#
# Callers write a Skill package by fanning its files out concurrently. Resolving
# per call would turn that fan-out into a *burst* of identical binding lookups and
# BaaS round trips, so these pin that the whole batch shares one resolution — and
# that reuse can never surface a spurious auth failure.

@pytest.mark.asyncio
async def test_package_writes_share_one_http_info_resolution():
    """一个 package 的多文件写共用一次 get_http_info，而不是每文件解析一次。"""
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse()

    for name in ("SKILL.md", "README.md", "scripts/run.sh", "assets/logo.png"):
        await fs.write_file(f"{_DEVICE_DIR}/{name}", b"x")

    assert baas.get_http_info.call_count == 1
    assert baas.invoke_http.call_count == 4
    # every upload went out against that one resolution
    assert all(
        call.kwargs["http_info"] is baas.get_http_info.return_value
        for call in baas.invoke_http.call_args_list
    )
    resolved = baas.get_http_info.call_args.kwargs
    assert resolved["bind_id"] == 4242
    assert resolved["port"] == 20003
    assert resolved["path"] == "/api/v1/file/upload"
    assert resolved["tenant"] == "team_claw"


@pytest.mark.asyncio
async def test_http_info_is_resolved_per_engine_path():
    """``http_url`` 里含 path，所以 upload / read / list 必须各自解析。"""
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(json_data={"data": {"files": []}})

    await fs.write_file(_DEVICE_PATH, b"x")
    await fs.read_file(_DEVICE_PATH)
    await fs.list_dir(_DEVICE_DIR)
    await fs.write_file(_DEVICE_PATH, b"y")

    assert [c.kwargs["path"] for c in baas.get_http_info.call_args_list] == [
        "/api/v1/file/upload",
        "/api/v1/file/read",
        "/api/v1/file/list",
    ]


@pytest.mark.asyncio
async def test_rejected_token_refreshes_and_replays_the_write_once():
    """缓存的 proxy token 中途过期时刷新重试，调用方不该看到假 401。"""
    fs, baas = _fs()
    stale, fresh = MagicMock(name="stale"), MagicMock(name="fresh")
    baas.get_http_info.side_effect = [stale, fresh]
    baas.invoke_http.side_effect = [_FakeResponse(status_code=401), _FakeResponse()]

    await fs.write_file(_DEVICE_PATH, b"hello")

    assert baas.get_http_info.call_count == 2
    assert [c.kwargs["http_info"] for c in baas.invoke_http.call_args_list] == [
        stale,
        fresh,
    ]


@pytest.mark.asyncio
async def test_forbidden_is_the_engines_own_answer_and_is_never_replayed():
    """403 是引擎自己的授权结论（PermissionError），重放会重跑一次变更写。"""
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(status_code=403)

    with pytest.raises(httpx.HTTPStatusError):
        await fs.write_file(_DEVICE_PATH, b"hello")

    assert baas.invoke_http.call_count == 1
    assert baas.get_http_info.call_count == 1

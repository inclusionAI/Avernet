"""Unit tests for TeclawDeviceFileSystem.

teclaw forwards every read **and write** per-file to the engine over
the cloud ``BaasInvokeTransport`` → ``BaasService.invoke_http`` (agentclawproxy
gateway, ``x-proxypass-token``):
write → ``/api/v1/file/upload`` (multipart), delete → ``/api/v1/file/remove``,
delete_tree → the same recursive-capable ``/api/v1/file/remove``, read →
``/api/v1/file/read``, list → ``/api/v1/file/list``. No OSS write and no
whole-artifact redeliver on an edit. These tests inject a stub ``path_mapper``
(its real behavior is covered in ``test_teclaw_paths``) and pin the per-file
calls and auth header. The transport is the real one over a mocked
``BaasService``, so these also pin that ``http_info`` is resolved once per engine
path rather than once per file.
"""
import logging

from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.devices.services.baas_invoke_transport import (
    BaasInvokeTransport,
)
from agentclaw.community.core.devices.services.device_filesystem_resolver import (
    DefaultDeviceFileSystemResolver,
)
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
        if not 200 <= self.status_code < 300:
            raise httpx.HTTPStatusError(
                "err", request=httpx.Request("POST", "http://x"),
                response=httpx.Response(self.status_code),
            )


def _fs():
    baas = MagicMock()  # BaasService (sync .get_http_info / .invoke_http)
    transport = BaasInvokeTransport(
        bind_id=_CONN_INFO["bind_id"],
        engine_port=_CONN_INFO["engine_port"],
        tenant=_CONN_INFO["tenant"],
        baas_service=baas,
    )
    fs = TeclawDeviceFileSystem(
        transport=transport, conn_info=_CONN_INFO, path_mapper=_map,
    )
    return fs, baas


@pytest.mark.asyncio
async def test_read_preserve_errors_distinguishes_transport_failure_from_absence():
    fs, baas = _fs()
    baas.invoke_http.side_effect = httpx.ConnectError("unavailable")

    assert await fs.read_file(_DEVICE_PATH) is None
    with pytest.raises(httpx.ConnectError, match="unavailable"):
        await fs.read_file(_DEVICE_PATH, preserve_read_errors=True)


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


# ── delete_tree (one recursive-capable remove call) ──────────────────


@pytest.mark.asyncio
async def test_delete_tree_calls_remove_once_for_the_complete_directory():
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse()
    ok = await fs.delete_tree(_DEVICE_DIR)
    assert ok is True
    kwargs = baas.invoke_http.call_args.kwargs
    assert kwargs["path"] == "/api/v1/file/remove"
    assert kwargs["json"] == {"target_path": _ENGINE_DIR}
    baas.invoke_http.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [302, 404, 500])
async def test_delete_tree_returns_false_on_any_non_2xx_without_fallback(status_code):
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(status_code=status_code)
    assert await fs.delete_tree(_DEVICE_DIR) is False
    baas.invoke_http.assert_called_once()


@pytest.mark.asyncio
async def test_delete_tree_returns_false_on_unknown_transport_outcome():
    fs, baas = _fs()
    baas.invoke_http.side_effect = httpx.ReadTimeout("unknown outcome")
    assert await fs.delete_tree(_DEVICE_DIR) is False
    baas.invoke_http.assert_called_once()


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
async def test_list_dir_returns_none_on_404_without_error_log(caplog):
    # A 404 means the directory does not exist — an expected condition, so it must
    # surface as None without raising or logging an operational ERROR (mirrors
    # read_file). Regression guard for the CDO alert triggered by ERROR-level 404
    # noise when a bot lists a missing directory (e.g. .../vendor).
    caplog.set_level(logging.DEBUG)
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(status_code=404)
    assert await fs.list_dir(_DEVICE_DIR) is None
    assert not any(
        r.levelno >= logging.ERROR and "list_dir" in r.getMessage()
        for r in caplog.records
    )
    assert any(
        r.levelno == logging.DEBUG
        and "list_dir" in r.getMessage()
        and "not found" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_exists_true_when_read_succeeds():
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(content=b"x")
    assert await fs.exists(_DEVICE_PATH) is True


# ── http_info resolution (one binding lookup per path, not per file) ──
@pytest.mark.asyncio
async def test_many_reads_resolve_http_info_once():
    """A promotion sweep reads every file through ``/api/v1/file/read``; each read
    used to re-run ``get_http_info`` (an ``ac_entity_device_binding`` SELECT plus a
    BaaS ``/http-info`` round trip). The transport now resolves it once and reuses
    it for every read on the same path."""
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(content=b"x")

    for i in range(5):
        assert await fs.read_file(f"{_ENGINE_ROOT}/workspace/f{i}.txt") == b"x"

    baas.get_http_info.assert_called_once_with(
        bind_id=4242, port=20003, path="/api/v1/file/read",
        tenant="team_claw", device_uuid=None,
    )
    assert baas.invoke_http.call_count == 5
    resolved = baas.get_http_info.return_value
    assert all(
        c.kwargs["http_info"] is resolved for c in baas.invoke_http.call_args_list
    )


@pytest.mark.asyncio
async def test_list_and_read_resolve_once_per_engine_path():
    # ``http_url`` embeds the engine path, so list and read cannot share an entry.
    fs, baas = _fs()
    baas.invoke_http.return_value = _FakeResponse(
        content=b"x", json_data={"data": {"files": []}},
    )

    await fs.list_dir(_DEVICE_DIR, recursive=True)
    await fs.read_file(_DEVICE_PATH)
    await fs.read_file(_DEVICE_PATH)

    assert [c.kwargs["path"] for c in baas.get_http_info.call_args_list] == [
        "/api/v1/file/list", "/api/v1/file/read",
    ]


@pytest.mark.asyncio
async def test_rejected_token_is_refreshed_and_the_read_retried_once():
    fs, baas = _fs()
    stale, fresh = MagicMock(name="stale"), MagicMock(name="fresh")
    baas.get_http_info.side_effect = [stale, fresh]
    baas.invoke_http.side_effect = [
        _FakeResponse(status_code=401), _FakeResponse(content=b"ok"),
    ]

    assert await fs.read_file(_DEVICE_PATH) == b"ok"
    assert [c.kwargs["http_info"] for c in baas.invoke_http.call_args_list] == [
        stale, fresh,
    ]


@pytest.mark.asyncio
async def test_failed_resolution_reads_as_missing_and_is_not_cached():
    fs, baas = _fs()
    baas.get_http_info.side_effect = [RuntimeError("baas down"), MagicMock()]
    baas.invoke_http.return_value = _FakeResponse(content=b"x")

    assert await fs.read_file(_DEVICE_PATH) is None
    assert await fs.read_file(_DEVICE_PATH) == b"x"
    assert baas.get_http_info.call_count == 2


def test_resolver_builds_teclaw_fs_over_the_cloud_baas_transport():
    baas = MagicMock()
    resolver = DefaultDeviceFileSystemResolver(
        baas_service=baas, bot_repo=MagicMock(), binding_repo=MagicMock(),
        sandbox_client=MagicMock(),
    )
    ctx = MagicMock(provider="teclaw", bot_id="bot7", conn_info=_CONN_INFO)

    fs = resolver(ctx, _map)

    assert isinstance(fs, TeclawDeviceFileSystem)
    transport = fs._transport
    assert isinstance(transport, BaasInvokeTransport)
    assert transport._bind_id == 4242
    assert transport._engine_port == 20003
    assert transport._tenant == "team_claw"
    # teclaw never pinned an instance; BaaS keeps choosing the active one.
    assert transport._device_uuid is None
    assert transport._baas_service is baas
    # A fresh transport (and so a fresh http_info cache) per dispatch.
    assert resolver(ctx, _map)._transport is not transport

"""Integration tests: LocalDeviceFileSystem (BaaS mode) — plugin → container.

Validates the second hop only (plugin's httpx-direct POST to the container's
``/api/file/*`` endpoints). The first hop (plugin → BaaS get_http_info) is
stubbed by a MagicMock BaasService that returns a fixed HttpConnectionInfo —
because that hop is already covered byte-for-byte by the unit tests in
``test_baas_service_get_http_info.py``.

Why ``patch("httpx.AsyncClient", FakeClient)`` instead of respx:
  Upstream ``tests/conftest.py`` installs an autouse ``_no_real_baas_calls``
  respx fixture that intercepts ``localhost:8890`` (BaaS) and ``pass_through``s
  every other host. Our container URL (``10.0.0.1:20010``) falls into the
  pass-through bucket and would hit the network in CI. ``patch("httpx.AsyncClient",
  FakeClient)`` cleanly bypasses both respx and the process's ``HTTP_PROXY``
  env — same pattern as ``tests/plugins/prod/test_baas_device_filesystem.py``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.devices.models import DeviceBindingContext
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasServiceError,
    HttpConnectionInfo,
)
from agentclaw.community.core.devices.services.local_device_filesystem import LocalDeviceFileSystem


pytestmark = pytest.mark.integration


CONTAINER_URL = "http://10.0.0.1:20010"
CONTAINER_TOKEN = "test-http-token"


def _fake_baas_service() -> MagicMock:
    """BaasService stub with get_http_info returning container URL blended with path."""
    m = MagicMock()

    def _side(*, path="", **kwargs):
        return HttpConnectionInfo(
            http_url=f"{CONTAINER_URL}{path}", token=CONTAINER_TOKEN,
        )

    m.get_http_info.side_effect = _side
    return m


def _make_fs(baas_service=None) -> LocalDeviceFileSystem:
    ctx = DeviceBindingContext(
        binding_id=42,
        device_id="bot-uuid-001",
        entity_id="staff_u001",
        adapter_port=20010,
        tenant="team_claw",
    )
    return LocalDeviceFileSystem(
        baas_service=baas_service or _fake_baas_service(),
        binding_ctx=ctx,
    )


def _fake_client_returning(response):
    """Build a FakeClient class that returns ``response`` on every .post()."""
    fake_post = AsyncMock(return_value=response)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            self.post = fake_post
            return self

        async def __aexit__(self, *a):
            return False

    return FakeClient, fake_post


@pytest.mark.asyncio
async def test_read_file_full_chain():
    """plugin.read_file → baas.get_http_info (mocked) → httpx POST (FakeClient)
    → response bytes round-trip back."""
    fs = _make_fs()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()
    fake_response.content = b"hello world"
    FakeClient, fake_post = _fake_client_returning(fake_response)

    with patch("httpx.AsyncClient", FakeClient):
        result = await fs.read_file("/tmp/x")

    assert result == b"hello world"
    # plugin posted to the container URL returned by BaaS
    assert fake_post.call_args.args[0] == f"{CONTAINER_URL}/api/file/read"
    # plugin carried the BaaS-issued token in openclawToken header
    assert fake_post.call_args.kwargs["headers"]["openclawToken"] == CONTAINER_TOKEN
    # body shape
    assert fake_post.call_args.kwargs["json"] == {"file_path": "/tmp/x"}


@pytest.mark.asyncio
async def test_write_file_full_chain():
    fs = _make_fs()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()
    FakeClient, fake_post = _fake_client_returning(fake_response)

    with patch("httpx.AsyncClient", FakeClient):
        await fs.write_file("/tmp/out.txt", b"payload")

    assert fake_post.call_args.args[0] == f"{CONTAINER_URL}/api/file/upload"
    # write_file sends multipart (files= + data=), not json=
    assert fake_post.call_args.kwargs["data"]["target_path"] == "/tmp/out.txt"


@pytest.mark.asyncio
async def test_delete_tree_full_chain():
    fs = _make_fs()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()
    FakeClient, fake_post = _fake_client_returning(fake_response)

    with patch("httpx.AsyncClient", FakeClient):
        result = await fs.delete_tree("/tmp/d")

    assert result is True
    assert fake_post.call_args.args[0] == f"{CONTAINER_URL}/api/file/rmtree"
    # plugin sends 'target_path' (the key the container adapter expects)
    assert fake_post.call_args.kwargs["json"] == {"target_path": "/tmp/d"}


@pytest.mark.asyncio
async def test_list_dir_full_chain():
    fs = _make_fs()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()
    fake_response.content = b'{"code":0,"data":{"files":[{"name":"a.txt","path":"/d/a.txt","is_dir":false,"relative_path":"a.txt"}]}}'
    fake_response.json = MagicMock(return_value={
        "code": 0,
        "data": {"files": [
            {"name": "a.txt", "path": "/d/a.txt", "is_dir": False, "relative_path": "a.txt"},
        ]},
    })
    FakeClient, fake_post = _fake_client_returning(fake_response)

    with patch("httpx.AsyncClient", FakeClient):
        result = await fs.list_dir("/tmp/d")

    assert result == [
        {"name": "a.txt", "path": "/d/a.txt", "is_dir": False, "relative_path": "a.txt"},
    ]
    assert fake_post.call_args.args[0] == f"{CONTAINER_URL}/api/file/list"


@pytest.mark.asyncio
async def test_baas_unreachable_propagates():
    """BaaS get_http_info raises → BaasServiceError propagates to caller
    (write_file is the raise-contract method per plan-02 §4.2)."""
    baas = MagicMock()
    baas.get_http_info.side_effect = BaasServiceError("baas down")
    fs = _make_fs(baas_service=baas)

    # httpx.AsyncClient not patched — but the request never gets there
    # because baas.get_http_info raises first.
    with pytest.raises(BaasServiceError, match="baas down"):
        await fs.write_file("/tmp/x", b"data")

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from engine.community.core.session_files.models import SessionFileTransferRequest
from engine.community.plugin_api.session_file_export import BaasFileExportError
from engine.community.plugins.session_file_export import BaasSessionFileClient


def _request(transfer_id: str = "source-001") -> SessionFileTransferRequest:
    return SessionFileTransferRequest(
        resource_id="sr_001",
        tenant="team_claw",
        session_key="session/a",
        transfer_id=transfer_id,
    )


@pytest.mark.asyncio
async def test_share_link_uses_session_route_without_bot_uuid():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-control-token"] == "control-secret"
        assert request.url.raw_path.decode() == (
            "/api/v1/sessions/team_claw/session%2Fa/files/transfers/source-001/share-link"
        )
        assert json.loads(request.content) == {
            "expire_seconds": 7200,
            "show": False,
            "operator": "engine",
        }
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "share_url": "https://oss.example/object?signature=redacted",
                    "expires_at": "2099-08-02T12:00:00Z",
                },
            },
        )

    client = BaasSessionFileClient(
        baas_base_url="https://baas.example",
        control_headers={"x-control-token": "control-secret"},
        allowed_share_hosts=frozenset({"oss.example"}),
        transport=httpx.MockTransport(handler),
    )

    link = await client.create_share_link(_request(), expire_seconds=7200)

    assert link.expires_at == "2099-08-02T12:00:00Z"
    assert [item.method for item in requests] == ["POST"]


@pytest.mark.asyncio
async def test_single_upload_streams_only_file_bytes_to_oss(tmp_path: Path):
    source = tmp_path / "report.txt"
    source.write_bytes(b"payload")
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "baas.example" and request.url.path.endswith(
            "upload-url"
        ):
            assert request.headers["x-control-token"] == "control-secret"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "transfer_id": "replacement-001",
                        "type": "SINGLE",
                        "upload_url": "https://oss.example/upload?signature=redacted",
                        "http_method": "PUT",
                    },
                },
            )
        if request.url.host == "oss.example":
            assert "x-control-token" not in request.headers
            assert request.content == b"payload"
            return httpx.Response(200)
        assert request.url.path.endswith("upload-url/replacement-001/complete")
        return httpx.Response(200, json={"code": 0, "data": {"status": "DONE"}})

    client = BaasSessionFileClient(
        baas_base_url="https://baas.example",
        control_headers={"x-control-token": "control-secret"},
        allowed_share_hosts=frozenset({"oss.example"}),
        transport=httpx.MockTransport(handler),
    )
    grant = await client.create_upload_grant(
        _request(), filename="report.txt", size_bytes=source.stat().st_size
    )
    await client.upload_file(grant, str(source), resource_id="sr_001")
    await client.complete_upload(_request(grant.transfer_id))

    assert [item.method for item in requests] == ["POST", "PUT", "POST"]
    assert "/api/v1/bots/" not in "\n".join(str(item.url) for item in requests)


@pytest.mark.asyncio
async def test_multipart_upload_uses_each_presigned_part(tmp_path: Path):
    source = tmp_path / "report.txt"
    source.write_bytes(b"abcde")
    uploaded: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "baas.example":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "transfer_id": "replacement-002",
                        "type": "MULTIPART",
                        "part_size": 3,
                        "part_count": 2,
                        "parts": [
                            {
                                "part_number": 1,
                                "upload_url": "https://oss.example/part-1",
                            },
                            {
                                "part_number": 2,
                                "upload_url": "https://oss.example/part-2",
                            },
                        ],
                    },
                },
            )
        uploaded.append(request.content)
        return httpx.Response(200)

    client = BaasSessionFileClient(
        baas_base_url="https://baas.example",
        control_headers={},
        allowed_share_hosts=frozenset({"oss.example"}),
        transport=httpx.MockTransport(handler),
    )
    grant = await client.create_upload_grant(
        _request(), filename="report.txt", size_bytes=source.stat().st_size
    )
    await client.upload_file(grant, str(source), resource_id="sr_001")

    assert uploaded == [b"abc", b"de"]


@pytest.mark.asyncio
async def test_share_link_source_missing_and_untrusted_host_are_rejected():
    source_missing = BaasSessionFileClient(
        baas_base_url="https://baas.example",
        control_headers={},
        allowed_share_hosts=frozenset({"oss.example"}),
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    with pytest.raises(BaasFileExportError, match="file_export_source_missing"):
        await source_missing.create_share_link(_request(), expire_seconds=7200)

    untrusted = BaasSessionFileClient(
        baas_base_url="https://baas.example",
        control_headers={},
        allowed_share_hosts=frozenset({"oss.example"}),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "share_url": "http://blocked.example/object",
                        "expires_at": "2099-08-02T12:00:00Z",
                    },
                },
            )
        ),
    )
    with pytest.raises(BaasFileExportError, match="file_export_failed"):
        await untrusted.create_share_link(_request(), expire_seconds=7200)

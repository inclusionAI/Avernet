from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from engine.community.core.resource_materialization.models import (
    MaterializationRequest,
)
from engine.community.plugins.resource_materialization import (
    SessionFileBaasMaterializationClient,
)


def _request(**overrides) -> MaterializationRequest:
    values = {
        "resource_id": "sr_001",
        "transfer_id": "transfer-001",
        "task_id": "task-001",
        "task_version": 1,
        "scope_key_hash": "scope_abc",
        "session_key_hash": "session_abc",
        "transfer_api_version": "session_v2",
        "tenant": "tenant-1",
        "session_id": "session/value",
        "workspace_relative_path": (
            ".teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
        ),
        "filename": "report.txt",
    }
    values.update(overrides)
    return MaterializationRequest(**values)


@pytest.mark.asyncio
async def test_session_pull_uses_share_link_and_never_forwards_control_headers(
    tmp_path: Path,
):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "baas.example":
            assert request.headers["x-control-token"] == "control-secret"
            assert request.url.path.endswith("/transfers/transfer-001/share-link")
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"share_url": "https://oss.example/object?signature=redacted"},
                },
            )
        assert request.url.host == "oss.example"
        assert "x-control-token" not in request.headers
        return httpx.Response(200, content=b"materialized bytes")

    client = SessionFileBaasMaterializationClient(
        baas_base_url="https://baas.example",
        control_headers={"x-control-token": "control-secret"},
        allowed_share_hosts=frozenset({"oss.example"}),
        transport=httpx.MockTransport(handler),
    )
    destination = tmp_path / "download.part"

    await client.pull(_request(), destination)

    assert destination.read_bytes() == b"materialized bytes"
    assert [request.method for request in requests] == ["POST", "GET"]


@pytest.mark.asyncio
async def test_session_pull_rejects_non_allowlisted_share_host_before_download(
    tmp_path: Path,
):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"code": 0, "data": {"share_url": "https://blocked.example/object"}},
        )

    client = SessionFileBaasMaterializationClient(
        baas_base_url="https://baas.example",
        control_headers={},
        allowed_share_hosts=frozenset({"oss.example"}),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ValueError, match="untrusted Session File share-link URL"):
        await client.pull(_request(), tmp_path / "download.part")

    assert [request.method for request in requests] == ["POST"]

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from engine.community.core.resource_materialization.models import (
    ChatAttachmentMaterializationRequest,
    MaterializationRequest,
)
from engine.community.plugins.resource_materialization import (
    HttpTemporaryUrlPullClient,
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
        "tenant": "team_claw",
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
            assert "/sessions/team_claw/" in request.url.path
            assert request.url.path.endswith("/transfers/transfer-001/share-link")
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "share_url": "https://oss.example/object?signature=redacted"
                    },
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


@pytest.mark.asyncio
async def test_temporary_url_pull_checks_host_dns_and_size(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "files.example"
        assert request.extensions["sni_hostname"] == "files.example"
        return httpx.Response(200, content=b"file-bytes")

    client = HttpTemporaryUrlPullClient(
        max_bytes=32,
        transport=httpx.MockTransport(handler),
    )
    request = ChatAttachmentMaterializationRequest(
        attachment_id="att-1",
        session_key="session-1",
        filename="file.txt",
        temporary_url="https://files.example/object?token=secret",
        scope_key_hash="a" * 64,
    )
    destination = tmp_path / "file.part"

    with patch(
        "engine.community.plugins.resource_materialization.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    ):
        await client.pull(request, destination)

    assert destination.read_bytes() == b"file-bytes"


@pytest.mark.asyncio
async def test_temporary_url_pull_pins_validated_ip_before_request(tmp_path: Path):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"safe")

    client = HttpTemporaryUrlPullClient(
        transport=httpx.MockTransport(handler),
    )
    with patch(
        "engine.community.plugins.resource_materialization.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    ) as resolve:
        await client.pull(_chat_request(), tmp_path / "file.part")

    resolve.assert_called_once()
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "files.example"
    assert requests[0].extensions["sni_hostname"] == "files.example"


@pytest.mark.asyncio
async def test_temporary_url_pull_accepts_any_public_https_host(tmp_path: Path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"file")

    client = HttpTemporaryUrlPullClient(
        transport=httpx.MockTransport(handler),
    )
    request = ChatAttachmentMaterializationRequest(
        attachment_id="att-1",
        session_key="session-1",
        filename="file.txt",
        temporary_url="https://another-public.example/object",
        scope_key_hash="a" * 64,
    )

    with patch(
        "engine.community.plugins.resource_materialization.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    ):
        await client.pull(request, tmp_path / "file.part")

    assert len(requests) == 1
    assert requests[0].headers["host"] == "another-public.example"
    assert (tmp_path / "file.part").read_bytes() == b"file"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"max_bytes": 0}, "limits must be positive"),
        (
            {
                "timeout_seconds": 0,
            },
            "limits must be positive",
        ),
    ],
)
def test_temporary_url_pull_rejects_invalid_configuration(kwargs, message):
    with pytest.raises(ValueError, match=message):
        HttpTemporaryUrlPullClient(**kwargs)


def _chat_request() -> ChatAttachmentMaterializationRequest:
    return ChatAttachmentMaterializationRequest(
        attachment_id="att-1",
        session_key="session-1",
        filename="file.txt",
        temporary_url="https://files.example/object?token=secret",
        scope_key_hash="a" * 64,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_length, content, message",
    [
        ("invalid", b"file", "invalid temporary URL content length"),
        ("5", b"file!", "temporary URL response exceeds size limit"),
        (None, b"file!", "temporary URL response exceeds size limit"),
    ],
)
async def test_temporary_url_pull_enforces_declared_and_observed_size(
    tmp_path: Path,
    content_length: str | None,
    content: bytes,
    message: str,
):
    headers = {"content-length": content_length} if content_length is not None else {}
    client = HttpTemporaryUrlPullClient(
        max_bytes=4,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers=headers, content=content)
        ),
    )

    with (
        patch(
            "engine.community.plugins.resource_materialization.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ),
        pytest.raises(ValueError, match=message),
    ):
        await client.pull(_chat_request(), tmp_path / "file.part")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses, side_effect, message",
    [
        (None, OSError("dns failed"), "host could not be resolved"),
        ([], None, "host could not be resolved"),
        ([(2, 1, 6, "", ("127.0.0.1", 443))], None, "non-public address"),
    ],
)
async def test_temporary_url_pull_rejects_unverifiable_or_private_dns(
    addresses,
    side_effect,
    message,
):
    client = HttpTemporaryUrlPullClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )

    with (
        patch(
            "engine.community.plugins.resource_materialization.socket.getaddrinfo",
            return_value=addresses,
            side_effect=side_effect,
        ),
        pytest.raises(ValueError, match=message),
    ):
        await client.pull(_chat_request(), Path("unused.part"))

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from engine.community.core.chat_file_share.models import ChatFileShareError
from engine.community.core.chat_file_share.service import ChatFileShareService
from engine.community.core.session_files.models import (
    BaasFileExportShareLink,
    SessionFileTransferRequest,
    SessionFileUploadGrant,
)
from engine.community.plugin_api.session_file_export import BaasFileExportError
from engine.community.plugins.session_file_export import BaasSessionFileClient


class _SessionFileClient:
    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.upload_grant_requests: list[
            tuple[SessionFileTransferRequest, str, int]
        ] = []
        self.uploads: list[tuple[str, str]] = []
        self.completed_requests: list[SessionFileTransferRequest] = []
        self.share_link_requests: list[tuple[SessionFileTransferRequest, int]] = []

    async def create_upload_grant(
        self,
        request: SessionFileTransferRequest,
        *,
        filename: str,
        size_bytes: int,
    ) -> SessionFileUploadGrant:
        if self.error:
            raise BaasFileExportError(self.error)
        self.upload_grant_requests.append((request, filename, size_bytes))
        return SessionFileUploadGrant(
            transfer_id="transfer-001",
            upload_type="SINGLE",
            upload_url="https://oss.example/upload",
        )

    async def upload_file(
        self,
        grant: SessionFileUploadGrant,
        source_path: str,
        *,
        resource_id: str,
    ) -> None:
        self.uploads.append((source_path, resource_id))

    async def complete_upload(self, request: SessionFileTransferRequest) -> None:
        self.completed_requests.append(request)

    async def create_share_link(
        self,
        request: SessionFileTransferRequest,
        *,
        expire_seconds: int,
    ) -> BaasFileExportShareLink:
        self.share_link_requests.append((request, expire_seconds))
        return BaasFileExportShareLink(
            download_url="https://oss.example/report.txt?signature=redacted",
            expires_at="2099-08-23T00:00:00Z",
        )


@pytest.mark.asyncio
async def test_share_reuses_the_session_file_upload_and_share_protocol(
    tmp_path: Path,
) -> None:
    source = tmp_path / "report.txt"
    source.write_bytes(b"report")
    client = _SessionFileClient()
    service = ChatFileShareService(
        workspace_root=tmp_path,
        tenant="team_claw",
        client=client,
    )

    result = await service.share(
        relative_path="report.txt",
        session_key="current-chat-session",
    )

    assert result.file_name == "report.txt"
    assert result.size_bytes == len(b"report")
    assert result.share_url.startswith("https://oss.example/")
    grant_request, filename, size_bytes = client.upload_grant_requests[0]
    assert grant_request.tenant == "team_claw"
    assert grant_request.session_key == "current-chat-session"
    assert grant_request.transfer_id == ""
    assert grant_request.resource_id.startswith("chat-share-")
    assert filename == "report.txt"
    assert size_bytes == len(b"report")
    assert client.uploads == [(str(source), grant_request.resource_id)]
    assert client.completed_requests == [
        SessionFileTransferRequest(
            resource_id=grant_request.resource_id,
            tenant="team_claw",
            session_key="current-chat-session",
            transfer_id="transfer-001",
        )
    ]
    assert client.share_link_requests == [
        (client.completed_requests[0], 86400)
    ]


@pytest.mark.asyncio
async def test_share_rejects_a_missing_chat_session_before_calling_baas(
    tmp_path: Path,
) -> None:
    source = tmp_path / "report.txt"
    source.write_bytes(b"report")
    client = _SessionFileClient()
    service = ChatFileShareService(
        workspace_root=tmp_path,
        tenant="team_claw",
        client=client,
    )

    with pytest.raises(ChatFileShareError, match="session_context_unavailable"):
        await service.share(relative_path="report.txt", session_key="")

    assert client.upload_grant_requests == []


@pytest.mark.asyncio
async def test_share_rejects_an_absolute_path_before_calling_baas(
    tmp_path: Path,
) -> None:
    client = _SessionFileClient()
    service = ChatFileShareService(
        workspace_root=tmp_path,
        tenant="team_claw",
        client=client,
    )

    with pytest.raises(ChatFileShareError, match="invalid_file_path"):
        await service.share(
            relative_path="/etc/passwd",
            session_key="current-chat-session",
        )

    assert client.upload_grant_requests == []


@pytest.mark.asyncio
async def test_share_rejects_a_workspace_symlink_before_calling_baas(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "linked.txt").symlink_to(outside)
    client = _SessionFileClient()
    service = ChatFileShareService(
        workspace_root=tmp_path,
        tenant="team_claw",
        client=client,
    )

    with pytest.raises(ChatFileShareError, match="invalid_file_path"):
        await service.share(
            relative_path="linked.txt",
            session_key="current-chat-session",
        )

    assert client.upload_grant_requests == []


@pytest.mark.asyncio
async def test_share_maps_a_session_file_service_failure(tmp_path: Path) -> None:
    source = tmp_path / "report.txt"
    source.write_bytes(b"report")
    service = ChatFileShareService(
        workspace_root=tmp_path,
        tenant="team_claw",
        client=_SessionFileClient(error="file_export_unavailable"),
    )

    with pytest.raises(ChatFileShareError, match="file_share_unavailable"):
        await service.share(
            relative_path="report.txt",
            session_key="current-chat-session",
        )


@pytest.mark.asyncio
async def test_share_does_not_log_the_short_lived_download_url(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = tmp_path / "report.txt"
    source.write_bytes(b"report")
    client = _SessionFileClient()
    service = ChatFileShareService(
        workspace_root=tmp_path,
        tenant="team_claw",
        client=client,
    )

    with caplog.at_level("INFO", logger="engine.chat_file_share"):
        await service.share(
            relative_path="report.txt",
            session_key="current-chat-session",
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "oss.example" not in messages
    assert "signature=" not in messages


@pytest.mark.asyncio
async def test_share_suppresses_http_access_logs_with_session_or_signed_urls(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = tmp_path / "report.txt"
    source.write_bytes(b"report")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200)
        if request.url.path.endswith("/files/upload-url"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "transfer_id": "transfer-001",
                        "type": "SINGLE",
                        "upload_url": "https://oss.example/upload?signature=secret",
                    },
                },
            )
        if request.url.path.endswith("/complete"):
            return httpx.Response(200, json={"code": 0, "data": {"status": "DONE"}})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "share_url": "https://oss.example/report.txt?signature=secret",
                    "expires_at": "2099-08-23T00:00:00Z",
                },
            },
        )

    service = ChatFileShareService(
        workspace_root=tmp_path,
        tenant="team_claw",
        client=BaasSessionFileClient(
            baas_base_url="https://baas.example",
            control_headers={},
            allowed_share_hosts=frozenset({"oss.example"}),
            transport=httpx.MockTransport(handler),
        ),
    )

    with caplog.at_level(logging.INFO):
        await service.share(
            relative_path="report.txt",
            session_key="current-chat-session",
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "current-chat-session" not in messages
    assert "signature=secret" not in messages


@pytest.mark.asyncio
async def test_share_rejects_an_unavailable_workspace(tmp_path: Path) -> None:
    client = _SessionFileClient()
    service = ChatFileShareService(
        workspace_root=tmp_path / "missing-workspace",
        tenant="team_claw",
        client=client,
    )

    with pytest.raises(ChatFileShareError, match="invalid_file_path"):
        await service.share(
            relative_path="report.txt",
            session_key="current-chat-session",
        )

    assert client.upload_grant_requests == []


@pytest.mark.asyncio
async def test_share_rejects_a_workspace_directory(tmp_path: Path) -> None:
    (tmp_path / "directory").mkdir()
    client = _SessionFileClient()
    service = ChatFileShareService(
        workspace_root=tmp_path,
        tenant="team_claw",
        client=client,
    )

    with pytest.raises(ChatFileShareError, match="invalid_file_path"):
        await service.share(
            relative_path="directory",
            session_key="current-chat-session",
        )

    assert client.upload_grant_requests == []

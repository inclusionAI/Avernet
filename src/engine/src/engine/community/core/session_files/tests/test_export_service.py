from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from engine.community.core.resource_materialization.models import (
    ManifestEntry,
    hash_identifier,
)
from engine.community.core.resource_materialization.service import ManifestStore
from engine.community.core.session_files.export_service import SessionFileExportService
from engine.community.core.session_files.models import (
    BaasFileExportShareLink,
    SessionFileError,
)
from engine.community.core.session_files.service import SessionFileService
from engine.community.plugin_api.session_file_export import BaasFileExportError


def _write_file(
    root: Path, content: bytes = b"downloadable report"
) -> tuple[SessionFileService, str, Path]:
    session_key = "session-a"
    relative = f".teamclaw/session-files/scope_a/{hash_identifier(session_key)}/sr_001/report.txt"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    stat = path.stat()
    ManifestStore(root).upsert(
        ManifestEntry(
            resource_id="sr_001",
            transfer_id="source-transfer",
            task_id="task-001",
            task_version=1,
            scope_key_hash="scope_a",
            session_key_hash=hash_identifier(session_key),
            filename="report.txt",
            relative_path=relative,
            canonical_bot_absolute_path=str(path.resolve()),
            size_bytes=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
            status="ready",
            observed_size=stat.st_size,
            observed_mtime_ns=stat.st_mtime_ns,
            observed_inode=stat.st_ino,
            baas_tenant="team_claw",
        )
    )
    return SessionFileService(workspace_root_provider=lambda: root), session_key, path


class _SessionFileClient:
    def __init__(self) -> None:
        self.share_calls = []
        self.grant_calls = []
        self.upload_calls = []
        self.complete_calls = []
        self.release: asyncio.Event | None = None
        self.source_missing = False
        self.fail = False

    async def create_share_link(self, request, *, expire_seconds):
        self.share_calls.append((request, expire_seconds))
        if self.release is not None:
            await self.release.wait()
        if self.source_missing and len(self.share_calls) == 1:
            raise BaasFileExportError("file_export_source_missing")
        if self.fail:
            raise BaasFileExportError("file_export_unavailable")
        return BaasFileExportShareLink(
            download_url="https://oss.example/download?redacted",
            expires_at="2099-08-02T12:00:00Z",
        )

    async def create_upload_grant(self, request, *, filename, size_bytes):
        self.grant_calls.append((request, filename, size_bytes))
        return type(
            "Grant",
            (),
            {
                "transfer_id": "replacement-transfer",
                "upload_type": "SINGLE",
                "upload_url": "https://oss.example/upload?redacted",
                "http_method": "PUT",
                "part_size": None,
                "part_count": None,
                "parts": None,
            },
        )()

    async def upload_file(self, grant, source_path, *, resource_id):
        self.upload_calls.append((grant, Path(source_path).read_bytes(), resource_id))

    async def complete_upload(self, request):
        self.complete_calls.append(request)


async def _ready_result(
    exports: SessionFileExportService,
    service: SessionFileService,
    session_key: str,
):
    source = service.prepare_export_source(
        session_key=session_key, resource_id="sr_001"
    )
    assert (
        await exports.request_download(source=source, session_key=session_key)
    ).state == "preparing"
    await exports._jobs[
        (source.resource_id, source.content_hash, source.transfer_id)
    ].task
    return await exports.request_download(source=source, session_key=session_key)


@pytest.mark.asyncio
async def test_original_transfer_is_shared_and_cached_without_upload(tmp_path: Path):
    service, session_key, _ = _write_file(tmp_path)
    client = _SessionFileClient()
    exports = SessionFileExportService(
        session_file_service=service, export_client=client
    )

    result = await _ready_result(exports, service, session_key)

    assert result.state == "ready"
    assert result.download is not None
    assert result.download.filename == "report.txt"
    assert len(client.share_calls) == 1
    request, expires = client.share_calls[0]
    assert request.tenant == "team_claw"
    assert request.session_key == session_key
    assert request.transfer_id == "source-transfer"
    assert expires == 7200
    assert client.grant_calls == []
    assert client.upload_calls == []


def test_missing_manifest_tenant_uses_injected_default_or_fails_closed(tmp_path: Path):
    service, session_key, _ = _write_file(tmp_path)
    entry = ManifestStore(tmp_path).get("sr_001")
    assert entry is not None
    ManifestStore(tmp_path).upsert(entry.model_copy(update={"baas_tenant": None}))

    with pytest.raises(SessionFileError, match="resource_export_tenant_unavailable"):
        service.prepare_export_source(session_key=session_key, resource_id="sr_001")

    configured_service = SessionFileService(
        workspace_root_provider=lambda: tmp_path,
        default_baas_tenant="configured-tenant",
    )
    source = configured_service.prepare_export_source(
        session_key=session_key,
        resource_id="sr_001",
    )

    assert source.tenant == "configured-tenant"


@pytest.mark.asyncio
async def test_changed_file_is_reuploaded_and_promoted_only_in_manifest(tmp_path: Path):
    service, session_key, path = _write_file(tmp_path)
    path.write_bytes(b"changed report")
    client = _SessionFileClient()
    exports = SessionFileExportService(
        session_file_service=service, export_client=client
    )

    result = await _ready_result(exports, service, session_key)
    entry = ManifestStore(tmp_path).get("sr_001")

    assert result.state == "ready"
    assert entry is not None
    assert entry.transfer_id == "replacement-transfer"
    assert entry.content_hash == hashlib.sha256(b"changed report").hexdigest()
    assert client.grant_calls[0][0].session_key == session_key
    assert len(client.upload_calls) == 1
    assert client.upload_calls[0][1:] == (b"changed report", "sr_001")
    assert client.complete_calls[0].transfer_id == "replacement-transfer"
    assert client.share_calls[0][0].transfer_id == "replacement-transfer"


@pytest.mark.asyncio
async def test_missing_original_transfer_reuploads_current_file(tmp_path: Path):
    service, session_key, _ = _write_file(tmp_path)
    client = _SessionFileClient()
    client.source_missing = True
    exports = SessionFileExportService(
        session_file_service=service, export_client=client
    )

    result = await _ready_result(exports, service, session_key)

    assert result.state == "ready"
    assert len(client.share_calls) == 2
    assert client.share_calls[0][0].transfer_id == "source-transfer"
    assert client.share_calls[1][0].transfer_id == "replacement-transfer"
    assert len(client.grant_calls) == 1


@pytest.mark.asyncio
async def test_same_source_joins_one_inflight_share_request(tmp_path: Path):
    service, session_key, _ = _write_file(tmp_path)
    client = _SessionFileClient()
    client.release = asyncio.Event()
    exports = SessionFileExportService(
        session_file_service=service, export_client=client
    )
    source = service.prepare_export_source(
        session_key=session_key, resource_id="sr_001"
    )

    assert (
        await exports.request_download(source=source, session_key=session_key)
    ).state == "preparing"
    await asyncio.sleep(0)
    assert (
        await exports.request_download(source=source, session_key=session_key)
    ).state == "preparing"
    assert len(client.share_calls) == 1
    client.release.set()
    await exports._jobs[
        (source.resource_id, source.content_hash, source.transfer_id)
    ].task

    assert (
        await exports.request_download(source=source, session_key=session_key)
    ).state == "ready"


@pytest.mark.asyncio
async def test_change_during_export_is_retried_as_a_fresh_job(tmp_path: Path):
    service, session_key, path = _write_file(tmp_path, b"old")
    path.write_bytes(b"first change")
    client = _SessionFileClient()
    client.release = asyncio.Event()
    exports = SessionFileExportService(
        session_file_service=service, export_client=client
    )
    source = service.prepare_export_source(
        session_key=session_key, resource_id="sr_001"
    )

    assert (
        await exports.request_download(source=source, session_key=session_key)
    ).state == "preparing"
    await asyncio.sleep(0)
    path.write_bytes(b"second change")
    client.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    fresh = service.prepare_export_source(session_key=session_key, resource_id="sr_001")
    assert fresh.requires_upload is True
    assert (
        await exports.request_download(source=fresh, session_key=session_key)
    ).state == "preparing"


@pytest.mark.asyncio
async def test_baas_failure_is_short_lived_and_safe_to_expose(tmp_path: Path):
    service, session_key, _ = _write_file(tmp_path)
    client = _SessionFileClient()
    client.fail = True
    exports = SessionFileExportService(
        session_file_service=service, export_client=client
    )

    result = await _ready_result(exports, service, session_key)

    assert result.state == "failed"
    assert result.error_code == "file_export_unavailable"

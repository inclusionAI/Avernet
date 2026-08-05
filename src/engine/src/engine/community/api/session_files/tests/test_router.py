from __future__ import annotations

import hashlib
from datetime import UTC, datetime
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, Module, singleton

from engine.community.api.session_files.router import router
from engine.community.core.resource_materialization.models import (
    ManifestEntry,
    hash_identifier,
)
from engine.community.core.resource_materialization.service import ManifestStore
from engine.community.core.session_files.export_service import SessionFileExportService
from engine.community.core.session_files.models import (
    BaasFileExportShareLink,
)
from engine.community.core.session_files.service import SessionFileService


def _write_ready_file(root: Path, session_key: str, content: bytes = b"report") -> Path:
    relative = f".teamclaw/session-files/scope_abc/{hash_identifier(session_key)}/sr_001/report.txt"
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    observed = target.stat()
    ManifestStore(root).upsert(
        ManifestEntry(
            resource_id="sr_001",
            transfer_id="transfer-001",
            task_id="task-001",
            task_version=1,
            scope_key_hash="scope_abc",
            session_key_hash=hash_identifier(session_key),
            filename="report.txt",
            relative_path=relative,
            canonical_bot_absolute_path=str(target.resolve()),
            size_bytes=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
            status="ready",
            observed_size=observed.st_size,
            observed_mtime_ns=observed.st_mtime_ns,
            observed_inode=observed.st_ino,
            uploaded_at=datetime(2026, 8, 3, 10, 20, 30, tzinfo=UTC),
            baas_tenant="team_claw",
        )
    )
    return target


@pytest.fixture
def client(tmp_path: Path):
    service = SessionFileService(workspace_root_provider=lambda: tmp_path)

    class _ExportClient:
        async def create_share_link(self, request, *, expire_seconds):
            assert expire_seconds == 7200
            return BaasFileExportShareLink(
                download_url="https://oss.example/file?redacted",
                expires_at="2099-08-02T12:00:00Z",
            )

        async def create_upload_grant(self, request, *, filename, size_bytes):
            raise AssertionError("unchanged router fixture must not upload")

        async def upload_file(self, grant, source_path, *, resource_id):
            raise AssertionError("unchanged router fixture must not upload")

        async def complete_upload(self, request):
            raise AssertionError("unchanged router fixture must not upload")

    export_service = SessionFileExportService(
        session_file_service=service,
        export_client=_ExportClient(),
    )

    class _Module(Module):
        def configure(self, binder) -> None:
            binder.bind(
                SessionFileService, to=InstanceProvider(service), scope=singleton
            )
            binder.bind(
                SessionFileExportService,
                to=InstanceProvider(export_service),
                scope=singleton,
            )

    app = FastAPI()
    attach_injector(app, Injector([_Module()]))
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client, tmp_path


def test_list_uses_trusted_proxypass_without_iam_header(client):
    test_client, _ = client

    response = test_client.get("/api/session-files", params={"sessionKey": "session-a"})

    assert response.status_code == 200
    assert response.json() == {"files": []}


def test_list_and_content_are_scoped_to_manifest_session(client):
    test_client, root = client
    target = _write_ready_file(root, "session-a")

    listed = test_client.get(
        "/api/session-files",
        params={"sessionKey": "session-a"},
    )
    content = test_client.get(
        "/api/session-files/sr_001/content",
        params={"sessionKey": "session-a", "disposition": "inline"},
    )
    cross_session = test_client.get(
        "/api/session-files/sr_001/content",
        params={"sessionKey": "session-b"},
    )

    assert listed.status_code == 200
    assert listed.json() == {
        "files": [
            {
                "resource_id": "sr_001",
                "display_name": "report.txt",
                "size_bytes": target.stat().st_size,
                "availability": "ready",
                "uploaded_at": "2026-08-03T10:20:30Z",
            }
        ]
    }
    assert content.status_code == 200
    assert content.content == b"report"
    assert content.headers["content-disposition"].startswith("inline;")
    assert cross_session.status_code == 404


def test_list_returns_null_upload_time_for_legacy_manifest(client):
    test_client, root = client
    _write_ready_file(root, "session-a")
    entry = ManifestStore(root).get("sr_001")
    assert entry is not None
    ManifestStore(root).upsert(entry.model_copy(update={"uploaded_at": None}))

    response = test_client.get(
        "/api/session-files",
        params={"sessionKey": "session-a"},
    )

    assert response.status_code == 200
    assert response.json()["files"][0]["uploaded_at"] is None


def test_changed_and_missing_files_are_not_streamed(client):
    test_client, root = client
    target = _write_ready_file(root, "session-a", b"report")
    target.write_bytes(b"change")

    changed_list = test_client.get(
        "/api/session-files",
        params={"sessionKey": "session-a"},
    )

    changed = test_client.get(
        "/api/session-files/sr_001/content",
        params={"sessionKey": "session-a"},
    )
    target.unlink()
    listed = test_client.get(
        "/api/session-files",
        params={"sessionKey": "session-a"},
    )
    missing = test_client.get(
        "/api/session-files/sr_001/content",
        params={"sessionKey": "session-a"},
    )

    assert changed.status_code == 409
    assert changed.json()["detail"] == "resource_changed"
    assert changed_list.json()["files"][0]["availability"] == "changed"
    assert changed_list.json()["files"][0]["uploaded_at"] == "2026-08-03T10:20:30Z"
    assert listed.json()["files"][0]["availability"] == "missing"
    assert listed.json()["files"][0]["uploaded_at"] == "2026-08-03T10:20:30Z"
    assert missing.status_code == 409
    assert missing.json()["detail"] == "resource_missing"


def test_small_attachment_streams_without_baas(client):
    test_client, root = client
    _write_ready_file(root, "session-a")
    params = {"sessionKey": "session-a", "disposition": "attachment"}

    response = test_client.get("/api/session-files/sr_001/content", params=params)

    assert response.status_code == 200
    assert response.content == b"report"
    assert response.headers["content-disposition"].startswith("attachment;")


def test_large_attachment_returns_preparing_then_external_url(client):
    test_client, root = client
    _write_ready_file(root, "session-a", b"x" * (30 * 1024 * 1024 + 1))
    params = {"sessionKey": "session-a", "disposition": "attachment"}

    preparing = test_client.get("/api/session-files/sr_001/content", params=params)
    time.sleep(0.05)
    ready = test_client.get("/api/session-files/sr_001/content", params=params)

    assert preparing.status_code == 202
    assert preparing.headers["retry-after"] == "2"
    assert ready.status_code == 200
    assert ready.json()["delivery"] == "external_url"
    assert ready.headers["cache-control"] == "no-store"


def test_inline_rejects_large_files_without_starting_export(client):
    test_client, root = client
    _write_ready_file(root, "session-a", b"x" * (30 * 1024 * 1024 + 1))

    response = test_client.get(
        "/api/session-files/sr_001/content",
        params={"sessionKey": "session-a", "disposition": "inline"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "resource_preview_too_large"


def test_delete_removes_only_manifest_entry_and_local_file(client):
    test_client, root = client
    target = _write_ready_file(root, "session-a")

    response = test_client.delete(
        "/api/session-files/sr_001",
        params={"sessionKey": "session-a"},
    )

    assert response.status_code == 200
    assert response.json() == {"resource_id": "sr_001", "deleted": True}
    assert not target.exists()
    assert ManifestStore(root).get("sr_001") is None

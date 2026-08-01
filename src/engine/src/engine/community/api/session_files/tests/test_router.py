from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from engine.community.api.session_files.router import router
from engine.community.core.resource_materialization.models import (
    ManifestEntry,
    hash_identifier,
)
from engine.community.core.resource_materialization.service import ManifestStore
from engine.community.core.session_files.service import SessionFileService
from engine.community.plugin_api.auth_gate.models import VerifyResult
from engine.community.plugin_api.auth_gate.protocol import AuthGateService
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, Module, singleton


class _AuthGate:
    def __init__(self) -> None:
        self.allowed = True
        self.calls: list[tuple[str, str, str]] = []

    async def verify(self, token: str, content: str, session_id: str) -> VerifyResult:
        self.calls.append((token, content, session_id))
        return VerifyResult(allowed=self.allowed)

    async def get_switch(self) -> bool:  # pragma: no cover - protocol support
        return True

    async def set_switch(self, enabled: bool) -> None:  # pragma: no cover
        return None


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
        )
    )
    return target


@pytest.fixture
def client(tmp_path: Path):
    auth = _AuthGate()
    service = SessionFileService(workspace_root_provider=lambda: tmp_path)

    class _Module(Module):
        def configure(self, binder) -> None:
            binder.bind(AuthGateService, to=InstanceProvider(auth), scope=singleton)
            binder.bind(SessionFileService, to=InstanceProvider(service), scope=singleton)

    app = FastAPI()
    attach_injector(app, Injector([_Module()]))
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client, tmp_path, auth


def test_list_requires_iam_identity(client):
    test_client, _, _ = client

    response = test_client.get("/api/session-files", params={"sessionKey": "session-a"})

    assert response.status_code == 401
    assert response.json()["detail"] == "missing_iam_token"


def test_list_and_content_are_scoped_to_manifest_session(client):
    test_client, root, auth = client
    target = _write_ready_file(root, "session-a")

    listed = test_client.get(
        "/api/session-files",
        params={"sessionKey": "session-a"},
        headers={"x-iam-token": "test-token"},
    )
    content = test_client.get(
        "/api/session-files/sr_001/content",
        params={"sessionKey": "session-a", "disposition": "attachment"},
        headers={"x-iam-token": "test-token"},
    )
    cross_session = test_client.get(
        "/api/session-files/sr_001/content",
        params={"sessionKey": "session-b"},
        headers={"x-iam-token": "test-token"},
    )

    assert listed.status_code == 200
    assert listed.json() == {
        "files": [
            {
                "resource_id": "sr_001",
                "display_name": "report.txt",
                "size_bytes": target.stat().st_size,
                "availability": "ready",
            }
        ]
    }
    assert content.status_code == 200
    assert content.content == b"report"
    assert content.headers["content-disposition"].startswith("attachment;")
    assert cross_session.status_code == 404
    assert len(auth.calls) == 3


def test_changed_and_missing_files_are_not_streamed(client):
    test_client, root, _ = client
    target = _write_ready_file(root, "session-a", b"report")
    headers = {"x-iam-token": "test-token"}
    target.write_bytes(b"change")

    changed_list = test_client.get(
        "/api/session-files",
        params={"sessionKey": "session-a"},
        headers=headers,
    )

    changed = test_client.get(
        "/api/session-files/sr_001/content",
        params={"sessionKey": "session-a"},
        headers=headers,
    )
    target.unlink()
    listed = test_client.get(
        "/api/session-files",
        params={"sessionKey": "session-a"},
        headers=headers,
    )
    missing = test_client.get(
        "/api/session-files/sr_001/content",
        params={"sessionKey": "session-a"},
        headers=headers,
    )

    assert changed.status_code == 409
    assert changed.json()["detail"] == "resource_changed"
    assert changed_list.json()["files"][0]["availability"] == "changed"
    assert listed.json()["files"][0]["availability"] == "missing"
    assert missing.status_code == 409
    assert missing.json()["detail"] == "resource_missing"


def test_delete_removes_only_manifest_entry_and_local_file(client):
    test_client, root, _ = client
    target = _write_ready_file(root, "session-a")

    response = test_client.delete(
        "/api/session-files/sr_001",
        params={"sessionKey": "session-a"},
        headers={"x-iam-token": "test-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"resource_id": "sr_001", "deleted": True}
    assert not target.exists()
    assert ManifestStore(root).get("sr_001") is None


def test_denied_iam_identity_cannot_access_files(client):
    test_client, root, auth = client
    _write_ready_file(root, "session-a")
    auth.allowed = False

    response = test_client.get(
        "/api/session-files",
        params={"sessionKey": "session-a"},
        headers={"x-iam-token": "test-token"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "session_file_access_denied"

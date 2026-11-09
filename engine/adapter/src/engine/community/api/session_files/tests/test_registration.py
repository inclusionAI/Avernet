import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, Module

from engine.community.api.session_files.tests import test_router as router_tests
from engine.community.api.session_files.router import router
from engine.community.core.session_files.service import SessionFileService
from engine.community.core.session_files.export_service import SessionFileExportService
from engine.community.core.session_files.tests.test_export_service import (
    _SessionFileClient,
)
from engine.community.core.resource_materialization.service import ManifestStore


client = router_tests.client


def test_post_register_returns_view_and_uses_existing_session_scope(client, caplog):
    http, root = client
    (root / "report.txt").write_text("private-file-body")
    with caplog.at_level(logging.INFO):
        response = http.post(
            "/api/session-files",
            params={"sessionKey": "session:exact"},
            json={"workspace_relative_path": "report.txt"},
            headers={
                "Authorization": "Bearer private-token",
                "Cookie": "secret-cookie",
            },
        )
    assert response.status_code == 200
    view = response.json()
    assert set(view) == {
        "resource_id",
        "display_name",
        "size_bytes",
        "availability",
        "uploaded_at",
        "creator_type",
        "creator_name",
        "created_at",
    }
    assert view["availability"] == "ready"
    assert view["creator_type"] == "bot"
    assert view["created_at"] == view["uploaded_at"]
    assert http.get(
        "/api/session-files", params={"sessionKey": "session:exact"}
    ).json() == {"files": [view]}
    resource_id = view["resource_id"]
    assert (
        http.get(
            f"/api/session-files/{resource_id}/content",
            params={"sessionKey": "session:exact"},
        ).content
        == b"private-file-body"
    )
    assert (
        http.get(
            f"/api/session-files/{resource_id}/content", params={"sessionKey": "other"}
        ).status_code
        == 404
    )
    assert (
        http.delete(
            f"/api/session-files/{resource_id}", params={"sessionKey": "session:exact"}
        ).status_code
        == 200
    )
    assert (root / "report.txt").read_text() == "private-file-body"
    assert "private-token" not in caplog.text
    assert "secret-cookie" not in caplog.text
    assert "private-file-body" not in caplog.text
    records = [
        record for record in caplog.records if record.name == "engine.session_files.api"
    ]
    assert {record.status for record in records} == {"request", 200}
    assert all(record.sessionKey == "session:exact" for record in records)
    assert records[-1].creator_type == "bot"
    assert records[-1].created_at == view["created_at"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"workspace_relative_path": 123},
        {"workspace_relative_path": "report", "creator_type": "user"},
        {"workspace_relative_path": "report", "creator_name": "Other Bot"},
        {"workspace_relative_path": "report", "created_at": "2000-01-01T00:00:00Z"},
        {"workspace_relative_path": "report", "token": "test-token"},
        {"workspace_relative_path": None},
    ],
)
def test_post_requires_exact_strict_body(client, body):
    http, root = client
    (root / "report").write_text("hello")
    response = http.post("/api/session-files", params={"sessionKey": "a"}, json=body)
    assert response.status_code == 422
    assert http.get("/api/session-files", params={"sessionKey": "a"}).json() == {
        "files": []
    }


def test_post_failure_logs_safe_status_and_requires_session(client, caplog):
    http, _ = client
    with caplog.at_level(logging.INFO):
        response = http.post(
            "/api/session-files",
            params={"sessionKey": "business:a"},
            json={"workspace_relative_path": "missing"},
        )
    assert response.status_code == 400
    records = [
        record for record in caplog.records if record.name == "engine.session_files.api"
    ]
    assert records[-1].status == 400
    assert records[-1].sessionKey == "business:a"
    assert records[-1].elapsed_ms >= 0
    assert (
        http.post(
            "/api/session-files", json={"workspace_relative_path": "report"}
        ).status_code
        == 422
    )


def test_registered_small_attachment_download_requires_no_baas_tenant(client):
    http, root = client
    (root / "report").write_text("small-file")
    view = http.post(
        "/api/session-files",
        params={"sessionKey": "a"},
        json={"workspace_relative_path": "report"},
    ).json()
    response = http.get(
        f"/api/session-files/{view['resource_id']}/content",
        params={"sessionKey": "a", "disposition": "attachment"},
    )
    assert response.status_code == 200
    assert response.content == b"small-file"
    assert response.headers["content-disposition"].startswith("attachment;")


@pytest.mark.parametrize(
    "source", ["directory", "missing", "/etc/passwd", "../escape", "link"]
)
def test_invalid_source_maps_to_client_error_not_storage_outage(client, source, caplog):
    http, root = client
    (root / "directory").mkdir()
    (root / "link").symlink_to(root / "directory", target_is_directory=True)
    with caplog.at_level(logging.INFO):
        response = http.post(
            "/api/session-files",
            params={"sessionKey": "a"},
            json={"workspace_relative_path": source},
            headers={
                "Authorization": "Bearer private-auth",
                "Cookie": "private-cookie",
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"] != "manifest_unavailable"
    assert "private-auth" not in caplog.text
    assert "private-cookie" not in caplog.text
    assert http.get("/api/session-files", params={"sessionKey": "a"}).json() == {
        "files": []
    }


def test_registration_storage_failure_maps_to_503_without_exception_secrets(
    client, monkeypatch, caplog
):
    http, root = client
    (root / "report").write_text("private-content")

    def fail(*args, **kwargs):
        raise OSError("private-storage-credential")

    monkeypatch.setattr(ManifestStore, "upsert", fail)
    with caplog.at_level(logging.INFO):
        response = http.post(
            "/api/session-files",
            params={"sessionKey": "a"},
            json={"workspace_relative_path": "report"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "registration_failed"}
    assert "private-storage-credential" not in caplog.text
    assert "private-content" not in caplog.text
    assert ManifestStore(root).list_entries() == []
    assert (root / "report").read_text() == "private-content"


def test_live_small_to_large_attachment_uploads_current_version(
    tmp_path,
):
    source = tmp_path / "large.bin"
    content = b"x" * (30 * 1024 * 1024 + 1)
    source.write_bytes(b"small")
    service = SessionFileService(
        workspace_root_provider=lambda: tmp_path,
        default_baas_tenant="configured-tenant",
    )
    baas = _SessionFileClient()
    exports = SessionFileExportService(session_file_service=service, export_client=baas)

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SessionFileService, to=InstanceProvider(service))
            binder.bind(SessionFileExportService, to=InstanceProvider(exports))

    app = FastAPI()
    attach_injector(app, Injector([Bindings()]))
    app.include_router(router)
    with TestClient(app) as http:
        view = http.post(
            "/api/session-files",
            params={"sessionKey": "a"},
            json={"workspace_relative_path": "large.bin"},
        ).json()
        source.write_bytes(content)
        path = f"/api/session-files/{view['resource_id']}/content"
        params = {"sessionKey": "a", "disposition": "attachment"}
        queued = http.get(path, params=params)
        assert queued.status_code == 202
        assert queued.headers["retry-after"] == "2"
        import asyncio

        async def wait_for_exports():
            await asyncio.gather(*(job.task for job in list(exports._jobs.values())))

        http.portal.call(wait_for_exports)
        response = http.get(path, params=params)
        # Promotion changes the export cache key; wait for the share-only follow-up.
        if response.status_code == 202:
            http.portal.call(wait_for_exports)
            response = http.get(path, params=params)
        assert response.status_code == 200
        assert response.json()["delivery"] == "external_url"
        assert response.json()["size_bytes"] == len(content)
        assert len(baas.upload_calls) == 1
        assert baas.upload_calls[0][1] == content
        assert baas.grant_calls[0][0].tenant == "configured-tenant"
        assert baas.grant_calls[0][0].transfer_id == ""
        assert baas.complete_calls[0].transfer_id == "replacement-transfer"
        assert source.read_bytes() == content


def test_live_response_consumes_pinned_bytes_after_source_symlink_swap(client, monkeypatch):
    http, root = client
    source = root / "report"
    source.write_bytes(b"observed content")
    view = http.post("/api/session-files", params={"sessionKey": "a"}, json={"workspace_relative_path": "report"}).json()
    original_open = SessionFileService.open_content
    streams = []
    def open_and_swap(self, **kwargs):
        content = original_open(self, **kwargs)
        streams.append(content.stream)
        source.unlink()
        source.symlink_to(root / "private")
        (root / "private").write_bytes(b"credential-secret")
        return content
    monkeypatch.setattr(SessionFileService, "open_content", open_and_swap)
    response = http.get(f"/api/session-files/{view['resource_id']}/content", params={"sessionKey": "a"})
    assert response.status_code == 200
    assert response.content == b"observed content"
    assert int(response.headers["content-length"]) == len(response.content)
    assert streams[0].closed


def test_live_large_to_small_attachment_returns_current_direct_content(client):
    http, root = client
    source = root / "report"
    with source.open("wb") as stream:
        stream.truncate(30 * 1024 * 1024 + 1)
    view = http.post("/api/session-files", params={"sessionKey": "a"}, json={"workspace_relative_path": "report"}).json()
    source.write_bytes(b"now small")
    response = http.get(f"/api/session-files/{view['resource_id']}/content", params={"sessionKey": "a", "disposition": "attachment"})
    assert response.status_code == 200
    assert response.content == b"now small"
    assert response.headers["content-length"] == "9"


def test_live_binary_has_no_store_but_legacy_headers_unchanged(client):
    from engine.community.api.session_files.tests.test_router import _write_ready_file
    http, root = client
    (root / "report").write_bytes(b"one")
    view = http.post("/api/session-files", params={"sessionKey": "a"}, json={"workspace_relative_path": "report"}).json()
    response = http.get(f"/api/session-files/{view['resource_id']}/content", params={"sessionKey": "a"})
    assert response.headers["cache-control"] == "no-store"
    (root / "report").write_bytes(b"two")
    assert http.get(f"/api/session-files/{view['resource_id']}/content", params={"sessionKey": "a"}).content == b"two"
    _write_ready_file(root, "legacy")
    legacy = http.get("/api/session-files/sr_001/content", params={"sessionKey": "legacy"})
    assert legacy.status_code == 200
    assert "cache-control" not in legacy.headers


def test_live_content_boundary_logs_terminal_success_failure_without_secrets(client, caplog):
    http, root = client
    (root / "report").write_bytes(b"private-content-value")
    view = http.post("/api/session-files", params={"sessionKey": "business:a"}, json={"workspace_relative_path": "report"}).json()
    headers = {"Authorization": "Bearer private-token-value", "Cookie": "private-cookie-value"}
    with caplog.at_level(logging.INFO, logger="engine.session_files.api"):
        success = http.get(f"/api/session-files/{view['resource_id']}/content", params={"sessionKey": "business:a"}, headers=headers)
        (root / "report").unlink()
        failed = http.get(f"/api/session-files/{view['resource_id']}/content", params={"sessionKey": "business:a"}, headers=headers)
    assert success.status_code == 200
    assert failed.status_code == 409
    records = [record for record in caplog.records if record.name == "engine.session_files.api" and getattr(record, "operation", "") == "session_files.content"]
    assert [record.status for record in records] == ["request", 200, "request", 409]
    assert all(record.sessionKey == "business:a" for record in records)
    assert all(record.resource_id == view["resource_id"] for record in records)
    assert all(record.disposition == "inline" for record in records)
    assert all(record.system == "engine" and record.elapsed_ms >= 0 for record in records)
    assert "engine.session_files.proxypass_access operation=content" in caplog.text
    assert all(record.method == "GET" and record.route == "/api/session-files/{resource_id}/content" for record in records)
    assert records[1].size_bytes == len(success.content)
    assert records[1].event == "success"
    assert records[-1].event == "failure"
    for secret in ["private-content-value", "private-token-value", "private-cookie-value"]:
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_content_boundary_stream_failure_logs_no_exception_or_body(caplog):
    from fastapi.responses import StreamingResponse
    from engine.community.api.session_files.content_diagnostics import logged_content_response

    async def chunks():
        yield b"private-stream-body"
        raise OSError("private-storage-password")

    async def action():
        return StreamingResponse(chunks(), headers={"Content-Length": "99"})

    with caplog.at_level(logging.INFO, logger="engine.session_files.api"):
        response = await logged_content_response(action, session_key="a", resource_id="r", disposition="attachment")
        with pytest.raises(OSError, match="private-storage-password"):
            async for _ in response.body_iterator:
                pass
    records = [record for record in caplog.records if record.name == "engine.session_files.api"]
    assert [record.event for record in records] == ["request", "failure"]
    assert records[-1].error_type == "OSError"
    assert records[-1].size_bytes == 99
    assert "private-storage-password" not in caplog.text
    assert "private-stream-body" not in caplog.text


@pytest.mark.asyncio
async def test_content_boundary_external_success_never_logs_signed_url(caplog):
    from fastapi.responses import JSONResponse
    from engine.community.api.session_files.content_diagnostics import logged_content_response

    async def action():
        return JSONResponse({"delivery": "external_url", "size_bytes": 123, "download_url": "https://example.test/private?token=test-token"})

    with caplog.at_level(logging.INFO, logger="engine.session_files.api"):
        await logged_content_response(action, session_key="a", resource_id="r", disposition="attachment")
    records = [record for record in caplog.records if record.name == "engine.session_files.api"]
    assert [record.event for record in records] == ["request", "success"]
    assert records[-1].status == 200
    assert records[-1].size_bytes == 123
    assert "test-token" not in caplog.text
    assert "example.test" not in caplog.text


@pytest.mark.asyncio
async def test_content_boundary_preparing_logs_terminal_202(caplog):
    from fastapi.responses import JSONResponse
    from engine.community.api.session_files.content_diagnostics import logged_content_response

    async def action():
        return JSONResponse(status_code=202, content={"status": "preparing_download", "retry_after_seconds": 2})

    with caplog.at_level(logging.INFO, logger="engine.session_files.api"):
        await logged_content_response(action, session_key="a", resource_id="r", disposition="attachment")
    records = [record for record in caplog.records if record.name == "engine.session_files.api"]
    assert [record.event for record in records] == ["request", "success"]
    assert records[-1].status == 202
    assert records[-1].size_bytes is None

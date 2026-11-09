from __future__ import annotations

import pytest
from engine.community.api.resource_materialization.router import (
    create_resource_materialization,
    stream_resource_content,
)
from engine.community.core.resource_materialization.models import (
    MaterializationRequest,
    MaterializedContent,
)
from engine.community.core.resource_materialization.service import (
    ResourceNotMaterializedError,
)
from fastapi import BackgroundTasks, HTTPException


class _Service:
    def __init__(self) -> None:
        self.requests = []

    async def materialize(self, request):
        self.requests.append(request)

    def open_content(self, *, resource_id, disposition):
        if resource_id == "missing":
            raise ResourceNotMaterializedError("resource_not_materialized")
        return MaterializedContent(
            path=self.path,
            filename="report.txt",
            media_type="text/plain",
            content_disposition=f"{disposition}; filename*=UTF-8''report.txt",
            size_bytes=self.path.stat().st_size,
        )


def _request() -> MaterializationRequest:
    return MaterializationRequest(
        resource_id="sr_001",
        transfer_id="transfer-001",
        task_id="task-001",
        task_version=1,
        scope_key_hash="scope_abc",
        session_key_hash="session_abc",
        device_path=(
            "workspace/.teamclaw/session-files/scope_abc/session_abc/"
            "sr_001/report.txt"
        ),
        filename="report.txt",
    )


@pytest.mark.asyncio
async def test_accepts_proxied_request_and_schedules_service():
    tasks = BackgroundTasks()
    service = _Service()

    response = await create_resource_materialization(
        _request(),
        tasks,
        service=service,
    )

    assert response["accepted"] is True
    assert response["task_id"] == "task-001"
    assert len(tasks.tasks) == 1
    await tasks()
    assert service.requests[0].resource_id == "sr_001"


@pytest.mark.asyncio
async def test_content_streams_manifest_file_over_proxied_route(tmp_path):
    service = _Service()
    service.path = tmp_path / "report.txt"
    service.path.write_bytes(b"content")

    response = await stream_resource_content(
        "sr_001",
        disposition="attachment",
        service=service,
    )

    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-length"] == "7"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert [chunk async for chunk in response.body_iterator] == [b"content"]


@pytest.mark.asyncio
async def test_content_returns_materializing_when_manifest_file_is_missing(tmp_path):
    service = _Service()
    service.path = tmp_path / "report.txt"
    service.path.write_bytes(b"content")

    with pytest.raises(HTTPException) as exc:
        await stream_resource_content(
            "missing",
            disposition="inline",
            service=service,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "resource_not_materialized"


@pytest.mark.parametrize("source_kind", ["baas_session_file", "temporary_url", "workspace_file"])
def test_legacy_http_refuses_live_ids_and_still_serves_controlled_copies(tmp_path, source_kind):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapi_injector import attach_injector
    from injector import Injector, InstanceProvider, Module
    from engine.community.api.resource_materialization.router import router
    from engine.community.core.resource_materialization.service import ManifestStore, ResourceMaterializationService
    from engine.community.core.resource_materialization.tests.test_service import _PullClient, _CallbackClient
    from engine.community.core.session_files.service import SessionFileService
    from engine.community.core.session_files.tests.test_export_service import _write_file

    _, _, legacy_path = _write_file(tmp_path)
    store = ManifestStore(tmp_path)
    store.upsert(store.get("sr_001").model_copy(update={"source_kind": source_kind}))
    (tmp_path / "live.txt").write_bytes(b"live bytes")
    view = SessionFileService(workspace_root_provider=lambda: tmp_path).register_file(
        session_key="a", workspace_relative_path="live.txt"
    )
    service = ResourceMaterializationService(
        pull_client=_PullClient(b"unused"), callback_client=_CallbackClient(),
        workspace_root_provider=lambda: tmp_path,
    )
    class Bindings(Module):
        def configure(self, binder):
            binder.bind(ResourceMaterializationService, to=InstanceProvider(service))
    app = FastAPI()
    attach_injector(app, Injector([Bindings()]))
    app.include_router(router)
    with TestClient(app) as http:
        live = http.get(f"/api/resource-materializations/{view.resource_id}/content")
        assert live.status_code == 409
        assert live.json() == {"detail": "resource_not_materialized"}
        legacy = http.get("/api/resource-materializations/sr_001/content")
        assert legacy.status_code == 200
        assert legacy.content == legacy_path.read_bytes()
        assert int(legacy.headers["content-length"]) == len(legacy.content)

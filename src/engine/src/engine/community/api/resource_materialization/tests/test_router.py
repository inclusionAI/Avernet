from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, HTTPException

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
from engine.community.plugin_api.auth_gate.models import VerifyResult


class _AuthGate:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    async def verify(self, token: str, content: str, session_id: str):
        return VerifyResult(allowed=self.allowed)


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
async def test_accepts_authenticated_request_and_schedules_service():
    tasks = BackgroundTasks()
    service = _Service()

    response = await create_resource_materialization(
        _request(),
        tasks,
        x_iam_token="iam-token",
        auth_gate_service=_AuthGate(),
        service=service,
    )

    assert response["accepted"] is True
    assert response["task_id"] == "task-001"
    assert len(tasks.tasks) == 1
    await tasks()
    assert service.requests[0].resource_id == "sr_001"


@pytest.mark.asyncio
async def test_rejects_missing_internal_identity():
    with pytest.raises(HTTPException) as exc:
        await create_resource_materialization(
            _request(),
            BackgroundTasks(),
            x_iam_token=None,
            auth_gate_service=_AuthGate(),
            service=_Service(),
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_rejects_denied_internal_identity():
    with pytest.raises(HTTPException) as exc:
        await create_resource_materialization(
            _request(),
            BackgroundTasks(),
            x_iam_token="denied",
            auth_gate_service=_AuthGate(allowed=False),
            service=_Service(),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_content_stream_requires_internal_auth_and_streams_manifest_file(tmp_path):
    service = _Service()
    service.path = tmp_path / "report.txt"
    service.path.write_bytes(b"content")

    response = await stream_resource_content(
        "sr_001",
        disposition="attachment",
        x_iam_token="iam-token",
        auth_gate_service=_AuthGate(),
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
            x_iam_token="iam-token",
            auth_gate_service=_AuthGate(),
            service=service,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "resource_not_materialized"

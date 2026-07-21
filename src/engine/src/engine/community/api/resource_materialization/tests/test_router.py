from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException
import pytest

from engine.community.api.resource_materialization.router import (
    create_resource_materialization,
)
from engine.community.core.resource_materialization.models import (
    MaterializationRequest,
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

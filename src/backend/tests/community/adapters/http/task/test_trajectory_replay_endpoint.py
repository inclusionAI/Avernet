"""HTTP contract tests for internal and public trajectory replay endpoints."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.task.router import router as public_router
from agentclaw.community.adapters.http.task.trajectory_replay_router import router as internal_router
from agentclaw.community.api.task.task_context_service import TaskContextServiceProtocol
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    TrajectoryActionType,
    TrajectoryEvent,
)
from agentclaw.community.core.task.task_context.task_trajectory.replay import (
    TaskTrajectoryReplay,
    TrajectoryReplayFrame,
)


class _StubTaskContextService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float, int, int | None]] = []

    async def replay_trajectory(
        self, task_id, *, playback_rate=1.0, from_sequence=0, limit=None
    ):
        self.calls.append((task_id, playback_rate, from_sequence, limit))
        event = TrajectoryEvent(
            task_id, "n1", TrajectoryActionType.RELAY, "report", 0, 1000, 1000,
            holder_id="bot-a",
        )
        return TaskTrajectoryReplay(
            task_id=task_id,
            playback_rate=playback_rate,
            source_started_at=1000,
            source_ended_at=1000,
            duration_ms=0,
            from_sequence=from_sequence,
            total_frames=1,
            frames=[TrajectoryReplayFrame(0, 0, 0, event)],
        )

    async def get_trajectory(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("replay endpoint must not call get_trajectory directly")

    def emit_trajectory_event(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("replay endpoint must not emit events")


class _StubModule(Module):
    def __init__(self, service: _StubTaskContextService) -> None:
        self._service = service

    @singleton
    @provider
    def task_context_service(self) -> TaskContextServiceProtocol:
        return self._service


def _client(service: _StubTaskContextService) -> TestClient:
    app = FastAPI()
    app.include_router(public_router)
    app.include_router(internal_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "owner"}
    attach_injector(app, Injector([_StubModule(service)]))
    return TestClient(app)


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "/openapi/v1/collaboration/tasks/trajectory/replay",
        "/api/v1/collaboration/tasks/trajectory/replay",
    ],
)
def test_replay_endpoint_returns_same_contract_for_public_and_internal(path):
    service = _StubTaskContextService()
    response = _client(service).get(
        path,
        params={
            "task_id": "t1",
            "playback_rate": "2.0",
            "from_sequence": "0",
            "limit": "1",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["task_id"] == "t1"
    assert data["playback_rate"] == 2.0
    assert data["total_frames"] == 1
    assert data["frames"][0]["sequence"] == 0
    assert data["frames"][0]["event"]["action_type"] == "relay"
    assert data["frames"][0]["event"]["holder_id"] == "bot-a"
    assert service.calls == [("t1", 2.0, 0, 1)]


@pytest.mark.unit
def test_replay_endpoint_rejects_out_of_range_playback_rate_before_service_call():
    service = _StubTaskContextService()
    response = _client(service).get(
        "/api/v1/collaboration/tasks/trajectory/replay",
        params={"task_id": "t1", "playback_rate": "20"},
    )

    assert response.status_code == 422
    assert service.calls == []

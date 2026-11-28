"""Contract tests for the authenticated Task Space listing."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task.router import router
from agentclaw.community.api.space_service import SpaceServiceProtocol
from agentclaw.community.api.task.task_artifact_service import (
    TaskArtifactApiServiceProtocol,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.errors import TaskNotFoundError
from agentclaw.community.core.spaces.models import (
    SpaceJoinStatus,
    SpaceListScope,
    SpaceRecord,
    SpaceRole,
    SpaceSummaryRecord,
    SpaceType,
)


class _Bindings(Module):
    def __init__(self, service: MagicMock) -> None:
        self._service = service

    def configure(self, binder) -> None:
        binder.bind(SpaceServiceProtocol, to=self._service)
        missing_task_service = MagicMock(spec=TaskServiceProtocol)
        missing_task_service.get_task_dashboard.side_effect = TaskNotFoundError(
            "spaces"
        )
        binder.bind(TaskServiceProtocol, to=missing_task_service)
        binder.bind(
            TaskArtifactApiServiceProtocol,
            to=MagicMock(spec=TaskArtifactApiServiceProtocol),
        )


def _client(service: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="provider-id-1",
        staffId="staff-1",
        operatorName="owner",
    )
    attach_injector(app, Injector([_Bindings(service)]))
    return TestClient(app)


def _summary(*, space_id: int, name: str, space_type: SpaceType) -> SpaceSummaryRecord:
    now = datetime(2026, 11, 25, tzinfo=timezone.utc)
    return SpaceSummaryRecord(
        space=SpaceRecord(
            id=space_id,
            space_code=f"space-{space_id}",
            space_type=space_type,
            name=name,
            personal_owner_id="staff-1" if space_type is SpaceType.PERSONAL else None,
            env="test",
            created_by="staff-1",
            updated_by="staff-1",
            gmt_created=now,
            gmt_modified=now,
        ),
        current_user_role=SpaceRole.ADMIN,
        join_status=SpaceJoinStatus.JOINED,
        member_count=1,
        owner_count=1,
    )


@pytest.mark.unit
def test_list_task_spaces_uses_authenticated_staff_and_maps_minimal_page() -> None:
    service = MagicMock(spec=SpaceServiceProtocol)
    service.list_spaces.return_value = (
        2,
        [
            _summary(space_id=7, name="Personal", space_type=SpaceType.PERSONAL),
            _summary(space_id=9, name="Team", space_type=SpaceType.TEAM),
        ],
    )

    response = _client(service).get(
        "/api/v1/collaboration/tasks/spaces",
        params={
            "page_no": 2,
            "page_size": 5,
            "scope": "accessible",
            "user_id": "spoofed-user",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "total": 2,
        "items": [
            {"space_id": 7, "space_name": "Personal", "space_type": "PERSONAL"},
            {"space_id": 9, "space_name": "Team", "space_type": "TEAM"},
        ],
    }
    service.list_spaces.assert_called_once_with(
        user_id="staff-1",
        keyword=None,
        space_type=None,
        page_no=2,
        page_size=5,
        scope=SpaceListScope.ACCESSIBLE,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "params",
    [
        {"page_no": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"scope": "private"},
        {"scope": "all"},
        {"space_type": "UNKNOWN"},
    ],
)
def test_list_task_spaces_rejects_invalid_query(params: dict[str, object]) -> None:
    service = MagicMock(spec=SpaceServiceProtocol)

    response = _client(service).get(
        "/api/v1/collaboration/tasks/spaces",
        params=params,
    )

    assert response.status_code == 422, response.text
    service.list_spaces.assert_not_called()

"""Contract tests for the internal personal-Space batch query."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.spaces_internal.router import router
from agentclaw.community.api.space_service import SpaceServiceProtocol
from agentclaw.community.core.spaces.errors import (
    SpaceNotFoundError,
    SpaceScTeamBindingNotFoundError,
    SpaceScTeamRepairConflictError,
    SpaceScTeamRepairNotApplicableError,
)
from agentclaw.community.core.spaces.models import (
    PersonalSpaceLookupRecord,
    SpaceScTeamRepairResult,
    SpaceScTeamRepairStatus,
)
from agentclaw.community.plugin_api.skill_center_client import SkillCenterTeamQueryError


@pytest.fixture
def service():
    return MagicMock()


@pytest.fixture
def client(service):
    class _Bindings(Module):
        def configure(self, binder):
            binder.bind(SpaceServiceProtocol, to=service)

    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([_Bindings()]))
    return TestClient(app)


def test_batch_query_returns_found_and_missing_in_request_order(client, service):
    service.batch_query_personal.return_value = [
        PersonalSpaceLookupRecord(user_id="user-2", space_id=22, found=True),
        PersonalSpaceLookupRecord(user_id="user-1", space_id=None, found=False),
    ]

    response = client.post(
        "/api/internal/spaces/personal/batch-query",
        json={"user_id": [" user-2 ", "user-1", "user-2"]},
    )

    assert response.status_code == 200
    assert response.json()["data"]["list"] == [
        {"user_id": "user-2", "space_id": 22, "found": True},
        {"user_id": "user-1", "space_id": None, "found": False},
    ]
    service.batch_query_personal.assert_called_once_with(user_ids=["user-2", "user-1"])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user_id": []},
        {"user_id": [" "]},
        {"user_id": [str(index) for index in range(501)]},
        {"user_id": ["user-1"], "env": "prod"},
        {"user_ids": ["user-1"]},
    ],
)
def test_batch_query_rejects_invalid_user_id_lists(client, service, payload):
    response = client.post("/api/internal/spaces/personal/batch-query", json=payload)

    assert response.status_code == 422
    service.batch_query_personal.assert_not_called()


def test_batch_query_contract_uses_singular_user_id_field(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    properties = schema["PersonalSpaceBatchQueryRequest"]["properties"]

    assert set(properties) == {"user_id"}


def test_repair_sc_team_binding_returns_repaired_result(client, service):
    service.repair_sc_team_binding.return_value = SpaceScTeamRepairResult(
        space_id=7,
        status=SpaceScTeamRepairStatus.REPAIRED,
        sc_team_id="sc-team-7",
    )

    response = client.post("/api/internal/spaces/7/sc-team-binding/repair")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "space_id": 7,
        "status": "REPAIRED",
        "sc_team_id": "sc-team-7",
    }
    service.repair_sc_team_binding.assert_called_once_with(space_id=7)


def test_repair_sc_team_binding_returns_already_bound_result(client, service):
    service.repair_sc_team_binding.return_value = SpaceScTeamRepairResult(
        space_id=7,
        status=SpaceScTeamRepairStatus.ALREADY_BOUND,
        sc_team_id="existing-team",
    )

    response = client.post("/api/internal/spaces/7/sc-team-binding/repair")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ALREADY_BOUND"
    assert response.json()["data"]["sc_team_id"] == "existing-team"


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (SpaceNotFoundError("space 7 not found"), 404, "SPACE_NOT_FOUND"),
        (
            SpaceScTeamBindingNotFoundError("SC mapping not found"),
            404,
            "SC_TEAM_BINDING_NOT_FOUND",
        ),
        (
            SpaceScTeamRepairNotApplicableError("TEAM only"),
            409,
            "SC_TEAM_REPAIR_NOT_APPLICABLE",
        ),
        (
            SpaceScTeamRepairConflictError("concurrent change"),
            409,
            "SC_TEAM_REPAIR_CONFLICT",
        ),
        (
            SkillCenterTeamQueryError("SC unavailable"),
            502,
            "SKILL_CENTER_QUERY_FAILED",
        ),
    ],
)
def test_repair_sc_team_binding_maps_domain_failures(
    client, service, failure, expected_status, expected_code
):
    service.repair_sc_team_binding.side_effect = failure

    response = client.post("/api/internal/spaces/7/sc-team-binding/repair")

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    assert response.json()["detail"]["message"] == str(failure)


def test_repair_sc_team_binding_rejects_invalid_space_id(client, service):
    response = client.post("/api/internal/spaces/not-an-id/sc-team-binding/repair")

    assert response.status_code == 422
    service.repair_sc_team_binding.assert_not_called()

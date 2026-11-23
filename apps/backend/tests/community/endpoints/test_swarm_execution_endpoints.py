"""HTTP conformance: real execution service, authenticated delivery and envelopes."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task.context_collab_methods import router
from tests.community.core.task.context_collab.conftest import db as db, engine as engine
from tests.community.core.task.context_collab.swarm_workflow_support import (
    deploy,
    OWNER,
)


@pytest.fixture
def swarm_client(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path)
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, injector)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        "owner", OWNER.actor.principal_id, "owner"
    )
    path = f"/api/v1/collaboration/task/context_collab/{driver.collet_id}/method-bindings/{driver.binding_id}/swarm"
    with TestClient(app) as client:
        yield client, path, driver, worker


def test_stage_step_and_interaction_end_to_end_http(swarm_client):
    client, path, driver, worker = swarm_client
    response = client.get(path + "/stages")
    assert response.status_code == 200
    preview = response.json()["data"]
    assert len(preview["stages"]) == 4
    body = {"request_id": "one", "expected_version": preview["version"]}
    response = client.post(path + "/step", json=body)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["dispatched"] and len(worker.calls) == 1
    assert client.post(path + "/step", json=body).json()["data"] == result
    graph = client.get(path + "/stages").json()["data"]
    stage = graph["stages"][0]
    assert stage["responsibility"] and stage["outputs"]["bindings"]
    assert stage["executor_refs"]
    message = {
        "request_id": "feedback",
        "expected_version": graph["version"],
        "stage_ref": stage["stage_id"],
        "session_ref": stage["session_ref"],
        "content": "Please prioritize usability",
    }
    response = client.post(path + "/interactions", json=message)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "ignore"
    graph = client.get(path + "/stages").json()["data"]
    assert graph["stages"][0]["messages"][0]["content"] == message["content"]


@pytest.mark.parametrize(
    "body",
    [
        {"request_id": "x", "expected_version": True},
        {"request_id": "x", "expected_version": 1, "actor_ref": "fake"},
        {"request_id": "", "expected_version": 1},
    ],
)
def test_invalid_step_shape_is_422(swarm_client, body):
    client, path, driver, worker = swarm_client
    response = client.post(path + "/step", json=body)
    assert response.status_code == 422 and not worker.calls


def test_stale_version_and_unknown_binding_map_to_envelope(swarm_client):
    client, path, driver, worker = swarm_client
    response = client.post(
        path + "/step", json={"request_id": "stale", "expected_version": 999}
    )
    assert response.status_code == 409
    assert response.json()["code"] == 409000
    response = client.get(path.replace(driver.binding_id, "unknown") + "/stages")
    assert response.status_code == 404
    assert not worker.calls


def test_human_http_client_cannot_forge_worker_clarification(swarm_client):
    client, path, driver, worker = swarm_client
    graph = client.get(path + "/stages").json()["data"]
    stage = graph["stages"][0]
    response = client.post(
        path + "/interactions",
        json={
            "request_id": "question",
            "expected_version": graph["version"],
            "stage_ref": stage["stage_id"],
            "session_ref": stage["session_ref"],
            "content": "Question",
            "kind": "clarification_request",
        },
    )
    assert response.status_code == 422 and not worker.calls

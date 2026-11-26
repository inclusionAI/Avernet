"""HTTP requests exercise the real service and SQL repository through a test injector."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task.product_profiles import (
    router,
    ProfileCreate,
    create_profile,
)
from agentclaw.community.api.task.product_profile_service import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    encode_composition,
)


@pytest.fixture
def client(runtime):
    injector = Injector(auto_bind=False)
    injector.binder.bind(ProductProfileServiceProtocol, to=runtime.service)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        "test", "profile-owner", "test"
    )
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield client


BASE = "/api/v1/collaboration/task/products"


def test_http_create_query_messages_and_interaction_are_wired(client, runtime):
    h = runtime
    response = client.post(
        BASE + "/profiles",
        json={
            "name": "coding",
            "composition": encode_composition(h.composition),
            "stages": h.stages,
            "request_id": "http-profile",
        },
    )
    assert response.status_code == 200, response.text
    profile = response.json()["data"]
    assert profile["stages"] == h.stages and profile[
        "composition"
    ] == encode_composition(h.composition)
    response = client.post(
        BASE + "/collets",
        json={
            "profile_id": profile["id"],
            "content": "request",
            "request_id": "http-collet",
        },
    )
    assert response.status_code == 200, response.text
    collet = response.json()["data"]
    response = client.get(
        f"{BASE}/collets/{collet['id']}/rollouts", params={"all_rollout": "false"}
    )
    assert response.status_code == 200, response.text
    queried = response.json()["data"]
    assert queried["revision"] == 1 and len(queried["rollouts"]) == 1
    stage_id = queried["rollouts"][0]["stages"][0]["stage_rollout_id"]
    response = client.get(f"{BASE}/stages/{stage_id}/messages")
    assert response.status_code == 200 and response.json()["data"]["bound"] is False
    response = client.post(
        f"{BASE}/rollouts/{queried['current_rollout_id']}/interactive",
        json={
            "interactive_context": "review progress",
            "request_id": "http-interaction",
            "expected_revision": queried["revision"],
        },
    )
    assert (
        response.status_code == 200 and response.json()["data"]["type"] == "DO_NOTHING"
    )
    assert not h.calls


@pytest.mark.parametrize(
    "field,value", [("tenant", "other"), ("context", {}), ("session_id", "injected")]
)
def test_http_rejects_caller_scope_or_session_override(client, runtime, field, value):
    body = {
        "name": "coding",
        "composition": encode_composition(runtime.composition),
        "stages": runtime.stages,
        "request_id": "invalid-http",
        field: value,
    }
    assert client.post(BASE + "/profiles", json=body).status_code == 422


def test_http_strict_boolean_identity_is_rejected(client):
    response = client.post(
        BASE + "/collets",
        json={"profile_id": True, "content": "request", "request_id": "bad-id"},
    )
    assert response.status_code == 422


def test_http_error_logging_redacts_entire_inline_configuration(runtime):
    import inspect
    from agentclaw.community.adapters.http.error_logging import (
        capture_call_params,
        REDACTED,
    )

    body = ProfileCreate(
        name="name",
        composition=encode_composition(runtime.composition),
        stages="contains confidential content",
        request_id="log-safe",
    )
    params = capture_call_params(
        inspect.signature(create_profile), (), {"private_body": body}
    )
    assert params["private_body"] == REDACTED
    assert "confidential" not in str(params)


def test_profile_inline_request_is_bounded_before_yaml_and_composition_decode(
    client, runtime
):
    from agentclaw.community.adapters.http.task.product_body_limits import (
        MAX_PRODUCT_BODY_BYTES,
    )

    response = client.post(
        BASE + "/profiles",
        content=b"x" * (MAX_PRODUCT_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert not runtime.calls

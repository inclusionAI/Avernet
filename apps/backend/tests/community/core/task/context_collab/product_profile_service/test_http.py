"""HTTP requests exercise the real service and SQL repository through a test injector."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector
from agentclaw.community.adapters.http.app import _domain_error_handler
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task.product_profiles import (
    router,
    ProfileCreate,
    ColletCreate,
    Interaction,
    create_profile,
)
from agentclaw.community.api.task.product_profile_service import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.errors import DomainError
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    encode_composition,
)


@pytest.fixture
def client(runtime):
    injector = Injector(auto_bind=False)
    injector.binder.bind(ProductProfileServiceProtocol, to=runtime.service)
    app = FastAPI()
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        "test", "profile-owner", "test"
    )
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield client


BASE = "/api/v1/collaboration/task/products"


def test_http_legacy_query_messages_and_interaction_are_wired(client, created):
    h = created
    profile = {"id": h.profile.id}
    response = client.post(
        BASE + "/collets",
        json={
            "profile_id": profile["id"],
            "content": "request",
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
            "interactive_uuid": "http-intervention",
        },
    )
    assert (
        response.status_code == 200 and response.json()["data"]["type"] == "DO_NOTHING"
    )
    assert not h.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant", "other"),
        ("context", {}),
        ("request_id", "removed-client-key"),
        ("session_id", "injected"),
    ],
)
def test_http_rejects_caller_scope_or_session_override(client, runtime, field, value):
    body = {
        "name": "coding",
        "stages": runtime.stages,
        field: value,
    }
    assert client.post(BASE + "/profiles", json=body).status_code == 422


@pytest.mark.parametrize("schema", [ProfileCreate, ColletCreate, Interaction])
def test_http_schema_has_no_actor_context_or_request_id(schema):
    assert {"context", "request_id"}.isdisjoint(schema.model_fields)
    assert {"context", "request_id"}.isdisjoint(
        schema.model_json_schema()["properties"]
    )


def test_http_uses_authenticated_actor_for_every_product_operation(client, created):
    h = created
    saved = h.repository.get_collet(h.collet.id)
    stage_id = saved.current().stages[0].stage_rollout_id
    reads = [
        f"{BASE}/collets/{h.collet.id}/rollouts",
        f"{BASE}/stages/{stage_id}/messages",
    ]
    client.app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        "test", "outsider", "test"
    )
    for path in reads:
        assert client.get(path).status_code == 403
    assert (
        client.post(
            BASE + "/profiles",
            json={
                "name": "forbidden",
                "stages": h.stages,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            BASE + "/collets", json={"profile_id": h.profile.id, "content": "forbidden"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{BASE}/rollouts/{saved.current_rollout_id}/interactive",
            json={"interactive_uuid": "forbidden", "interactive_context": "forbidden"},
        ).status_code
        == 403
    )
    h.interaction.propose.assert_not_awaited()
    h.bot.get_session_messages.assert_not_awaited()
    assert h.repository.get_collet(h.collet.id).revision == 1

    client.app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        "test", "profile-owner", "test"
    )
    for path in reads:
        assert client.get(path).status_code == 200


def test_product_routes_do_not_fall_back_to_an_ambient_actor_without_http_auth(
    client, created
):
    def unauthenticated():
        raise HTTPException(status_code=401, detail="not authenticated")

    client.app.dependency_overrides[get_current_user] = unauthenticated
    assert client.get(f"{BASE}/collets/{created.collet.id}/rollouts").status_code == 401


def test_http_strict_boolean_identity_is_rejected(client):
    response = client.post(
        BASE + "/collets",
        json={"profile_id": True, "content": "request"},
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
        stages="contains confidential content",
    )
    params = capture_call_params(
        inspect.signature(create_profile), (), {"private_body": body}
    )
    assert params["private_body"] == REDACTED
    assert "confidential" not in str(params)


def test_profile_inline_request_is_bounded_before_yaml_compilation(client, runtime):
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


def test_interaction_schema_requires_uuid_before_context_without_revision():
    assert tuple(Interaction.model_fields) == (
        "interactive_uuid",
        "interactive_context",
    )
    assert set(Interaction.model_json_schema()["required"]) == {
        "interactive_uuid",
        "interactive_context",
    }


@pytest.mark.parametrize(
    "body",
    [
        {"interactive_context": "feedback"},
        {"interactive_uuid": "", "interactive_context": "feedback"},
        {"interactive_uuid": True, "interactive_context": "feedback"},
        {"interactive_uuid": "x" * 129, "interactive_context": "feedback"},
        {
            "interactive_uuid": "intervention",
            "interactive_context": "feedback",
            "expected_revision": 1,
        },
        {
            "interactive_uuid": "intervention",
            "interactive_context": "feedback",
            "request_id": "removed",
        },
    ],
)
def test_http_interaction_rejects_missing_uuid_invalid_uuid_and_removed_fields(
    client, created, body
):
    h = created
    before = h.repository.get_collet(h.collet.id)
    response = client.post(
        f"{BASE}/rollouts/{before.current_rollout_id}/interactive", json=body
    )
    assert response.status_code == 422
    assert h.repository.get_collet(h.collet.id).revision == before.revision
    h.interaction.propose.assert_not_awaited()


def test_http_interaction_retries_replay_and_changed_uuid_content_conflicts(
    client, created
):
    h = created
    before = h.repository.get_collet(h.collet.id)
    path = f"{BASE}/rollouts/{before.current_rollout_id}/interactive"
    body = {"interactive_uuid": "http-intervention", "interactive_context": "feedback"}
    first, repeated = client.post(path, json=body), client.post(path, json=body)
    assert first.status_code == repeated.status_code == 200
    assert first.json()["data"] == repeated.json()["data"]
    h.interaction.propose.assert_awaited_once()
    conflict = client.post(path, json={**body, "interactive_context": "changed"})
    assert conflict.status_code == 409
    assert h.repository.get_collet(h.collet.id).revision == before.revision + 1
    assert (
        client.post(
            path, json={**body, "interactive_uuid": "new-intervention"}
        ).status_code
        == 200
    )
    assert h.repository.get_collet(h.collet.id).revision == before.revision + 2


@pytest.mark.parametrize("endpoint_names", ["directed", "legacy", "mixed"])
def test_http_create_rejects_all_caller_composition_shapes(
    client, runtime, endpoint_names
):
    composition = encode_composition(runtime.composition)
    composition["required_methods"].append(
        {
            "type": "MethodTemplate",
            "profile": {"type": "MethodProfile", "method": "Open", "version": "0.2"},
            "policy": None,
        }
    )
    relation = {
        "type": "MethodRelationTemplate",
        "source_method": "Swarm",
        "target_method": "Open",
        "relation": "support",
    }
    if endpoint_names == "legacy":
        relation["parent_method"] = relation.pop("source_method")
        relation["child_method"] = relation.pop("target_method")
    elif endpoint_names == "mixed":
        relation.update(parent_method="Swarm", child_method="Open")
    composition["relations"] = [relation]
    response = client.post(
        BASE + "/profiles",
        json={"name": "composed", "composition": composition, "stages": runtime.stages},
    )
    assert response.status_code == 422, response.text
    assert not runtime.calls


def test_profile_create_schema_accepts_only_name_and_static_stages():
    assert tuple(ProfileCreate.model_fields) == ("name", "stages")
    schema = ProfileCreate.model_json_schema()
    assert schema["required"] == ["name", "stages"]
    assert schema["additionalProperties"] is False

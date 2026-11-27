"""Creation routes retain DI, authenticated actor scope and HTTP guards."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.community.framework.coverage_gate import _walk_apiroutes
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.app import _domain_error_handler
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task.collaboration_registration import (
    mount_collaboration_routers,
)
from agentclaw.community.adapters.http.task.product_body_limits import (
    MAX_PRODUCT_BODY_BYTES,
    ProductBodyLimitRoute,
)
from agentclaw.community.adapters.http.task.product_profiles import (
    create_collet,
    create_profile,
)
from agentclaw.community.api.task.product_profile_service import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.errors import DomainError, Forbidden
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
    actor_context_scope,
    current_actor_context,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    default_composition,
)

BASE = "/api/v1/collaboration/task/context_collab"
LEGACY = "/api/v1/collaboration/task/products"
STAGES = """stages:
  - name: build
    candidates: [{type: bot, uuid: worker}]
    depends_on: []
    acceptance: {condition: tests pass}
"""


@pytest.fixture
def delivery():
    service = AsyncMock(spec=ProductProfileServiceProtocol)
    actors = []

    async def profile(name, stages):
        actors.append(current_actor_context())
        return ProductProfile(7, name, default_composition(), stages)

    async def collet(profile_id, content):
        actors.append(current_actor_context())
        return Collet(11, profile_id, content, StatusEnum.CREATE)

    service.create_product_profile.side_effect = profile
    service.create_product_collet.side_effect = collet
    injector = Injector(auto_bind=False)
    injector.binder.bind(ProductProfileServiceProtocol, to=service)
    app = FastAPI()
    app.add_exception_handler(DomainError, _domain_error_handler)
    mount_collaboration_routers(app)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        "provider-id", "authenticated-staff", "test"
    )
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, service=service, actors=actors)


@pytest.mark.parametrize("path", [BASE + "/product_profiles", LEGACY + "/profiles"])
def test_create_profile_delegates_to_service(delivery, path):
    response = delivery.client.post(path, json={"name": "coding", "stages": STAGES})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["id"], data["name"], data["stages"]) == (7, "coding", STAGES)
    assert data["composition"]["relations"][0] == {
        "type": "MethodRelationTemplate",
        "source_method": "MultiRollout",
        "target_method": "Swarm",
        "relation": "nest",
    }
    delivery.service.create_product_profile.assert_awaited_once_with("coding", STAGES)
    delivery.service.create_product_collet.assert_not_awaited()
    assert delivery.actors == [
        ActorContext(PrincipalRef("human", "authenticated-staff"), "api")
    ]


def test_creation_entry_logs_route_and_size_without_payload(delivery, caplog):
    stage_marker = "private-stage-marker"
    content_marker = "private-content-marker"
    logger_name = "start"

    with caplog.at_level(logging.INFO, logger=logger_name):
        delivery.client.post(
            BASE + "/product_profiles",
            json={
                "name": "coding",
                "stages": STAGES.replace("tests pass", stage_marker),
            },
        )
        profile_entry = next(
            record.getMessage()
            for record in caplog.records
            if "product_profile.http.create.start" in record.getMessage()
        )
        assert stage_marker not in caplog.text
        assert "stage_bytes=" in profile_entry

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=logger_name):
        delivery.client.post(
            BASE + "/collets",
            json={"profile_id": 7, "content": content_marker},
        )
        collet_entry = next(
            record.getMessage()
            for record in caplog.records
            if "product_collet.http.create.start" in record.getMessage()
        )
        assert content_marker not in caplog.text
        assert "profile_id=7" in collet_entry
        assert "content_bytes=" in collet_entry


@pytest.mark.parametrize("path", [BASE + "/collets", LEGACY + "/collets"])
def test_create_collet_delegates_with_authenticated_actor(delivery, path):
    ambient = ActorContext(PrincipalRef("human", "not-the-http-caller"), "test")
    with actor_context_scope(ambient):
        response = delivery.client.post(
            path, json={"profile_id": 7, "content": "implement the feature"}
        )
        assert current_actor_context() == ambient
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "id": 11,
        "profile_id": 7,
        "content": "implement the feature",
        "status": "CREATE",
    }
    delivery.service.create_product_collet.assert_awaited_once_with(
        7, "implement the feature"
    )
    delivery.service.create_product_profile.assert_not_awaited()
    assert delivery.actors == [
        ActorContext(PrincipalRef("human", "authenticated-staff"), "api")
    ]


@pytest.mark.parametrize(
    "path,body",
    [
        ("/product_profiles", {"name": "coding", "stages": STAGES}),
        ("/collets", {"profile_id": 7, "content": "request"}),
    ],
)
def test_creation_requires_http_auth_even_with_ambient_actor(delivery, path, body):
    def unauthenticated():
        raise HTTPException(401, "not authenticated")

    delivery.client.app.dependency_overrides[get_current_user] = unauthenticated
    with actor_context_scope(ActorContext(PrincipalRef("human", "ambient"), "test")):
        assert delivery.client.post(BASE + path, json=body).status_code == 401
    delivery.service.create_product_profile.assert_not_awaited()
    delivery.service.create_product_collet.assert_not_awaited()


@pytest.mark.parametrize(
    "path,body",
    [
        ("/product_profiles", {"stages": STAGES}),
        ("/product_profiles", {"name": "coding"}),
        ("/product_profiles", {"name": "", "stages": STAGES}),
        ("/product_profiles", {"name": "coding", "stages": {"stages": []}}),
        ("/product_profiles", {"name": "coding", "stages": STAGES, "composition": {}}),
        ("/product_profiles", {"name": "coding", "stages": STAGES, "creator": "spoof"}),
        ("/collets", {"profile_id": True, "content": "request"}),
        ("/collets", {"profile_id": "7", "content": "request"}),
        ("/collets", {"profile_id": 0, "content": "request"}),
        ("/collets", {"profile_id": 7, "content": ""}),
        ("/collets", {"content": "request"}),
        ("/collets", {"profile_id": 7, "content": "request", "context": {}}),
        ("/collets", {"profile_id": 7, "content": "request", "request_id": "removed"}),
    ],
)
def test_invalid_bodies_do_not_call_service(delivery, path, body):
    assert delivery.client.post(BASE + path, json=body).status_code == 422
    delivery.service.create_product_profile.assert_not_awaited()
    delivery.service.create_product_collet.assert_not_awaited()


@pytest.mark.parametrize(
    "error,status",
    [
        (ContextCollabNotFoundError("profile missing"), 404),
        (ContextCollabValidationError("invalid workflow"), 422),
        (ContextCollabVersionConflict("conflict"), 409),
        (Forbidden("not authorized"), 403),
    ],
)
@pytest.mark.parametrize("kind", ["product_profiles", "collets"])
def test_service_failures_are_not_reported_as_success(delivery, kind, error, status):
    if kind == "product_profiles":
        method = delivery.service.create_product_profile
        body = {"name": "coding", "stages": STAGES}
    else:
        method = delivery.service.create_product_collet
        body = {"profile_id": 7, "content": "request"}
    method.side_effect = error
    response = delivery.client.post(f"{BASE}/{kind}", json=body)
    assert response.status_code == status, response.text
    if isinstance(error, Forbidden):
        assert response.json() == {"detail": "not authorized"}
    else:
        assert response.json()["data"] is None
    method.assert_awaited_once()


@pytest.mark.parametrize("kind", ["product_profiles", "collets"])
def test_request_is_bounded_before_json_parsing(delivery, kind):
    response = delivery.client.post(
        f"{BASE}/{kind}",
        content=b"x" * (MAX_PRODUCT_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    delivery.service.create_product_profile.assert_not_awaited()
    delivery.service.create_product_collet.assert_not_awaited()


def test_registration_shares_guarded_handlers_and_keeps_existing_routes(delivery):
    app = delivery.client.app
    for path, handler in [
        (BASE + "/product_profiles", create_profile),
        (BASE + "/collets", create_collet),
    ]:
        routes = [
            r for full_path, r in _walk_apiroutes(app.routes) if full_path == path
        ]
        assert len(routes) == 1
        assert routes[0].endpoint is handler
        assert isinstance(routes[0], ProductBodyLimitRoute)
    paths = app.openapi()["paths"]
    assert "post" in paths[BASE]
    assert "get" in paths[BASE + "/{collab_context_id}"]
    assert "post" in paths[BASE + "/{collab_context_id}/events"]
    assert "post" in paths[LEGACY + "/profiles"]
    assert "post" in paths[LEGACY + "/collets"]
    schemas = app.openapi()["components"]["schemas"]
    assert schemas["ProfileCreate"]["required"] == ["name", "stages"]
    assert schemas["ColletCreate"]["required"] == ["profile_id", "content"]
    assert schemas["ProfileCreate"]["additionalProperties"] is False
    assert schemas["ColletCreate"]["additionalProperties"] is False

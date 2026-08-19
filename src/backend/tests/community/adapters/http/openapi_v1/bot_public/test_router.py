"""HTTP contract for the public Bot catalog adapter.

These tests exercise the assembled public API with injected service doubles. A
production change that drops the explicit projection, caller scope, validation,
or admission declaration must make at least one assertion below fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.gateway_principal.models import (
    AppPrincipal,
    GatewayApp,
    GatewayUser,
    UserPrincipal,
)
from agentclaw.community.core.gateway_principal.verifier import VerifiedCaller
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)


def _bot(*, friendship: dict[str, Any] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": 91,
        "bot_id": "catalog-bot",
        "entity_id": "owner-1",
        "bot_type": "service",
        "bot_name": "Catalog Bot",
        "bot_desc": "A public catalog entry",
        "owner_name": "Owner",
        "active_engine": "openclaw",
        "status": "ACTIVE",
        "binding_id": 77,
        "device_id": "device-secret",
        "ext": {"iam_token": "secret", "passport": {"token": "secret"}},
        "env": "pre",
        "instance_selector": "instance-secret",
    }
    if friendship is not None:
        record["friend_record_approval"] = friendship
    return record


@dataclass
class _PublicService:
    result: dict[str, Any] = field(
        default_factory=lambda: {
            "total": 1,
            "items": [
                _bot(
                    friendship={
                        "status": "PENDING",
                        "ext": {"approvals": [{"require_approval": True}]},
                        "internal_id": 12,
                    }
                )
            ],
        }
    )
    calls: list[dict[str, Any]] = field(default_factory=list)

    def search_public_bots_by_keyword(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.result


@dataclass
class _DiscoverService:
    result: dict[str, Any] = field(
        default_factory=lambda: {
            "total": 1,
            "items": [
                {
                    **_bot(),
                    "recommend": {
                        "score": 0.92,
                        "reasons": ["matches the requested capability"],
                        "short_profile": "Short public profile",
                        "profile_key": "private-profile-key",
                    },
                }
            ],
            "context": {"recommend_response": {"private": "payload"}},
        }
    )
    calls: list[dict[str, Any]] = field(default_factory=list)

    def search_by_keyword(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def services() -> tuple[_PublicService, _DiscoverService]:
    return _PublicService(), _DiscoverService()


@pytest.fixture
def app(services: tuple[_PublicService, _DiscoverService]) -> FastAPI:
    public_service, discover_service = services

    class _Bindings(Module):
        def configure(self, binder) -> None:
            binder.bind(BotPublicServiceProtocol, to=public_service)
            binder.bind(BotDiscoverServiceProtocol, to=discover_service)

    app = FastAPI()
    app.include_router(build_public_router())
    app.dependency_overrides[require_principal] = lambda: {"user_id": "caller-1"}
    attach_injector(app, Injector([_Bindings()]))
    mount_public_error_handlers(app)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return user_scoped_client(app, "caller-1", raise_server_exceptions=False)


def test_search_projects_only_public_fields_and_uses_verified_caller(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    response = client.get(
        "/openapi/v1/bots/public/search",
        params={"search": "catalog", "page": 2, "page_size": 5},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 200000
    assert body["data"] == {
        "total": 1,
        "items": [
            {
                "bot_id": "catalog-bot",
                "entity_id": "owner-1",
                "bot_type": "service",
                "name": "Catalog Bot",
                "description": "A public catalog entry",
                "owner_name": "Owner",
                "engine": "openclaw",
                "status": "ACTIVE",
                "friendship": {"status": "PENDING", "requires_approval": True},
            }
        ],
    }
    rendered = str(body["data"])
    for forbidden in ("binding_id", "device_id", "iam_token", "instance_selector", "internal_id"):
        assert forbidden not in rendered
    assert services[0].calls == [
        {"user_id": "caller-1", "search": "catalog", "page": 2, "page_size": 5}
    ]


def test_discover_translates_runtime_state_only_at_service_boundary(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    response = client.get(
        "/openapi/v1/bots/public/discover",
        params={"keyword": "automation", "top_k": 3, "min_score": 0.5, "runtime_state": "verify"},
    )

    assert response.status_code == 200, response.text
    item = response.json()["data"]["items"][0]
    assert item == {
        "bot_id": "catalog-bot",
        "entity_id": "owner-1",
        "bot_type": "service",
        "name": "Catalog Bot",
        "description": "A public catalog entry",
        "owner_name": "Owner",
        "engine": "openclaw",
        "status": "ACTIVE",
        "recommendation": {
            "score": 0.92,
            "reasons": ["matches the requested capability"],
            "short_profile": "Short public profile",
        },
    }
    assert services[1].calls == [
        {
            "keyword": "automation",
            "user_id": "caller-1",
            "top_k": 3,
            "min_score": 0.5,
            "filters": {"runtime_state": ["verify"]},
        }
    ]


def test_discover_uses_online_filter_by_default(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    response = client.get("/openapi/v1/bots/public/discover", params={"keyword": "automation"})

    assert response.status_code == 200, response.text
    assert services[1].calls[0]["filters"] == {"runtime_state": ["online"]}


def test_discover_rejects_a_record_without_an_authoritative_bot_type(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    """A malformed discovery record must not be guessed as a personal Bot."""
    record = dict(services[1].result["items"][0])
    record.pop("bot_type")
    services[1].result["items"] = [record]

    response = client.get("/openapi/v1/bots/public/discover", params={"keyword": "automation"})

    assert response.status_code == 502, response.text
    assert response.json()["message"] == "Recommendation service unavailable"


@pytest.mark.parametrize("path", [
    "/openapi/v1/bots/public/search?page=0",
    "/openapi/v1/bots/public/search?page_size=101",
    "/openapi/v1/bots/public/discover?keyword=",
    "/openapi/v1/bots/public/discover?keyword=x&top_k=21",
    "/openapi/v1/bots/public/discover?keyword=x&min_score=1.1",
    "/openapi/v1/bots/public/discover?keyword=x&runtime_state=offline",
])
def test_catalog_rejects_invalid_queries(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == 422000


@pytest.mark.parametrize("path", [
    "/openapi/v1/bots/public/search",
    "/openapi/v1/bots/public/discover?keyword=automation",
])
def test_catalog_refuses_a_forged_user_id(client: TestClient, path: str) -> None:
    response = client.get(path, params={"user_id": "other-user"})

    assert response.status_code == 403, response.text
    assert response.json()["code"] == 403001
    assert response.json()["message"] == "Forbidden"


@pytest.mark.parametrize("path", [
    "/openapi/v1/bots/public/search",
    "/openapi/v1/bots/public/discover?keyword=automation",
])
def test_catalog_allows_a_user_plus_app_principal(
    app: FastAPI, services: tuple[_PublicService, _DiscoverService], path: str
) -> None:
    app.dependency_overrides[require_principal] = lambda: VerifiedCaller(
        principals=(
            UserPrincipal(subject=GatewayUser(id="caller-1", username="caller")),
            AppPrincipal(
                tenant="test",
                app=GatewayApp(app_id=1, app_name="partner", owners="team", tenant="test"),
            ),
        )
    )

    response = user_scoped_client(app, "caller-1").get(path)

    assert response.status_code == 200, response.text
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("path", [
    "/openapi/v1/bots/public/search",
    "/openapi/v1/bots/public/discover?keyword=automation",
])
def test_catalog_refuses_a_pure_application_principal(app: FastAPI, path: str) -> None:
    app.dependency_overrides[require_principal] = lambda: VerifiedCaller(
        principals=(
            AppPrincipal(
                tenant="test",
                app=GatewayApp(app_id=1, app_name="partner", owners="team", tenant="test"),
            ),
        )
    )

    response = user_scoped_client(app, "caller-1", raise_server_exceptions=False).get(path)

    assert response.status_code == 401, response.text
    assert response.json()["code"] == 401001


def test_discover_returns_fixed_502_when_recommender_is_unavailable(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    services[1].result = {
        "total": 0,
        "items": [],
        "context": {"recommend_response": None, "internal": "do not disclose"},
    }

    response = client.get("/openapi/v1/bots/public/discover", params={"keyword": "automation"})

    assert response.status_code == 502, response.text
    assert response.json()["code"] == 502000
    assert response.json()["message"] == "Recommendation service unavailable"
    assert response.json()["data"] is None

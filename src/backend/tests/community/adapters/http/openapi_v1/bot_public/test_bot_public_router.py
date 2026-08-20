"""HTTP contract for the public Bot catalog adapter."""

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
from agentclaw.community.core.gateway_principal.models import AppPrincipal, GatewayApp
from agentclaw.community.core.gateway_principal.verifier import VerifiedCaller
from tests.community.adapters.http.openapi_v1.conftest import mount_public_error_handlers

_SEARCH_PATH = "/openapi/v1/bots/catalog/search"
_DISCOVER_PATH = "/openapi/v1/bots/catalog/discover"


def _bot() -> dict[str, Any]:
    return {
        "id": 91,
        "bot_id": "catalog-bot",
        "entity_id": "owner-1",
        "bot_type": "service",
        "bot_name": "Catalog Bot",
        "bot_desc": "A public catalog entry",
        "owner_name": "Owner",
        "active_engine": "openclaw",
        "status": "ACTIVE",
        "friend_record_approval": {
            "status": "PENDING",
            "ext": {"approvals": [{"require_approval": True}]},
        },
        "binding_id": 77,
        "device_id": "device-secret",
        "ext": {"iam_token": "secret", "passport": {"token": "secret"}},
        "env": "pre",
        "instance_selector": "instance-secret",
    }


@dataclass
class _PublicService:
    result: dict[str, Any] = field(
        default_factory=lambda: {"total": 1, "items": [_bot()]}
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
    return TestClient(app, raise_server_exceptions=False)


def test_search_projects_only_catalog_fields(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    response = client.get(
        _SEARCH_PATH,
        params={"search": "catalog", "page": 2, "page_size": 5},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
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
            }
        ],
    }
    rendered = str(response.json()["data"])
    for forbidden in (
        "friendship",
        "binding_id",
        "device_id",
        "iam_token",
        "instance_selector",
    ):
        assert forbidden not in rendered
    assert services[0].calls == [
        {"search": "catalog", "page": 2, "page_size": 5}
    ]


def test_discover_uses_online_filter_by_default(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    response = client.get(_DISCOVER_PATH, params={"keyword": "automation"})

    assert response.status_code == 200, response.text
    assert services[1].calls == [
        {
            "keyword": "automation",
            "top_k": 10,
            "min_score": 0.1,
            "filters": {"runtime_state": ["online"]},
        }
    ]
    item = response.json()["data"]["items"][0]
    assert "friendship" not in item
    assert item["recommendation"]["score"] == 0.92



@pytest.mark.parametrize("path", [_SEARCH_PATH, f"{_DISCOVER_PATH}?keyword=automation"])
def test_catalog_allows_a_pure_application_principal(
    app: FastAPI, path: str
) -> None:
    app.dependency_overrides[require_principal] = lambda: VerifiedCaller(
        principals=(
            AppPrincipal(
                tenant="test",
                app=GatewayApp(
                    app_id=1,
                    app_name="partner",
                    owners="team",
                    tenant="test",
                ),
            ),
        )
    )

    response = TestClient(app, raise_server_exceptions=False).get(path)

    assert response.status_code == 200, response.text


def test_discover_returns_fixed_502_when_recommender_is_unavailable(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    services[1].result = {
        "total": 0,
        "items": [],
        "context": {"recommend_response": None, "internal": "do not disclose"},
    }

    response = client.get(_DISCOVER_PATH, params={"keyword": "automation"})

    assert response.status_code == 502, response.text
    assert response.json()["code"] == 502000
    assert response.json()["data"] is None

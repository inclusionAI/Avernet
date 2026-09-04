"""HTTP contract for the public Bot catalog adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogCaller,
    BotCatalogSearchFilters,
    BotCatalogSearchUnavailableError,
)
from agentclaw.community.core.gateway_principal.models import (
    AppPrincipal,
    GatewayApp,
    GatewayUser,
    UserPrincipal,
)
from agentclaw.community.core.gateway_principal.verifier import VerifiedCaller
from tests.community.adapters.http.openapi_v1.conftest import mount_public_error_handlers, public_router

_SEARCH_PATH = "/openapi/v1/bots/catalog/search"
_DISCOVER_PATH = "/openapi/v1/bots/catalog/discover"


def _bot() -> dict[str, Any]:
    return {
        "id": 91,
        "bot_id": "catalog-bot",
        "bot_uuid": "catalog-bot:owner-1",
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

    def search_catalog_public_bots_by_keyword(self, **kwargs: Any) -> dict[str, Any]:
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
    app.include_router(public_router())
    app.dependency_overrides[require_principal] = lambda: VerifiedCaller(
        principals=(
            UserPrincipal(subject=GatewayUser(id="caller-1", username="caller-1")),
        )
    )
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
                "bot_uuid": "catalog-bot:owner-1",
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
        {
            "search": "catalog",
            "page": 2,
            "page_size": 5,
            "filters": BotCatalogSearchFilters(),
            "caller": BotCatalogCaller(
                tenant_id="teamclaw", user_id="caller-1", app_id=None
            ),
            "request_id": "",
        }
    ]


def test_search_selectively_projects_frontend_bcs_filters(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    response = client.get(
        _SEARCH_PATH,
        params=[
            ("visibility", "public,protected"),
            ("visibility", "private"),
            ("user_visibility", "protected"),
            ("user_visibility", "public,protected"),
            ("status", "online"),
            ("viewer_actor_type", "human"),
            ("viewer_actor_id", "caller-1"),
            ("friendship", "friends"),
        ],
    )

    assert response.status_code == 200, response.text
    assert services[0].calls[0]["filters"] == BotCatalogSearchFilters(
        visibility=("public", "protected", "private"),
        user_visibility=("protected", "public"),
        status="online",
        viewer_actor_type="human",
        viewer_actor_id="caller-1",
        friendship="friends",
    )


def test_search_all_friendship_does_not_require_a_viewer(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    response = client.get(_SEARCH_PATH, params={"friendship": "all"})

    assert response.status_code == 200, response.text
    assert services[0].calls[0]["filters"] == BotCatalogSearchFilters(
        friendship="all"
    )


@pytest.mark.parametrize(
    "params",
    [
        {"viewer_actor_type": "human"},
        {"viewer_actor_id": "caller-1"},
        {"friendship": "friends"},
        {"visibility": "public,unknown"},
        {"status": "busy"},
    ],
)
def test_search_rejects_invalid_bcs_filter_combinations(
    client: TestClient,
    services: tuple[_PublicService, _DiscoverService],
    params: dict[str, str],
) -> None:
    response = client.get(_SEARCH_PATH, params=params)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == 422000
    assert services[0].calls == []


def test_search_projects_bcs_is_friend_when_present(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    services[0].result["items"][0]["is_friend"] = False

    response = client.get(_SEARCH_PATH)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["items"][0]["is_friend"] is False


def test_search_projects_requested_bcs_metadata_when_present(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    friend_ext = {
        "public_user_approval": {
            "status": "PROCESSING",
            "view_friend_deps": [{"deptNo": "D1"}],
        }
    }
    services[0].result["items"][0].update(
        {
            "visibility": "protected",
            "is_online": False,
            "actor_kind": "bot",
            "is_friend": False,
            "friend_ext": friend_ext,
            "friend_check_in_strategy": {},
            "user_visibility": "private",
            "unexpected_bcs_field": "must-not-be-public",
        }
    )

    response = client.get(_SEARCH_PATH)

    assert response.status_code == 200, response.text
    item = response.json()["data"]["items"][0]
    assert item["visibility"] == "protected"
    assert item["is_online"] is False
    assert item["actor_kind"] == "bot"
    assert item["is_friend"] is False
    assert item["friend_ext"] == friend_ext
    assert item["friend_check_in_strategy"] == {}
    assert item["user_visibility"] == "private"
    assert "unexpected_bcs_field" not in item


def test_search_omits_is_friend_when_bcs_did_not_return_it(
    client: TestClient,
) -> None:
    response = client.get(_SEARCH_PATH)

    assert response.status_code == 200, response.text
    assert "is_friend" not in response.json()["data"]["items"][0]


def test_search_openapi_declares_the_fixed_catalog_unavailable_envelope(
    app: FastAPI,
) -> None:
    """Catches generated clients losing the fixed 502 catalog error contract."""
    response = app.openapi()["paths"][_SEARCH_PATH]["get"]["responses"]["502"]

    assert response["description"] == "Catalog service unavailable"
    content = response["content"]["application/json"]
    assert content["schema"] == {"$ref": "#/components/schemas/ErrorEnvelope"}
    assert content["example"] == {
        "code": 502000,
        "message": "Catalog service unavailable",
        "request_id": "b0a6d2f4e8c94b1a9f3d5e7c60218a4d",
    }


def test_search_openapi_declares_optional_bcs_filter_parameters(app: FastAPI) -> None:
    parameters = {
        parameter["name"]: parameter
        for parameter in app.openapi()["paths"][_SEARCH_PATH]["get"]["parameters"]
    }

    assert {
        "visibility",
        "user_visibility",
        "status",
        "viewer_actor_type",
        "viewer_actor_id",
        "friendship",
    } <= parameters.keys()
    assert parameters["visibility"]["required"] is False
    assert parameters["user_visibility"]["required"] is False
    assert "comma-separate" in parameters["visibility"]["description"]
    assert "requires viewer_actor_type" in parameters["viewer_actor_id"][
        "description"
    ]


def test_search_openapi_declares_optional_bcs_is_friend(app: FastAPI) -> None:
    is_friend = app.openapi()["components"]["schemas"]["PublicBot"]["properties"][
        "is_friend"
    ]

    assert is_friend["anyOf"] == [{"type": "boolean"}, {"type": "null"}]


def test_search_openapi_declares_optional_bcs_preferred_bot_uuid(
    app: FastAPI,
) -> None:
    bot_uuid = app.openapi()["components"]["schemas"]["PublicBot"]["properties"][
        "bot_uuid"
    ]

    assert bot_uuid["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert bot_uuid["description"] == (
        "Catalog Search Bot UUID, preferring BCS with a Backend address fallback."
    )


def test_search_openapi_declares_optional_bcs_metadata_fields(app: FastAPI) -> None:
    properties = app.openapi()["components"]["schemas"]["PublicBot"]["properties"]

    assert properties["visibility"]["description"] == (
        "BCS visibility returned by Catalog Search when available."
    )
    assert properties["is_online"]["description"] == (
        "BCS online state returned by Catalog Search when available."
    )
    assert properties["actor_kind"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert properties["friend_ext"]["description"] == (
        "BCS friend extension returned by Catalog Search when available."
    )
    assert properties["friend_check_in_strategy"]["description"] == (
        "BCS friend check-in strategy returned by Catalog Search when available."
    )
    assert properties["user_visibility"]["description"] == (
        "BCS user visibility returned by Catalog Search when available."
    )


@pytest.mark.parametrize(
    ("principal", "expected_caller"),
    [
        (
            VerifiedCaller(
                principals=(
                    UserPrincipal(
                        subject=GatewayUser(id="user-1", username="user-1")
                    ),
                )
            ),
            BotCatalogCaller(tenant_id="teamclaw", user_id="user-1", app_id=None),
        ),
        (
            VerifiedCaller(
                principals=(
                    AppPrincipal(
                        tenant="tenant-2",
                        app=GatewayApp(
                            app_id=2,
                            app_name="partner",
                            owners="team",
                            tenant="tenant-2",
                        ),
                    ),
                )
            ),
            BotCatalogCaller(tenant_id="tenant-2", user_id=None, app_id=2),
        ),
        (
            VerifiedCaller(
                principals=(
                    UserPrincipal(
                        subject=GatewayUser(id="user-3", username="user-3")
                    ),
                    AppPrincipal(
                        tenant="tenant-3",
                        app=GatewayApp(
                            app_id=3,
                            app_name="partner",
                            owners="team",
                            tenant="tenant-3",
                        ),
                    ),
                )
            ),
            BotCatalogCaller(tenant_id="tenant-3", user_id="user-3", app_id=3),
        ),
    ],
)
def test_search_projects_the_verified_principal_to_catalog_caller(
    app: FastAPI,
    services: tuple[_PublicService, _DiscoverService],
    principal: VerifiedCaller,
    expected_caller: BotCatalogCaller,
) -> None:
    app.dependency_overrides[require_principal] = lambda: principal

    response = TestClient(app, raise_server_exceptions=False).get(_SEARCH_PATH)

    assert response.status_code == 200, response.text
    assert services[0].calls[-1]["caller"] == expected_caller


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
            "catalog_filters": BotCatalogSearchFilters(status="online"),
            "caller": BotCatalogCaller(
                tenant_id="teamclaw", user_id="caller-1", app_id=None
            ),
            "request_id": "",
        }
    ]
    item = response.json()["data"]["items"][0]
    assert "friendship" not in item
    assert item["recommendation"]["score"] == 0.92


def test_discover_forwards_verified_viewer_to_bcs_catalog(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    response = client.get(
        _DISCOVER_PATH,
        params={
            "keyword": "automation",
            "viewer_actor_type": "human",
            "viewer_actor_id": "caller-1",
        },
    )

    assert response.status_code == 200, response.text
    call = services[1].calls[0]
    assert call["catalog_filters"] == BotCatalogSearchFilters(
        status="online",
        viewer_actor_type="human",
        viewer_actor_id="caller-1",
    )
    assert call["caller"] == BotCatalogCaller(
        tenant_id="teamclaw", user_id="caller-1", app_id=None
    )
    assert call["request_id"] == ""


def test_discover_keeps_non_bcs_runtime_state_in_bcsfuse_only(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    response = client.get(
        _DISCOVER_PATH,
        params={"keyword": "automation", "runtime_state": "verify"},
    )

    assert response.status_code == 200, response.text
    call = services[1].calls[0]
    assert call["filters"] == {"runtime_state": ["verify"]}
    assert call["catalog_filters"] == BotCatalogSearchFilters()


def test_discover_preserves_allowlisted_legacy_json_values(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    services[1].result["items"][0].update(
        {
            "bot_type": {"legacy": "service"},
            "owner_name": {"display": "Owner"},
            "recommend": {
                "score": 0.92,
                "reasons": ["matches capability", {"legacy": "reason"}],
                "short_profile": {"summary": "Short public profile"},
                "profile_key": "private-profile-key",
            },
        }
    )

    response = client.get(_DISCOVER_PATH, params={"keyword": "automation"})

    assert response.status_code == 200, response.text
    item = response.json()["data"]["items"][0]
    assert item["bot_type"] == {"legacy": "service"}
    assert item["owner_name"] == {"display": "Owner"}
    assert item["recommendation"] == {
        "score": 0.92,
        "reasons": ["matches capability", {"legacy": "reason"}],
        "short_profile": {"summary": "Short public profile"},
    }
    rendered = str(item)
    for forbidden in (
        "binding_id",
        "device_id",
        "iam_token",
        "instance_selector",
        "profile_key",
    ):
        assert forbidden not in rendered


def test_discover_openapi_allows_legacy_json_values(app: FastAPI) -> None:
    schemas = app.openapi()["components"]["schemas"]
    public_bot = schemas["PublicBot"]["properties"]
    recommendation = schemas["Recommendation"]["properties"]

    assert "enum" not in public_bot["bot_type"]
    assert "type" not in public_bot["bot_type"]
    assert "type" not in public_bot["owner_name"]
    assert "type" not in recommendation["reasons"]
    assert "type" not in recommendation["short_profile"]



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


def test_search_returns_fixed_502_when_bcs_catalog_is_unavailable(
    client: TestClient, services: tuple[_PublicService, _DiscoverService]
) -> None:
    def _unavailable(**_kwargs: Any) -> dict[str, Any]:
        raise BotCatalogSearchUnavailableError("upstream unavailable")

    services[0].search_catalog_public_bots_by_keyword = _unavailable

    response = client.get(_SEARCH_PATH, params={"search": "catalog"})

    assert response.status_code == 502, response.text
    assert response.json()["code"] == 502000
    assert response.json()["data"] is None

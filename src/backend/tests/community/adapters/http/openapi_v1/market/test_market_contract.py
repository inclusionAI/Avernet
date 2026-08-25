from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.market import router
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.skill_market_service import (
    SkillMarketSearchResult,
    SkillMarketServiceProtocol,
)
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
    SkillCenterMarketSearchResult,
)


class _SkillMarket:
    def __init__(self) -> None:
        self.query = None

    def search(self, query):
        self.query = query
        return SkillMarketSearchResult(
            total=1,
            items=(
                {
                    "id": 7,
                    "name": "Calendar",
                    "description": "Manage calendars",
                    "git_path": "git://official/calendar",
                },
            ),
        )


class _McpMarket:
    def __init__(self) -> None:
        self.kwargs = None

    def get_mcp_list(self, **kwargs):
        self.kwargs = kwargs
        return {
            "success": True,
            "total": 1,
            "data": [
                {
                    "serverCode": "mcp.calendar",
                    "name": "Calendar MCP",
                    "category": "office",
                    "tags": ["calendar", "productivity"],
                    "networkTypes": ["INTERNET"],
                    "transportProtocol": "STREAMABLE_HTTP",
                    "endpoints": [
                        {
                            "networkType": "INTERNET",
                            "transportProtocol": "STREAMABLE_HTTP",
                            "headers": {"X-Custom-Header": "opaque"},
                        }
                    ],
                    "tools": [
                        {
                            "name": "create_event",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "extInfo": {"internal": True},
                                },
                            },
                        }
                    ],
                    "futureCatalogueField": {"nestedValue": "retained"},
                }
            ],
        }


class _SkillCenter:
    def __init__(self) -> None:
        self.request = None

    def search_market_skills(self, request):
        self.request = request
        return SkillCenterMarketSearchResult(
            total=1,
            items=(
                {
                    "skillCode": "sc-calendar",
                    "skillName": "SC Calendar",
                    "accessLevel": "PUBLIC",
                },
            ),
        )

    def get_market_tags(self):
        return [
            {
                "id": 1,
                "name": "研发效能",
                "description": None,
                "iconUrl": None,
                "parentId": None,
                "tagLevel": 1,
                "children": [
                    {
                        "id": 2,
                        "name": "代码质量",
                        "parentId": 1,
                        "tagLevel": 2,
                        "children": [],
                    }
                ],
            }
        ]


class _Bindings(Module):
    def __init__(self, skill, mcp, sc) -> None:
        self.skill = skill
        self.mcp = mcp
        self.sc = sc

    def configure(self, binder) -> None:
        binder.bind(SkillMarketServiceProtocol, to=self.skill)
        binder.bind(MCPMarketServiceProtocol, to=self.mcp)
        binder.bind(SkillCenterClient, to=self.sc)


def _client():
    skill = _SkillMarket()
    mcp = _McpMarket()
    sc = _SkillCenter()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "user-1"}
    attach_injector(app, Injector([_Bindings(skill, mcp, sc)]))
    return TestClient(app), skill, mcp, sc


def test_builtin_skill_market_maps_body_and_envelopes_page():
    client, skill, _, _ = _client()

    response = client.post(
        "/openapi/v1/bots/market/skills",
        params={"user_id": "user-1"},
        json={"keyword": "calendar", "page_num": 2, "page_size": 5},
    )

    assert response.status_code == 200
    assert skill.query.keyword == "calendar"
    assert skill.query.page_num == 2
    assert skill.query.page_size == 5
    assert response.json()["data"] == {
        "total": 1,
        "items": [
            {
                "id": 7,
                "skill_uuid": None,
                "name": "Calendar",
                "description": "Manage calendars",
                "category": None,
                "tags": None,
                "git_path": "git://official/calendar",
            }
        ],
    }


def test_mcp_market_reuses_market_service_mapping():
    client, _, mcp, _ = _client()

    response = client.post(
        "/openapi/v1/bots/market/mcp-servers",
        params={"user_id": "user-1"},
        json={"keyword": "calendar", "page_num": 3, "page_size": 10},
    )

    assert response.status_code == 200
    assert mcp.kwargs["page_num"] == 3
    assert mcp.kwargs["page_size"] == 10
    assert mcp.kwargs["search_key"] == "calendar"
    item = response.json()["data"]["items"][0]
    assert item["server_code"] == "mcp.calendar"
    assert item["category"] == "office"
    assert item["tags"] == ["calendar", "productivity"]
    assert item["endpoints"][0]["headers"] == {"X-Custom-Header": "opaque"}
    assert item["tools"][0]["inputSchema"]["properties"] == {
        "title": {"type": "string"}
    }
    assert item["future_catalogue_field"] == {"nested_value": "retained"}


def test_mcp_market_forwards_legacy_filters_including_tags():
    client, _, mcp, _ = _client()

    response = client.post(
        "/openapi/v1/bots/market/mcp-servers",
        params={"user_id": "user-1"},
        json={
            "keyword": "calendar",
            "page_num": 2,
            "page_size": 5,
            "server_codes": ["mcp.calendar"],
            "platform_server_codes": ["platform.calendar"],
            "run_modes": ["REMOTE"],
            "statuses": ["ONLINE"],
            "transport_protocols": ["STREAMABLE_HTTP"],
            "host_platforms": ["serverless"],
            "owners": ["10001"],
            "network_types": ["INTERNET", "INTRANET"],
            "categories": ["office"],
            "tenants": ["default"],
            "tags": ["calendar", "productivity"],
        },
    )

    assert response.status_code == 200
    assert mcp.kwargs == {
        "page_num": 2,
        "page_size": 5,
        "search_key": "calendar",
        "server_codes": ["mcp.calendar"],
        "platform_server_codes": ["platform.calendar"],
        "run_modes": ["REMOTE"],
        "statuses": ["ONLINE"],
        "transport_protocols": ["STREAMABLE_HTTP"],
        "host_platforms": ["serverless"],
        "owners": ["10001"],
        "network_types": ["INTERNET"],
        "categories": ["office"],
        "tenants": ["default"],
        "tags": ["calendar", "productivity"],
    }

    request_schema = client.app.openapi()["components"]["schemas"][
        "McpMarketSearchRequest"
    ]
    assert {
        "server_codes",
        "platform_server_codes",
        "run_modes",
        "statuses",
        "transport_protocols",
        "host_platforms",
        "owners",
        "network_types",
        "categories",
        "tenants",
        "tags",
    } <= request_schema["properties"].keys()


def test_mcp_market_returns_empty_page_when_requested_network_is_not_visible():
    client, _, mcp, _ = _client()

    response = client.post(
        "/openapi/v1/bots/market/mcp-servers",
        params={"user_id": "user-1"},
        json={"network_types": ["INTRANET"]},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"total": 0, "items": []}
    assert mcp.kwargs is None


def test_skill_center_market_forces_public_scope_and_hides_team_id():
    client, _, _, sc = _client()

    response = client.post(
        "/openapi/v1/bots/market/skill-center/skills",
        params={"user_id": "user-1"},
        json={
            "keyword": "calendar",
            "pageNum": 2,
            "pageSize": 10,
            "tagList": ["office"],
            "sortBy": "heat",
            "creatorWorkNo": "10001",
        },
    )

    assert response.status_code == 200
    assert sc.request.page_num == 2
    assert sc.request.page_size == 10
    assert sc.request.tag_list == ("office",)
    assert sc.request.team_id is None
    assert sc.request.access_level == "PUBLIC"
    assert response.json()["data"]["items"][0]["skillCode"] == "sc-calendar"

    request_schema = client.app.openapi()["components"]["schemas"][
        "SkillCenterMarketSearchRequest"
    ]
    properties = request_schema["properties"]
    assert "teamId" not in properties
    assert "appKey" not in properties
    assert "source" not in properties


def test_skill_center_market_rejects_caller_supplied_team_id():
    client, _, _, sc = _client()

    response = client.post(
        "/openapi/v1/bots/market/skill-center/skills",
        params={"user_id": "user-1"},
        json={"teamId": 123},
    )

    assert response.status_code == 422
    assert sc.request is None


def test_skill_center_tags_normalizes_null_children_to_empty_lists():
    tag = {
        "id": 1,
        "name": "研发效能",
        "description": None,
        "iconUrl": None,
        "parentId": None,
        "tagLevel": 1,
        "children": None,
    }

    client, _, _, sc = _client()
    sc.get_market_tags = lambda: [tag]

    response = client.get(
        "/openapi/v1/bots/market/skill-center/tags",
        params={"user_id": "user-1"},
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["children"] == []


def test_skill_center_tags_returns_nested_tag_tree_without_changing_search_contract():
    client, _, _, _ = _client()

    response = client.get(
        "/openapi/v1/bots/market/skill-center/tags",
        params={"user_id": "user-1"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "id": 1,
            "name": "研发效能",
            "description": None,
            "iconUrl": None,
            "parentId": None,
            "tagLevel": 1,
            "children": [
                {
                    "id": 2,
                    "name": "代码质量",
                    "description": None,
                    "iconUrl": None,
                    "parentId": 1,
                    "tagLevel": 2,
                    "children": [],
                }
            ],
        }
    ]

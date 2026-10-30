"""Public routes require authentication and preserve shared protocol semantics."""

import asyncio

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from search_core.adapters.http import create_search_router
from search_core.adapters.http.routes import SearchBody
from search_core.api.errors import (
    SearchOverloaded,
    SearchUnavailable,
    UnsupportedSearchOption,
)
from search_core.api.models import (
    EffectiveRetrieval,
    EffectiveSearch,
    ScoreDetails,
    SearchItem,
    SearchResult,
    StageScore,
    VerifiedIdentity,
)


class Application:
    def __init__(self):
        self.calls = []

    async def execute(self, command, identity):
        self.calls.append((command, identity))
        if command.extensions:
            raise UnsupportedSearchOption("unsupported extension")
        if command.query == "unavailable":
            raise SearchUnavailable("private provider failure")
        if command.query == "overloaded":
            raise SearchOverloaded("no free execution permits")
        if command.query == "slow":
            await asyncio.sleep(0.1)
        return SearchResult(
            items=(SearchItem("agent:1", "Name", "Summary", 0.5, "rrf"),)
        )


async def authenticate(request: Request):
    if request.headers.get("authorization") != "Bearer test-only":
        raise HTTPException(401, "authentication required")
    return VerifiedIdentity("caller", frozenset({"agent:1"}))


def client_for(service, **kwargs):
    app = FastAPI()
    app.include_router(create_search_router(service, authenticate, **kwargs))
    return TestClient(app)


@pytest.mark.parametrize("operation", ["search", "recommend"])
def test_same_request_and_response_contract(operation):
    service = Application()
    with client_for(service) as client:
        result = client.post(
            f"/api/v2/{operation}",
            json={"query": "  助手  "},
            headers={"authorization": "Bearer test-only"},
        )
    assert result.status_code == 200
    assert set(result.json()) == {"items", "status", "reasons"}
    assert result.json()["items"] == [
        {
            "id": "agent:1",
            "name": "Name",
            "summary": "Summary",
            "score": 0.5,
            "score_source": "rrf",
            "extensions": {},
        }
    ]
    command, identity = service.calls[0]
    assert (command.query, command.limit, command.scene, command.operation) == (
        "助手",
        5,
        "default",
        operation,
    )
    assert identity.allowed_ids == frozenset({"agent:1"})


def test_unauthenticated_request_never_reaches_business_service():
    service = Application()
    with client_for(service) as client:
        assert client.post("/api/v2/search", json={"query": "x"}).status_code == 401
    assert service.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"query": " "},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 21},
        {"query": "x", "allowed_ids": ["secret"]},
        {"query": "x", "filters": {"tag": "x"}},
    ],
)
def test_invalid_or_authority_bearing_input_is_rejected(payload):
    service = Application()
    with client_for(service) as client:
        result = client.post(
            "/api/v2/search",
            json=payload,
            headers={"authorization": "Bearer test-only"},
        )
    assert result.status_code == 422
    assert service.calls == []


def test_unsupported_option_and_dependency_failure_are_not_empty_success():
    with client_for(Application()) as client:
        headers = {"authorization": "Bearer test-only"}
        unsupported = client.post(
            "/api/v2/search",
            json={"query": "x", "extensions": {"bad": 1}},
            headers=headers,
        )
        failed = client.post(
            "/api/v2/search", json={"query": "unavailable"}, headers=headers
        )
    assert unsupported.status_code == 400
    assert failed.status_code == 503
    assert "private provider failure" not in failed.text


def test_deadline_is_an_error_not_an_empty_result():
    with client_for(Application(), timeout_seconds=0.01) as client:
        response = client.post(
            "/api/v2/search",
            json={"query": "slow"},
            headers={"authorization": "Bearer test-only"},
        )
    assert response.status_code == 504


def test_capacity_rejection_is_distinct_from_dependency_failure():
    with client_for(Application()) as client:
        response = client.post(
            "/api/v2/search",
            json={"query": "overloaded"},
            headers={"authorization": "Bearer test-only"},
        )
    assert response.status_code == 429


def test_host_can_delegate_positive_limit_to_application_policy():
    service = Application()
    with client_for(service, request_model=SearchBody) as client:
        response = client.post(
            "/api/v2/search",
            json={"query": "x", "limit": 21},
            headers={"authorization": "Bearer test-only"},
        )
        schema = client.get("/openapi.json").json()["components"]["schemas"][
            "SearchBody"
        ]
    assert response.status_code == 200
    assert service.calls[0][0].limit == 21
    assert "maximum" not in schema["properties"]["limit"]


@pytest.mark.parametrize("operation", ["search", "recommend"])
def test_response_preserves_effective_parameters_and_degradation(operation):
    class DegradedApplication:
        async def execute(self, command, identity):
            return SearchResult(
                status="degraded",
                reasons=("embedding_unavailable",),
                effective=EffectiveSearch(
                    limit=3,
                    retrieval=EffectiveRetrieval(True, True, False),
                ),
            )

    with client_for(DegradedApplication()) as client:
        response = client.post(
            f"/api/v2/{operation}",
            json={"query": "x", "limit": 5},
            headers={"authorization": "Bearer test-only"},
        )
    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "status": "degraded",
        "reasons": ["embedding_unavailable"],
        "effective": {
            "limit": 3,
            "retrieval": {
                "keyword_enabled": True,
                "vector_enabled": True,
                "rerank_enabled": False,
            },
        },
    }


def test_response_omits_unknown_retrieval_configuration():
    class Application:
        async def execute(self, command, identity):
            return SearchResult(effective=EffectiveSearch(limit=command.limit))

    with client_for(Application()) as client:
        response = client.post(
            "/api/v2/search",
            json={"query": "x"},
            headers={"authorization": "Bearer test-only"},
        )
    assert response.json()["effective"] == {"limit": 5}


@pytest.mark.parametrize("operation", ["search", "recommend"])
def test_stage_scores_are_additive_and_missing_stages_are_omitted(operation):
    class Application:
        async def execute(self, command, identity):
            return SearchResult(
                items=(
                    SearchItem(
                        "a",
                        "A",
                        "summary",
                        0.01,
                        "rrf",
                        score_details=ScoreDetails(
                            keyword=StageScore(0.0, 2), rrf=StageScore(0.01, 1)
                        ),
                    ),
                )
            )

    with client_for(Application()) as client:
        response = client.post(
            f"/api/v2/{operation}",
            json={"query": "x"},
            headers={"authorization": "Bearer test-only"},
        )
    item = response.json()["items"][0]
    assert item["score"] == 0.01 and item["score_source"] == "rrf"
    assert item["summary"] == "summary"
    assert item["scoreDetails"] == {
        "keyword": {"value": 0.0, "rank": 2},
        "rrf": {"value": 0.01, "rank": 1},
    }

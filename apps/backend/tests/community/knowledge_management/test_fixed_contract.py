"""Golden HTTP responses cover every interaction field, including honest nulls."""

import json
from collections import Counter
from pathlib import Path

import pytest
from agentclaw.community.adapters.http.knowledge_management.router import router
from agentclaw.community.core.knowledge_management.config import (
    KnowledgeManagementConfig,
)

from .conftest import grant


@pytest.fixture
def expected():
    return json.loads(
        (Path(__file__).parent / "fixtures/management_contract.json").read_text()
    )


def test_fixed_catalog_and_detail_http_samples(client, expected):
    prefix = "/api/knowledge/spaces/1/knowledge-bases"
    assert client.get(prefix).json() == expected["catalog"]
    assert client.get(prefix + "/kb/catalog-detail").json() == expected["detail"]


def test_fixed_grant_http_sample_retains_missing_bot(client, world, expected):
    world.catalog.grants = {"items": [grant(), grant("deleted")], "next_cursor": 17}
    response = client.get("/api/knowledge/spaces/1/knowledge-bases/kb/authorized-bots")
    assert response.status_code == 200
    assert response.json() == expected["authorized_bots"]


def test_one_route_per_method_and_path():
    routes = Counter(
        (method, route.path) for route in router.routes for method in route.methods
    )
    assert all(count == 1 for count in routes.values())
    assert len(routes) == 10  # Includes the separate conversation-only projection
    assert ("GET", "/api/knowledge/spaces/{space_id}/bots") not in routes
    assert (
        "GET",
        "/api/knowledge/bots/{bot_id}/conversation-knowledge-bases",
    ) in routes


@pytest.mark.parametrize(
    "suffix", ["", "/kb/catalog-detail", "/kb/authorized-bots", "/kb/bot-candidates"]
)
def test_all_reads_reuse_existing_closed_config(client, world, suffix):
    from fastapi_injector import get_injector_instance

    injector = get_injector_instance(client.app)
    injector.binder.bind(KnowledgeManagementConfig, to=KnowledgeManagementConfig())
    response = client.get("/api/knowledge/spaces/1/knowledge-bases" + suffix)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KNOWLEDGE_INTEGRATION_UNAVAILABLE"
    assert world.catalog.calls == []

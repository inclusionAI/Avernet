"""Agent business validates scope/options before delegating to retrieval."""

import asyncio
import subprocess
import sys

import pytest
from agent_search.bootstrap.app import create_agent_search_app
from agent_search.search.service import AgentSearchApplication
from avernet_search.api.errors import UnsupportedSearchOption
from avernet_search.api.models import (
    SearchCommand,
    SearchItem,
    SearchResult,
    VerifiedIdentity,
)
from avernet_search.core.ranking import reciprocal_rank_fusion
from fastapi import Request
from fastapi.testclient import TestClient


class Retrieval:
    def __init__(self):
        self.calls = []

    async def retrieve(self, request):
        self.calls.append(request)
        return SearchResult(items=(SearchItem("agent:1", "Agent", "能力", 0.4, "rrf"),))


def test_preserves_prefilter_and_trusted_scope_without_forwarding_extensions():
    retrieval = Retrieval()
    service = AgentSearchApplication(retrieval, filter_fields=frozenset({"category"}))
    result = asyncio.run(
        service.execute(
            SearchCommand("助手", 3, "search", filters={"category": ("payment",)}),
            VerifiedIdentity("bcn", frozenset({"agent:1"})),
        )
    )
    assert [item.item_id for item in result.items] == ["agent:1"]
    assert retrieval.calls[0].filters == {"category": ("payment",)}
    assert retrieval.calls[0].allowed_ids == frozenset({"agent:1"})


@pytest.mark.parametrize(
    "command",
    [
        SearchCommand("x", 5, "search", filters={"unknown": ("x",)}),
        SearchCommand("x", 5, "search", extensions={"strategy": "unimplemented"}),
        SearchCommand("x", 5, "search", scene="unimplemented"),
    ],
)
def test_unsupported_policy_is_not_silently_ignored(command):
    retrieval = Retrieval()
    with pytest.raises(UnsupportedSearchOption):
        asyncio.run(
            AgentSearchApplication(retrieval).execute(
                command, VerifiedIdentity("bcn", None)
            )
        )
    assert retrieval.calls == []


def test_empty_scope_does_not_query_all_agents():
    retrieval = Retrieval()
    result = asyncio.run(
        AgentSearchApplication(retrieval).execute(
            SearchCommand("x", 5, "search"),
            VerifiedIdentity("bcn", frozenset()),
        )
    )
    assert result.items == ()
    assert retrieval.calls == []


def test_empty_filter_is_no_match_and_provider_cannot_leak_out_of_scope():
    retrieval = Retrieval()
    service = AgentSearchApplication(retrieval, filter_fields=frozenset({"category"}))
    empty = asyncio.run(
        service.execute(
            SearchCommand("x", 5, "search", {"category": ()}),
            VerifiedIdentity("bcn", None),
        )
    )
    assert empty.items == () and retrieval.calls == []
    scoped = asyncio.run(
        service.execute(
            SearchCommand("x", 5, "search"),
            VerifiedIdentity("bcn", frozenset({"other"})),
        )
    )
    assert scoped.items == ()


async def authenticate(request: Request):
    return VerifiedIdentity("test-bcn", None)


def test_app_uses_shared_routes_and_owns_no_tc_routes():
    with TestClient(create_agent_search_app(Retrieval(), authenticate)) as client:
        for operation in ("search", "recommend"):
            response = client.post(f"/api/v2/{operation}", json={"query": "助手"})
            assert response.status_code == 200
            assert response.json()["items"][0]["id"] == "agent:1"
        assert client.post("/api/v1/groups/g/fuse", json={}).status_code == 404
        assert client.get("/health").json() == {"status": "ok"}


def test_synthetic_catalog_uses_shared_ranking_and_apps_do_not_share_providers():
    class Catalog:
        def __init__(self, prefix):
            self.cards = {
                f"{prefix}:pay": {"name": "支付助手", "description": "支付查询"},
                f"{prefix}:help": {"name": "帮助助手", "description": "支付与账户帮助"},
            }

        async def retrieve(self, request):
            # Test-only ranked sources. Real model/index adapters arrive separately.
            eligible = [
                key
                for key in self.cards
                if request.allowed_ids is None or key in request.allowed_ids
            ]
            ranking = reciprocal_rank_fusion([eligible, list(reversed(eligible))])
            return SearchResult(
                items=tuple(
                    SearchItem(
                        item.item_id,
                        self.cards[item.item_id]["name"],
                        self.cards[item.item_id]["description"],
                        item.score,
                        "rrf",
                    )
                    for item in ranking[: request.limit]
                )
            )

    first = create_agent_search_app(Catalog("first"), authenticate)
    second = create_agent_search_app(Catalog("second"), authenticate)
    with TestClient(first) as first_client, TestClient(second) as second_client:
        for operation in ("search", "recommend"):
            left = first_client.post(
                f"/api/v2/{operation}", json={"query": "支付", "limit": 2}
            ).json()
            right = second_client.post(
                f"/api/v2/{operation}", json={"query": "支付", "limit": 2}
            ).json()
            assert [item["id"] for item in left["items"]] == ["first:help", "first:pay"]
            assert [item["id"] for item in right["items"]] == [
                "second:help",
                "second:pay",
            ]
            assert left["items"][0]["score_source"] == "rrf"


def test_fresh_process_can_start_and_search_with_tc_imports_forbidden():
    script = """
import importlib.abc, sys
class BlockTC(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'src', 'bcsfuse_internal', 'qdrant_client'}:
            raise AssertionError('Forbidden dependency: ' + fullname)
sys.meta_path.insert(0, BlockTC())
from fastapi import Request
from fastapi.testclient import TestClient
from agent_search.bootstrap.app import create_agent_search_app
from avernet_search.api.models import SearchResult, VerifiedIdentity
class Retrieval:
    async def retrieve(self, request): return SearchResult()
async def auth(request: Request): return VerifiedIdentity('test', None)
with TestClient(create_agent_search_app(Retrieval(), auth)) as client:
    assert client.post('/api/v2/search', json={'query':'x'}).json()['items'] == []
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr

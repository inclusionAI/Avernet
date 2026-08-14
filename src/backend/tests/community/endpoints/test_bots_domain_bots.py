"""Endpoint test for GET /api/bots/search/domain-bots.

Exercises the real handler → real ``BotService`` → real repository against the
per-test SQLite DB: a domain bot is inserted directly, then the endpoint is
expected to return it. No service mocking — a previous mock-based version patched
``BotService.list_domain_bots`` at the class level and never stopped the patcher,
which leaked a "Database connection failed" stub into later, order-dependent
tests. (The contrived service-raises-500 case was dropped with that mock; the
handler's generic try/except is covered elsewhere.)
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import BotRepository

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


def _seed_domain_bots(world):
    """Insert domain bots into the per-test DB."""
    repo = world.get(BotRepository)
    for i, name in enumerate(["Domain Architect Bot", "Security Domain Bot", "Platform Bot"], 1):
        repo.insert(
            {
                "bot_id": f"domain_bot_{i}",
                "bot_name": name,
                "owner_id": "test_user",
                "owner_name": "test_user",
                "creator_id": "test_user",
                "entity_id": "test_user",
                "entity_type": "staff",
                "active_engine": "openclaw",
                "bot_type": "personal",
                "status": "ACTIVE",
                "ext": {"is_domain_bot": True},
            }
        )


@endpoint_test(
    method="GET",
    path="/api/bots/search/domain-bots",
    scenario="ok_no_pagination",
    input=CaseInput(query_params={}, headers={"x-user-id": "operator-user"}),
    seed=_seed_domain_bots,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"total": 3},
        },
    ),
)
def list_domain_bots_ok_no_pagination():
    """Happy path: no pagination params returns all domain bots."""


@endpoint_test(
    method="GET",
    path="/api/bots/search/domain-bots",
    scenario="ok_with_pagination",
    input=CaseInput(
        query_params={"page": "1", "page_size": "2"},
        headers={"x-user-id": "operator-user"},
    ),
    seed=_seed_domain_bots,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"total": 3},
        },
    ),
)
def list_domain_bots_ok_with_pagination():
    """Happy path: with pagination, returns paginated results."""


@endpoint_test(
    method="GET",
    path="/api/bots/search/domain-bots",
    scenario="ok_keyword_match",
    input=CaseInput(
        query_params={"keyword": "Security"},
        headers={"x-user-id": "operator-user"},
    ),
    seed=_seed_domain_bots,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"total": 1, "items": [{"bot_id": "domain_bot_2"}]},
        },
    ),
)
def list_domain_bots_keyword_match():
    """Happy path: keyword filters to matching bots only."""


@endpoint_test(
    method="GET",
    path="/api/bots/search/domain-bots",
    scenario="ok_keyword_no_match",
    input=CaseInput(
        query_params={"keyword": "nonexistent"},
        headers={"x-user-id": "operator-user"},
    ),
    seed=_seed_domain_bots,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"total": 0, "items": []},
        },
    ),
)
def list_domain_bots_keyword_no_match():
    """Happy path: keyword with no matches returns empty."""


@endpoint_test(
    method="GET",
    path="/api/bots/search/domain-bots",
    scenario="invalid_pagination",
    input=CaseInput(
        query_params={"page": "0", "page_size": "20"},
        headers={"x-user-id": "operator-user"},
    ),
    expect=ExpectError(status=422),
)
def list_domain_bots_invalid_page():
    """Error path: page=0 violates Query(ge=1) → 422 (real validation, no mock)."""

"""Tests for the ``GET /openapi/v1/bots`` list upgrade (Track B #2).

Covers the three new workshop filters (DEMO §0.1): ``deploy_mode``/``service``/
``space``. The legacy ``keyword``/``engine``/``status`` filters keep going
straight to ``BotService.list_bots_by_conditions``; the new ones are not known
downstream, so the handler post-filters. When none of the three is supplied
the handler must behave exactly as before — that is what keeps existing callers
untouched.

``space`` filters by the structured ``ac_bots.space_id`` column (NOT
``bot.ext``); a bot with NULL ``space_id`` falls back to the personal space
``personal:{owner_id}`, so the personal view always contains it.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1.bots.router import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_service import BotServiceProtocol


def _bot(bot_id: str, bot_type: str = "personal", space_id: str | None = None) -> dict:
    bot = {
        "bot_id": bot_id,
        "bot_name": bot_id,
        "bot_desc": "",
        "active_engine": "openclaw",
        "bot_type": bot_type,
        "status": "ACTIVE",
        "owner_id": "u1",
    }
    if space_id is not None:
        bot["space_id"] = space_id
    return bot


@pytest.fixture
def bot_service():
    svc = MagicMock()
    return svc


@pytest.fixture
def client(bot_service):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=bot_service)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, "u1")


def _ok(resp):
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["code"] == 200000
    return body["data"]


def test_no_new_filters_uses_legacy_path(bot_service, client):
    # arrange: a normal paged row the service returns.
    bot_service.list_bots_by_conditions.return_value = {
        "total": 1,
        "items": [_bot("b1")],
    }

    data = _ok(client.get("/openapi/v1/bots"))

    assert data["total"] == 1
    assert [i["bot_id"] for i in data["items"]] == ["b1"]
    # The handler must forward the caller's page/page_size to the service in the
    # legacy path — not re-page on a fetched window.
    kwargs = bot_service.list_bots_by_conditions.call_args.kwargs
    assert kwargs["page"] == 1
    assert kwargs["page_size"] == 20
    assert "deploy_mode" not in kwargs  # service does not know the new filters


def test_deploy_mode_cloud_is_a_noop_for_legacy_path(bot_service, client):
    bot_service.list_bots_by_conditions.return_value = {
        "total": 1,
        "items": [_bot("b1")],
    }

    _ok(client.get("/openapi/v1/bots?deploy_mode=cloud"))

    # cloud is the endpoint's only mode, so it must NOT flip the handler into the
    # post-filter / re-pagination branch — the legacy one-page path is used.
    kwargs = bot_service.list_bots_by_conditions.call_args.kwargs
    assert kwargs["page"] == 1


def test_deploy_mode_local_returns_empty_page(bot_service, client):
    # This endpoint does not own local bots — local lives under /bots/local —
    # so a local view here is empty by definition and the service is not called.
    data = _ok(client.get("/openapi/v1/bots?deploy_mode=local"))

    assert data["total"] == 0
    assert data["items"] == []
    bot_service.list_bots_by_conditions.assert_not_called()


def test_service_filter_keeps_only_service_bots(bot_service, client):
    bot_service.list_bots_by_conditions.return_value = {
        "total": 3,
        "items": [
            _bot("p1", bot_type="personal"),
            _bot("s1", bot_type="service"),
            _bot("s2", bot_type="service"),
        ],
    }

    data = _ok(client.get("/openapi/v1/bots?service=yes"))

    assert [i["bot_id"] for i in data["items"]] == ["s1", "s2"]


def test_service_no_keeps_only_non_service_bots(bot_service, client):
    bot_service.list_bots_by_conditions.return_value = {
        "total": 2,
        "items": [
            _bot("p1", bot_type="personal"),
            _bot("s1", bot_type="service"),
        ],
    }

    data = _ok(client.get("/openapi/v1/bots?service=no"))

    assert [i["bot_id"] for i in data["items"]] == ["p1"]


def test_space_filter_matches_structured_column_and_personal_fallback(bot_service, client):
    bot_service.list_bots_by_conditions.return_value = {
        "total": 3,
        "items": [
            _bot("p1", space_id="personal:u1"),  # explicit personal
            _bot("p2"),  # NULL space_id → personal fallback personal:u1
            _bot("t1", space_id="team:9"),  # different space
        ],
    }

    # Personal space view: both p1 and p2 land in personal:u1 (the latter via
    # the NULL → personal fallback).
    data = _ok(client.get("/openapi/v1/bots?space=personal:u1"))
    assert [i["bot_id"] for i in data["items"]] == ["p1", "p2"]

    # A team space view keeps only the bot whose structured column matches.
    data_team = _ok(client.get("/openapi/v1/bots?space=team:9"))
    assert [i["bot_id"] for i in data_team["items"]] == ["t1"]


def test_new_filter_repages_with_bounded_window(bot_service, client):
    # The post-filter branch must not forward the caller's small page_size to the
    # service (which would slice before filtering); it fetches a bounded window.
    bot_service.list_bots_by_conditions.return_value = {
        "total": 2,
        "items": [_bot("p1"), _bot("s1", bot_type="service")],
    }

    _ok(client.get("/openapi/v1/bots?service=no&page=1&page_size=5"))

    kwargs = bot_service.list_bots_by_conditions.call_args.kwargs
    # Window, not the caller's slice: page=1, page_size is the bounded window.
    assert kwargs["page"] == 1
    assert kwargs["page_size"] > 5

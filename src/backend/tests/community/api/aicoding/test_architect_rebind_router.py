"""Unit tests for the aicoding architect-rebind router.

``PUT /api/bots/{architect_bot_id}/architect-rebind`` moved from the generic
bot-management router into the aicoding HTTP package (Phase 2 migration).
The route owns its own ``ApiResponse``/``RebindArchitectRequest`` models and
binds ``ArchitectRebindServiceProtocol`` via the community injector; these
tests exercise the happy path plus every error branch via a minimal FastAPI
test app (never imports ``agentclaw.servers.web.app``).
"""
from __future__ import annotations

from typing import Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module
from unittest.mock import MagicMock

from agentclaw.community.adapters.http.aicoding.architect_rebind_router import (
    router as architect_rebind_router,
)
from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.api.aicoding.architect_rebind_service import (
    ArchitectRebindServiceProtocol,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotPermissionError,
    BotServiceError,
)


def _make_ctx(user_id: str = "test_user", nick_name: str = "Test User") -> RequestContext:
    return RequestContext(user_id=user_id, nick_name=nick_name)


def _bind_architect_rebind_service(svc) -> Module:
    class _M(Module):
        def configure(self, binder):
            binder.bind(ArchitectRebindServiceProtocol, to=svc)

    return _M()


@pytest.fixture
def client():
    """Minimal FastAPI app mounting only the aicoding architect-rebind router."""
    svc = MagicMock()

    app = FastAPI()
    app.include_router(architect_rebind_router)

    # Owner/operator comes from the request context by default; tests override
    # it when they want a different caller (e.g. anonymous user).
    app.dependency_overrides[get_request_context] = lambda: _make_ctx()

    attach_injector(app, Injector([_bind_architect_rebind_service(svc)]))

    yield TestClient(app), svc


# ===========================================================================
# PUT /api/bots/{architect_bot_id}/architect-rebind
# ===========================================================================
class TestRebindArchitectBot:
    def test_success(self, client):
        tc, svc = client
        svc.rebind_architect_bot_batch.return_value = {
            "architect_bot_id": "arch1",
            "results": [
                {"bot_id": "c1", "success": True, "changed": True,
                 "previous_architect_bot_id": "old", "architect_bot_id": "arch1"},
            ],
            "total": 1,
            "succeeded": 1,
            "failed": 0,
        }
        resp = tc.put(
            "/api/bots/arch1/architect-rebind",
            json={"coding_bot_ids": ["c1", "c2", "c1"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["total"] == 1
        kwargs = svc.rebind_architect_bot_batch.call_args.kwargs
        assert kwargs["architect_bot_id"] == "arch1"
        assert kwargs["coding_bot_ids"] == ["c1", "c2", "c1"]
        assert kwargs["operator_id"] == "test_user"

    def test_anonymous_user_rejected(self, client):
        tc, svc = client
        tc.app.dependency_overrides[get_request_context] = lambda: _make_ctx(user_id="anonymous")
        resp = tc.put(
            "/api/bots/arch1/architect-rebind",
            json={"coding_bot_ids": ["c1"]},
        )
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 400
        svc.rebind_architect_bot_batch.assert_not_called()

    def test_empty_coding_ids_rejected_by_request_model(self, client):
        tc, svc = client
        # RebindArchitectRequest validator rejects empty/all-blank list -> 422
        resp = tc.put(
            "/api/bots/arch1/architect-rebind",
            json={"coding_bot_ids": ["  ", ""]},
        )
        assert resp.status_code == 422
        svc.rebind_architect_bot_batch.assert_not_called()

    def test_coding_bot_ids_are_stripped_of_whitespace(self, client):
        tc, svc = client
        svc.rebind_architect_bot_batch.return_value = {
            "architect_bot_id": "arch1", "results": [], "total": 0,
            "succeeded": 0, "failed": 0,
        }
        resp = tc.put(
            "/api/bots/arch1/architect-rebind",
            json={"coding_bot_ids": ["  c1  ", "c2", "   "]},
        )
        assert resp.status_code == 200
        kwargs = svc.rebind_architect_bot_batch.call_args.kwargs
        assert kwargs["coding_bot_ids"] == ["c1", "c2"]

    def test_bot_not_found(self, client):
        tc, svc = client
        svc.rebind_architect_bot_batch.side_effect = BotNotFoundError("nope")
        resp = tc.put("/api/bots/arch1/architect-rebind", json={"coding_bot_ids": ["c1"]})
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 404

    def test_permission_error(self, client):
        tc, svc = client
        svc.rebind_architect_bot_batch.side_effect = BotPermissionError("forbidden")
        resp = tc.put("/api/bots/arch1/architect-rebind", json={"coding_bot_ids": ["c1"]})
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 403

    def test_service_error(self, client):
        tc, svc = client
        svc.rebind_architect_bot_batch.side_effect = BotServiceError("bad")
        resp = tc.put("/api/bots/arch1/architect-rebind", json={"coding_bot_ids": ["c1"]})
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400

    def test_unexpected_exception(self, client):
        tc, svc = client
        svc.rebind_architect_bot_batch.side_effect = ValueError("boom")
        resp = tc.put("/api/bots/arch1/architect-rebind", json={"coding_bot_ids": ["c1"]})
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500

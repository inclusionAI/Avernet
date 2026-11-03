"""GET /api/public/bots/{bot_id}/overview — unauthenticated, typed overview."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.bot_public import bot_public_noauth_router
from agentclaw.community.api.bot_overview_service import (
    BotOverviewNotFoundError,
    BotOverviewServiceProtocol,
)
from agentclaw.community.core.bot_overview.models import BotOverview
from agentclaw.community.core.bot_overview.payloads import AICodingEnginePayload

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def _client(service: MagicMock) -> TestClient:
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotOverviewServiceProtocol, to=service)

    app = FastAPI()
    app.include_router(bot_public_noauth_router)
    attach_injector(app, Injector([_M()]))
    return TestClient(app)


def test_returns_overview_without_credentials() -> None:
    service = MagicMock()
    service.get_overview.return_value = BotOverview(
        bot_id="b1",
        engine="claude_code",
        status="ACTIVE",
        observed_at=NOW,
        payload=AICodingEnginePayload(engine="claude_code"),
    )

    response = _client(service).get("/api/public/bots/b1/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["bot_id"] == "b1"
    assert body["engine"] == "claude_code"
    assert body["status"] == "ACTIVE"
    assert body["payload"] == {"engine": "claude_code"}
    assert body["skills"] is None and body["sessions"] is None
    service.get_overview.assert_called_once_with("b1", activity_days=14)


def test_passes_activity_window() -> None:
    service = MagicMock()
    service.get_overview.return_value = BotOverview(
        bot_id="b1", engine="openclaw", status="ACTIVE", observed_at=NOW
    )

    response = _client(service).get("/api/public/bots/b1/overview?activity_days=30")

    assert response.status_code == 200
    service.get_overview.assert_called_once_with("b1", activity_days=30)


def test_rejects_out_of_range_activity_window() -> None:
    service = MagicMock()
    client = _client(service)

    assert client.get("/api/public/bots/b1/overview?activity_days=0").status_code == 422
    assert client.get("/api/public/bots/b1/overview?activity_days=91").status_code == 422
    service.get_overview.assert_not_called()


def test_unknown_bot_is_404() -> None:
    service = MagicMock()
    service.get_overview.side_effect = BotOverviewNotFoundError("nope")

    response = _client(service).get("/api/public/bots/nope/overview")

    assert response.status_code == 404


def test_openapi_types_the_engine_payload_as_a_discriminated_union() -> None:
    schema = _client(MagicMock()).get("/openapi.json").json()

    payload = schema["components"]["schemas"]["BotOverview"]["properties"]["payload"]
    union = next(branch for branch in payload["anyOf"] if "discriminator" in branch)
    assert union["discriminator"]["propertyName"] == "engine"
    assert set(union["discriminator"]["mapping"]) == {"aicoding", "claude_code"}
    assert "AICodingEnginePayload" in schema["components"]["schemas"]


def test_legacy_public_routes_still_mounted() -> None:
    paths = _client(MagicMock()).get("/openapi.json").json()["paths"]
    assert "/api/public/bots/{bot_id}/binding" in paths
    assert "/api/public/bots/{bot_id}/overview" in paths

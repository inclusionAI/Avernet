"""``GET /openapi/v1/bots/{bot_id}/overview`` — app-admissible, non-user overview.

Wired through the real signed-principal seam and public error handlers with a
fake overview service, so each case pins one rule:

- an application acting alone (no end user) is admitted, as is a person;
- there is still no anonymous access: an unsigned request is a 401;
- an unknown Bot is the surface's 404, the window is bounded;
- the published document types the engine payload as a discriminated union.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.openapi_v1.bots.overview import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    PRINCIPAL_HEADER,
    require_principal,
)
from agentclaw.community.api.bot_overview_service import (
    BotOverviewNotFoundError,
    BotOverviewServiceProtocol,
)
from agentclaw.community.core.bot_overview.models import BotOverview, SessionActivity
from agentclaw.community.core.bot_overview.payloads import AICodingEnginePayload
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    public_document,
)
from tests.community.adapters.http.openapi_v1.test_principal_seam import (
    mint,
    signing_key,  # noqa: F401 — installs/resets the real verifier per test
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
_PATH = "/openapi/v1/bots/b1/overview"


def _overview(engine: str = "claude_code") -> BotOverview:
    return BotOverview(
        bot_id="b1",
        engine=engine,
        status="ACTIVE",
        observed_at=NOW,
        sessions=SessionActivity(window_start=NOW - timedelta(days=14)),
        payload=AICodingEnginePayload(engine=engine),
    )


@pytest.fixture
def overview_client():
    service = MagicMock()
    service.get_overview.return_value = _overview()
    app = mount_public_error_handlers(FastAPI())
    app.include_router(router, dependencies=[Depends(require_principal)])
    injector = Injector()
    injector.binder.bind(BotOverviewServiceProtocol, to=service)
    attach_injector(app, injector)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service


def test_app_only_caller_is_admitted(overview_client):
    client, service = overview_client

    response = client.get(_PATH, headers={PRINCIPAL_HEADER: mint(user_id=None)})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["bot_id"] == "b1"
    assert data["engine"] == "claude_code"
    assert data["payload"] == {"engine": "claude_code"}
    assert data["skills"] == [] and data["mcps"] == []
    assert data["sessions"]["daily"] == []
    service.get_overview.assert_called_once_with("b1", activity_days=14)


def test_person_is_admitted_too(overview_client):
    client, _ = overview_client

    response = client.get(_PATH, headers={PRINCIPAL_HEADER: mint()})

    assert response.status_code == 200


def test_unsigned_request_is_refused(overview_client):
    client, service = overview_client

    assert client.get(_PATH).status_code == 401
    service.get_overview.assert_not_called()


def test_passes_activity_window(overview_client):
    client, service = overview_client

    response = client.get(
        _PATH,
        params={"activity_days": 30},
        headers={PRINCIPAL_HEADER: mint(user_id=None)},
    )

    assert response.status_code == 200
    service.get_overview.assert_called_once_with("b1", activity_days=30)


@pytest.mark.parametrize("days", [0, 91])
def test_rejects_out_of_range_activity_window(overview_client, days):
    client, service = overview_client

    response = client.get(
        _PATH,
        params={"activity_days": days},
        headers={PRINCIPAL_HEADER: mint(user_id=None)},
    )

    assert response.status_code == 422
    service.get_overview.assert_not_called()


def test_unknown_bot_is_404(overview_client):
    client, service = overview_client
    service.get_overview.side_effect = BotOverviewNotFoundError("b1")

    response = client.get(_PATH, headers={PRINCIPAL_HEADER: mint(user_id=None)})

    assert response.status_code == 404


def test_document_types_the_engine_payload_as_a_discriminated_union():
    schema = public_document()

    operation = schema["paths"]["/openapi/v1/bots/{bot_id}/overview"]["get"]
    assert "user_id" not in {p["name"] for p in operation.get("parameters", [])}
    payload = schema["components"]["schemas"]["BotOverview"]["properties"]["payload"]
    union = next(branch for branch in payload["anyOf"] if "discriminator" in branch)
    assert union["discriminator"]["propertyName"] == "engine"
    assert set(union["discriminator"]["mapping"]) == {"aicoding", "claude_code"}
    assert "AICodingEnginePayload" in schema["components"]["schemas"]

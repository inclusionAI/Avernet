"""``GET /openapi/v1/bots/{bot_id}/overview`` — app-admissible, non-user overview.

Wired through the real signed-principal seam and public error handlers with a
fake overview service, so each case pins one rule:

- an application acting alone (no end user) is admitted, as is a person;
- there is still no anonymous access: an unsigned request is a 401;
- an unknown Bot is the surface's 404;
- the published document types the engine payload as a discriminated union.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

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
        sessions=SessionActivity(last_session_at=NOW - timedelta(days=3)),
        payload=AICodingEnginePayload(engine=engine),
    )


@pytest.fixture
def overview_client():
    service = MagicMock()
    service.get_overview = AsyncMock(return_value=_overview())
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
    assert data["payload"] == {
        "engine": "claude_code",
        "template_type": None,
        "template_version": None,
        "bot_type": None,
        "template_factory": None,
        "hosted_workspace_enabled": False,
        "devflow_workflow": None,
        "code_repos": [],
        "yuque_kb_repos": [],
    }
    assert data["skills"] == [] and data["mcps"] == []
    assert data["sessions"] == {"last_session_at": "2026-10-31T12:00:00Z"}
    service.get_overview.assert_called_once_with("b1")


def test_person_is_admitted_too(overview_client):
    client, _ = overview_client

    response = client.get(_PATH, headers={PRINCIPAL_HEADER: mint()})

    assert response.status_code == 200


def test_unsigned_request_is_refused(overview_client):
    client, service = overview_client

    assert client.get(_PATH).status_code == 401
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
    aicoding = schema["components"]["schemas"]["AICodingEnginePayload"]
    assert "AICodingTemplateFactoryIdentity" in schema["components"]["schemas"]
    for field in (
        "template_type",
        "template_version",
        "bot_type",
        "template_factory",
        "hosted_workspace_enabled",
        "devflow_workflow",
        "code_repos",
        "yuque_kb_repos",
    ):
        assert field in aicoding["properties"]

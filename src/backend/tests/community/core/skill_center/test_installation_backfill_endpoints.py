"""End-to-end tests for /api/internal/skill-center/installations/*."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, singleton

from agentclaw.community.adapters.http.skill_center.installations_internal import (
    internal_router,
)
from agentclaw.community.adapters.http.skill_center.internal_auth import (
    verify_skill_center_internal_token,
)
from agentclaw.community.api.installation_backfill_service import (
    InstallationBackfillServiceProtocol,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.di.config import SkillCenterInternalToken

_TOKEN = "test-token"
_URL = "/api/internal/skill-center/installations/backfill/bot"


class _Service:
    """Records what the router asked for, answers as scripted."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._raises = raises

    def backfill_bot(self, *, bot_id: str, owner_id: str) -> None:
        self.calls.append({"bot_id": bot_id, "owner_id": owner_id})
        if self._raises is not None:
            raise self._raises


def _client(service: _Service, *, with_auth: bool = False) -> TestClient:
    """Mount only the internal router, with the service bound behind it.

    ``with_auth=False`` overrides the token dependency, so the endpoint tests
    read as endpoint tests; the auth gate itself is covered by the tests that
    pass ``with_auth=True``.
    """
    app = FastAPI()
    app.include_router(internal_router)
    if not with_auth:
        app.dependency_overrides[verify_skill_center_internal_token] = (
            lambda authorization=None: None
        )

    injector = Injector()
    injector.binder.bind(
        InstallationBackfillServiceProtocol,
        InstanceProvider(service),
        scope=singleton,
    )
    injector.binder.bind(
        SkillCenterInternalToken,
        InstanceProvider(SkillCenterInternalToken(value=_TOKEN)),
        scope=singleton,
    )
    attach_injector(app, injector)
    return TestClient(app)


@pytest.mark.unit
def test_it_converges_the_named_bot_and_echoes_the_pair():
    service = _Service()
    response = _client(service).post(
        _URL, json={"bot_id": "bot-1", "owner_id": "owner"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {"bot_id": "bot-1", "owner_id": "owner"},
    }
    assert service.calls == [{"bot_id": "bot-1", "owner_id": "owner"}]


@pytest.mark.unit
def test_it_404s_an_unknown_bot():
    service = _Service(raises=LocalSkillNotFoundError())
    response = _client(service).post(
        _URL, json={"bot_id": "nope", "owner_id": "owner"}
    )

    assert response.status_code == 404


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [{}, {"bot_id": "bot-1"}, {"owner_id": "owner"}, {"bot_id": "", "owner_id": "o"}],
)
def test_it_rejects_an_incomplete_pair(body: dict):
    service = _Service()
    response = _client(service).post(_URL, json=body)

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.unit
def test_a_request_without_the_token_never_reaches_the_service():
    service = _Service()
    response = _client(service, with_auth=True).post(
        _URL, json={"bot_id": "bot-1", "owner_id": "owner"}
    )

    assert response.status_code == 422  # the Header(...) is required
    assert service.calls == []


@pytest.mark.unit
def test_a_request_with_the_wrong_token_never_reaches_the_service():
    service = _Service()
    response = _client(service, with_auth=True).post(
        _URL,
        json={"bot_id": "bot-1", "owner_id": "owner"},
        headers={"Authorization": "Bearer wrong"},
    )

    assert response.status_code == 401
    assert service.calls == []


@pytest.mark.unit
def test_the_configured_token_is_accepted():
    service = _Service()
    response = _client(service, with_auth=True).post(
        _URL,
        json={"bot_id": "bot-1", "owner_id": "owner"},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )

    assert response.status_code == 200
    assert service.calls == [{"bot_id": "bot-1", "owner_id": "owner"}]

"""End-to-end tests for /api/internal/skill-center/installations/* endpoints."""

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
    BackfillReport,
    BotBackfillOutcome,
    InstallationBackfillServiceProtocol,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.di.config import SkillCenterInternalToken

_TOKEN = "test-token"


class _Service:
    """Records what the router asked for, answers as scripted."""

    def __init__(
        self,
        *,
        outcome: BotBackfillOutcome | None = None,
        report: BackfillReport | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.bot_calls: list[dict] = []
        self.page_calls: list[dict] = []
        self._outcome = outcome or BotBackfillOutcome(
            bot_id="bot-1", owner_id="owner", changed=True
        )
        self._report = report or BackfillReport(
            total=1,
            page=1,
            page_size=50,
            scanned=1,
            changed=1,
            failed=0,
            outcomes=(self._outcome,),
        )
        self._raises = raises

    def backfill_bot(self, *, bot_id: str, owner_id: str) -> BotBackfillOutcome:
        self.bot_calls.append({"bot_id": bot_id, "owner_id": owner_id})
        if self._raises is not None:
            raise self._raises
        return self._outcome

    def backfill_page(self, **kwargs) -> BackfillReport:
        self.page_calls.append(kwargs)
        return self._report


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
def test_bot_backfill_returns_the_outcome():
    service = _Service()
    response = _client(service).post(
        "/api/internal/skill-center/installations/backfill/bot",
        json={"bot_id": "bot-1", "owner_id": "owner"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "bot_id": "bot-1",
            "owner_id": "owner",
            "changed": True,
            "error": None,
        },
    }
    assert service.bot_calls == [{"bot_id": "bot-1", "owner_id": "owner"}]


@pytest.mark.unit
def test_bot_backfill_404s_an_unknown_bot():
    service = _Service(raises=LocalSkillNotFoundError())
    response = _client(service).post(
        "/api/internal/skill-center/installations/backfill/bot",
        json={"bot_id": "nope", "owner_id": "owner"},
    )

    assert response.status_code == 404


@pytest.mark.unit
def test_page_backfill_reports_totals_and_per_bot_outcomes():
    report = BackfillReport(
        total=120,
        page=1,
        page_size=50,
        scanned=2,
        changed=1,
        failed=1,
        outcomes=(
            BotBackfillOutcome(bot_id="bot-1", owner_id="owner", changed=True),
            BotBackfillOutcome(
                bot_id="bot-2", owner_id="owner", changed=False, error="boom"
            ),
        ),
    )
    response = _client(_Service(report=report)).post(
        "/api/internal/skill-center/installations/backfill/page",
        json={"page": 1, "page_size": 50},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["total"], data["scanned"], data["changed"], data["failed"]) == (
        120,
        2,
        1,
        1,
    )
    assert data["has_more"] is True
    assert data["outcomes"][1] == {
        "bot_id": "bot-2",
        "owner_id": "owner",
        "changed": False,
        "error": "boom",
    }


@pytest.mark.unit
def test_page_backfill_defaults_to_an_unfiltered_first_page():
    service = _Service()
    _client(service).post(
        "/api/internal/skill-center/installations/backfill/page", json={}
    )

    assert service.page_calls == [
        {"owner_id": None, "engine_type": None, "page": 1, "page_size": 50}
    ]


@pytest.mark.unit
def test_page_backfill_forwards_its_filters():
    service = _Service()
    _client(service).post(
        "/api/internal/skill-center/installations/backfill/page",
        json={
            "owner_id": "owner",
            "engine_type": "openclaw",
            "page": 3,
            "page_size": 10,
        },
    )

    assert service.page_calls == [
        {
            "owner_id": "owner",
            "engine_type": "openclaw",
            "page": 3,
            "page_size": 10,
        }
    ]


@pytest.mark.unit
@pytest.mark.parametrize("page_size", [0, 201])
def test_page_backfill_rejects_an_out_of_range_page_size(page_size: int):
    service = _Service()
    response = _client(service).post(
        "/api/internal/skill-center/installations/backfill/page",
        json={"page_size": page_size},
    )

    assert response.status_code == 422
    assert service.page_calls == []


@pytest.mark.unit
def test_a_request_without_the_token_never_reaches_the_service():
    service = _Service()
    response = _client(service, with_auth=True).post(
        "/api/internal/skill-center/installations/backfill/page", json={}
    )

    assert response.status_code == 422  # the Header(...) is required
    assert service.page_calls == []


@pytest.mark.unit
def test_a_request_with_the_wrong_token_never_reaches_the_service():
    service = _Service()
    response = _client(service, with_auth=True).post(
        "/api/internal/skill-center/installations/backfill/page",
        json={},
        headers={"Authorization": "Bearer wrong"},
    )

    assert response.status_code == 401
    assert service.page_calls == []


@pytest.mark.unit
def test_the_configured_token_is_accepted():
    service = _Service()
    response = _client(service, with_auth=True).post(
        "/api/internal/skill-center/installations/backfill/page",
        json={},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )

    assert response.status_code == 200
    assert service.page_calls == [
        {"owner_id": None, "engine_type": None, "page": 1, "page_size": 50}
    ]

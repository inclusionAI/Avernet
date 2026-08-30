"""Public HTTP contract for Space Skill Publication resources."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.spaces.publication_routes import (
    router,
)
from agentclaw.community.api.space_skill_publication_service import (
    SpaceSkillPublicationServiceProtocol,
)
from agentclaw.community.core.skill_center.publication_contract import (
    PublicationAttemptRecord,
    PublicationAttemptStatus,
    PublicationImpactItem,
    PublicationRecovery,
    PublicationRecoveryKind,
    PublicationRecoveryState,
    PublicationRetryResult,
)
from agentclaw.community.core.skill_center.errors import (
    PublicationAttemptNotFoundError,
    PublicationInProgressError,
    PublicationRecoveryNotAvailableError,
    PublicationRequiresNewAttemptError,
    PublicationResultUnknownError,
    PublicationTaskUnavailableError,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)


def _attempt(*, status=PublicationAttemptStatus.PREPARING):
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    return PublicationAttemptRecord(
        attempt_id=71,
        skill_id=11,
        target_version=2,
        status=status,
        sc_version_number="2.0.0",
        recovery=PublicationRecovery(
            PublicationRecoveryState.AUTO_RETRYING
            if status is not PublicationAttemptStatus.SUCCEEDED
            else PublicationRecoveryState.NOT_AVAILABLE,
            PublicationRecoveryKind.PREPARATION
            if status is not PublicationAttemptStatus.SUCCEEDED
            else None,
        ),
        error_code=None,
        error_message=None,
        skill_version_id=None,
        created_by="owner-1",
        gmt_created=now,
        gmt_modified=now,
    )


@pytest.fixture
def publication_service():
    service = MagicMock()
    service.list_publication_impact.return_value = (
        1,
        [PublicationImpactItem("owner-1", "bot-1", "Risk Bot")],
    )
    service.create_publication.return_value = _attempt()
    service.list_publications.return_value = (1, [_attempt()])
    service.get_publication.return_value = _attempt()
    service.retry_publication.return_value = PublicationRetryResult(
        _attempt(), task_required=True
    )
    return service


@pytest.fixture
def client(publication_service):
    class _Bindings(Module):
        def configure(self, binder):
            binder.bind(SpaceSkillPublicationServiceProtocol, to=publication_service)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "owner-1"}
    attach_injector(app, Injector([_Bindings()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, "owner-1")


def test_publication_routes_publish_impact_create_collection_and_detail(
    client, publication_service
) -> None:
    impact = client.get(
        "/openapi/v1/bots/spaces/3/skills/11/publication-impact?page=1&page_size=20"
    )
    created = client.post(
        "/openapi/v1/bots/spaces/3/skills/11/publications",
        headers={"Idempotency-Key": "publish-71"},
    )
    collection = client.get(
        "/openapi/v1/bots/spaces/3/skills/11/publications?page=1&page_size=20"
    )
    detail = client.get("/openapi/v1/bots/spaces/3/skills/11/publications/71")

    assert impact.status_code == 200
    assert impact.json()["data"] == {
        "total": 1,
        "items": [{"owner_id": "owner-1", "bot_id": "bot-1", "bot_name": "Risk Bot"}],
    }
    assert created.status_code == 202
    assert created.json()["code"] == 202000
    assert created.json()["data"]["attempt_id"] == "71"
    assert created.json()["data"]["status"] == "PREPARING"
    assert collection.json()["data"]["total"] == 1
    assert detail.json()["data"]["sc_version_number"] == "2.0.0"
    publication_service.create_publication.assert_called_once_with(
        space_id=3,
        skill_id=11,
        actor_id="owner-1",
        request_id="publish-71",
    )


def test_publication_retry_returns_202_or_idempotent_200(
    client, publication_service
) -> None:
    queued = client.post("/openapi/v1/bots/spaces/3/skills/11/publications/71/retry")
    publication_service.retry_publication.return_value = PublicationRetryResult(
        _attempt(status=PublicationAttemptStatus.SUCCEEDED), task_required=False
    )
    succeeded = client.post("/openapi/v1/bots/spaces/3/skills/11/publications/71/retry")

    assert queued.status_code == 202
    assert queued.json()["code"] == 202000
    assert succeeded.status_code == 200
    assert succeeded.json()["code"] == 200000


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (PublicationAttemptNotFoundError(), 404, 404205),
        (PublicationInProgressError(), 409, 409309),
        (PublicationResultUnknownError(), 409, 409310),
        (PublicationRecoveryNotAvailableError(), 409, 409311),
        (PublicationRequiresNewAttemptError(), 409, 409315),
        (PublicationTaskUnavailableError(), 503, 503203),
    ],
)
def test_publication_routes_return_stable_error_codes(
    client, publication_service, error, status, code
) -> None:
    publication_service.create_publication.side_effect = error

    response = client.post(
        "/openapi/v1/bots/spaces/3/skills/11/publications",
        headers={"Idempotency-Key": "publish-error"},
    )

    assert response.status_code == status
    assert response.json()["code"] == code

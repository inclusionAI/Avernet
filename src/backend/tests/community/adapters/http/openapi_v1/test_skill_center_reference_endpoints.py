"""HTTP contract for persistent SC Public Reference operations."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.skill_sets.skill_center_references import (
    router,
)
from agentclaw.community.api.skill_center_reference_service import (
    SkillCenterReferenceServiceProtocol,
)
from agentclaw.community.core.skill_center.reference_contract import (
    ReferenceValidationError,
    SkillCenterReferenceBatch,
    SkillCenterReferenceItem,
    SkillCenterReferencePage,
    SkillCenterReferenceStatus,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    bind_bot_access_seam,
    user_scoped_client,
)


def _item() -> SkillCenterReferenceItem:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    return SkillCenterReferenceItem(
        reference_id="reference-a",
        request_id="request-a",
        skill_set_id="42",
        skill_code="public-a",
        sc_version_number=None,
        status=SkillCenterReferenceStatus.QUEUED,
        skill_id=None,
        error_code=None,
        error_message=None,
        gmt_created=now,
        gmt_modified=now,
    )


class _Service:
    def __init__(self) -> None:
        self.created = None
        self.listed = None
        self.gotten = None

    def create(self, **kwargs):
        self.created = kwargs
        return SkillCenterReferenceBatch(
            request_id="request-a",
            bot_id=kwargs["bot_id"],
            owner_id=kwargs["owner_id"],
            skill_set_id=kwargs["skill_set_id"],
            actor_id=kwargs["actor_id"],
            items=(_item(),),
        )

    def list(self, **kwargs):
        self.listed = kwargs
        return SkillCenterReferencePage(total=1, items=(_item(),))

    def get(self, **kwargs):
        self.gotten = kwargs
        return _item()


def _client(service: _Service):
    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillCenterReferenceServiceProtocol, to=service)
            bind_bot_access_seam(binder)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "actor"}
    attach_injector(app, Injector([Bindings()]))
    return user_scoped_client(app, "actor"), service


def test_create_returns_accepted_persistent_batch_identity() -> None:
    client, service = _client(_Service())

    response = client.post(
        "/openapi/v1/bots/bot-a/skill-sets/42/skill-center-references",
        headers={"Idempotency-Key": "command-key"},
        json={"skill_codes": ["public-a"]},
    )

    assert response.status_code == 202
    assert response.json()["data"] == {
        "request_id": "request-a",
        "reference_ids": ["reference-a"],
    }
    assert service.created == {
        "bot_id": "bot-a",
        "owner_id": "actor",
        "actor_id": "actor",
        "skill_set_id": "42",
        "idempotency_key": "command-key",
        "skill_codes": ("public-a",),
    }


def test_collection_and_detail_use_frozen_bot_skill_set_scope() -> None:
    client, service = _client(_Service())

    collection = client.get(
        "/openapi/v1/bots/bot-a/skill-sets/42/skill-center-references",
        params={"request_id": "request-a", "status": "QUEUED", "page": 1},
    )
    detail = client.get(
        "/openapi/v1/bots/bot-a/skill-sets/42/skill-center-references/reference-a"
    )

    assert collection.status_code == 200
    assert collection.json()["data"]["total"] == 1
    assert detail.status_code == 200
    assert detail.json()["data"]["reference_id"] == "reference-a"
    assert service.listed["skill_set_id"] == "42"
    assert service.gotten["reference_id"] == "reference-a"


def test_reference_validation_error_uses_stable_422_envelope() -> None:
    class _Invalid(_Service):
        def create(self, **_kwargs):
            raise ReferenceValidationError("private validation detail")

    client, _service = _client(_Invalid())

    response = client.post(
        "/openapi/v1/bots/bot-a/skill-sets/42/skill-center-references",
        headers={"Idempotency-Key": "command-key"},
        json={"skill_codes": [" padded "]},
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422000
    assert response.json()["message"] == "Invalid Reference request"

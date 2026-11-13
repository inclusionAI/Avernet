"""Unit guards for service-Bot evaluation context resolution and authorization."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationConflictError,
    ServicePublicationNotFoundError,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services import service_bot_eval_service
from agentclaw.community.core.service_bot.services.service_bot_eval_service import (
    ServiceBotEvalService,
)

NOW = datetime(2026, 11, 9, 12, 0, 0)


class FixedBotRepository:
    """Only the legacy locator resolution used by the service under test."""

    def __init__(self, bot: dict) -> None:
        self._bot = bot

    def list_matches_by_legacy_locator(self, bot_id: str, owner_id: str) -> list[dict]:
        assert bot_id == self._bot["bot_id"]
        assert owner_id == self._bot["owner_id"]
        return [self._bot]


def record(version: int = 1, status: PublishStatus = PublishStatus.DRAFT) -> BotPublishRecord:
    return BotPublishRecord(
        id=version,
        source_bot_pk=1,
        source_bot_id="service-eval",
        publish_bot_id="service-eval",
        name="Service evaluation Bot",
        owner_id="owner",
        permission_owner="owner",
        status=status.value,
        version=version,
        env="dev",
        gmt_create=NOW,
        gmt_modified=NOW,
    )


def service(bot: dict, records: list[BotPublishRecord] | None = None) -> ServiceBotEvalService:
    publish_repo = Mock()
    publish_repo.list_by_source_bot.return_value = records or []
    return ServiceBotEvalService(
        bot_repo=FixedBotRepository(bot),
        publish_repo=publish_repo,
        collaborator_service=Mock(),
        plugin=AsyncMock(),
    )


def bot(**overrides: object) -> dict:
    values: dict = {
        "id": 1,
        "bot_id": "service-eval",
        "owner_id": "owner",
        "entity_id": "owner",
        "bot_type": "service",
        "gmt_modified": NOW,
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("modified", "expected"),
    [
        (datetime(2026, 11, 9, 8, 30, 5), "2026-11-09T08:30:05"),
        (date(2026, 11, 9), "2026-11-09"),
    ],
)
def test_eval_context_serializes_datetime_and_date_modified(
    monkeypatch, modified, expected
):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc = service(bot(gmt_modified=modified), [record()])

    _, release, context = svc._resolve(
        "service-eval",
        "1",
        actor_id="owner",
        owner_id="owner",
        required_level=PermissionLevel.MEMBER,
        require_draft=True,
    )

    assert release.version == 1
    assert context.bot_id == "service-eval"
    assert context.latest_bot_modified_at == expected


def test_eval_context_rejects_actor_without_permission(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    collaborator_service = Mock()
    collaborator_service.get_permission_level.return_value = PermissionLevel.NONE
    svc = ServiceBotEvalService(
        bot_repo=FixedBotRepository(bot()),
        publish_repo=Mock(),
        collaborator_service=collaborator_service,
        plugin=AsyncMock(),
    )

    with pytest.raises(ServicePublicationNotFoundError, match="bot not found"):
        svc._resolve(
            "service-eval",
            "1",
            actor_id="member",
            owner_id="owner",
            required_level=PermissionLevel.MEMBER,
        )


def test_eval_context_rejects_non_service_bot(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc = service(bot(bot_type="personal"), [record()])

    with pytest.raises(ServicePublicationConflictError, match="not a service bot"):
        svc._resolve(
            "service-eval",
            "1",
            actor_id="owner",
            owner_id="owner",
            required_level=PermissionLevel.MEMBER,
        )


def test_eval_context_rejects_missing_requested_version(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc = service(bot(), [record(version=2)])

    with pytest.raises(ServicePublicationNotFoundError, match="publication not found"):
        svc._resolve(
            "service-eval",
            "3",
            actor_id="owner",
            owner_id="owner",
            required_level=PermissionLevel.MEMBER,
        )


def test_eval_context_rejects_service_bot_without_publication(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc = service(bot())

    with pytest.raises(ServicePublicationConflictError, match="no actionable publication"):
        svc._resolve(
            "service-eval",
            "latest",
            actor_id="owner",
            owner_id="owner",
            required_level=PermissionLevel.MEMBER,
            latest_when_missing=True,
        )


def test_precheck_rejects_non_draft_publication(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc = service(bot(), [record(status=PublishStatus.SUCCESS)])

    with pytest.raises(ServicePublicationConflictError, match="not a draft"):
        svc._resolve(
            "service-eval",
            "1",
            actor_id="owner",
            owner_id="owner",
            required_level=PermissionLevel.MEMBER,
            require_draft=True,
        )

"""Application behavior for Publication API orchestration."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from agentclaw.community.core.skill_center.errors import PublicationTaskUnavailableError
from agentclaw.community.core.skill_center.publication_contract import (
    PublicationAttemptCreation,
    PublicationAttemptRecord,
    PublicationAttemptStatus,
    PublicationImpactCandidate,
    PublicationRecovery,
    PublicationRecoveryKind,
    PublicationRecoveryState,
)
from agentclaw.community.core.skill_center.services.space_skill_publication_service import (
    SpaceSkillPublicationService,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset


def _attempt() -> PublicationAttemptRecord:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    return PublicationAttemptRecord(
        attempt_id=71,
        skill_id=11,
        frozen_draft_locator="draft://00000000-0000-4000-8000-000000000011/v2/00000000-0000-4000-8000-000000000012",
        target_version=2,
        status=PublicationAttemptStatus.PREPARING,
        sc_version_number="2.0.0",
        recovery=PublicationRecovery(
            PublicationRecoveryState.AUTO_RETRYING,
            PublicationRecoveryKind.PREPARATION,
        ),
        error_code=None,
        error_message=None,
        skill_version_id=None,
        created_by="owner",
        gmt_created=now,
        gmt_modified=now,
    )


class _Access:
    def __init__(self) -> None:
        self.calls = []

    def require_space_member(self, **kwargs):
        self.calls.append(kwargs)
        return object(), object()


class _Repository:
    def __init__(self) -> None:
        self.attempt = _attempt()
        self.create_calls = 0
        self.candidates = ()

    def create_or_replay_attempt(self, **_kwargs):
        self.create_calls += 1
        return PublicationAttemptCreation(self.attempt, created=self.create_calls == 1)

    def require_publisher(self, **_kwargs):
        return None

    def list_impact_candidates(self, **_kwargs):
        return self.candidates


class _Queue:
    def __init__(self) -> None:
        self.calls = []
        self.fail = True

    def enqueue(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.fail:
            raise RuntimeError("queue unavailable")
        return object()


class _Reader:
    def active_skill_assets(self, **_kwargs):
        return (
            RegisteredSkillAsset(
                skill_id=11,
                name="risk-review",
                git_path="center://uuid",
                skill_uuid="uuid",
                sc_version_number="1.0.0",
            ),
        )


def _service(repository: _Repository, queue: _Queue) -> SpaceSkillPublicationService:
    return SpaceSkillPublicationService(
        access=_Access(),
        repository=repository,
        capability_reader=_Reader(),
        task_queue=queue,
        env_provider=lambda: "test",
    )


def test_enqueue_failure_keeps_attempt_and_same_key_replay_ensures_task() -> None:
    repository = _Repository()
    queue = _Queue()
    service = _service(repository, queue)

    with pytest.raises(PublicationTaskUnavailableError):
        service.create_publication(
            space_id=3,
            skill_id=11,
            actor_id="owner",
            request_id="publish-71",
        )

    queue.fail = False
    replay = service.create_publication(
        space_id=3,
        skill_id=11,
        actor_id="owner",
        request_id="publish-71",
    )

    assert replay.attempt_id == 71
    assert repository.create_calls == 2
    assert len(queue.calls) == 2
    assert queue.calls[0][1]["idempotency_key"] == "skill-publication:teamclaw:71"

    repository.attempt = replace(
        _attempt(),
        status=PublicationAttemptStatus.SUCCEEDED,
        recovery=PublicationRecovery(
            PublicationRecoveryState.NOT_AVAILABLE,
            None,
        ),
    )
    terminal_replay = service.create_publication(
        space_id=3,
        skill_id=11,
        actor_id="owner",
        request_id="publish-71",
    )
    assert terminal_replay.status == "SUCCEEDED"
    assert len(queue.calls) == 2


def test_publication_impact_confirms_candidates_through_capability_reader() -> None:
    repository = _Repository()
    repository.candidates = (
        PublicationImpactCandidate(
            owner_id="owner",
            bot_id="bot-1",
            bot_name="Risk Bot",
            bot={"owner_id": "owner", "bot_id": "bot-1", "env": "test"},
        ),
    )
    service = _service(repository, _Queue())

    total, items = service.list_publication_impact(
        space_id=3,
        skill_id=11,
        actor_id="owner",
        page=1,
        page_size=20,
    )

    assert total == 1
    assert items[0].bot_id == "bot-1"
    assert items[0].bot_name == "Risk Bot"

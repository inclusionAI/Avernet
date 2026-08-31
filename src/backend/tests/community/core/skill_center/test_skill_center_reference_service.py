"""Application contract for durable SC Public Reference acceptance."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentclaw.community.core.skill_center.reference_contract import (
    ReferenceBatchSizeError,
    SkillCenterReferenceBatch,
    SkillCenterReferenceCreateResult,
    SkillCenterReferenceItem,
    SkillCenterReferenceStatus,
)
from agentclaw.community.core.skill_center.services.skill_center_reference_service import (
    SKILL_CENTER_REFERENCE_TASK,
    SkillCenterReferenceService,
)


def _item(reference_id: str, request_id: str, code: str) -> SkillCenterReferenceItem:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    return SkillCenterReferenceItem(
        reference_id=reference_id,
        request_id=request_id,
        skill_set_id="42",
        skill_code=code,
        sc_version_number=None,
        status=SkillCenterReferenceStatus.QUEUED,
        skill_id=None,
        error_code=None,
        error_message=None,
        gmt_created=now,
        gmt_modified=now,
    )


class _References:
    def __init__(self, *, created: bool) -> None:
        self.created = created
        self.calls: list[dict] = []

    def get_batch_by_idempotency_key(self, *, env, idempotency_key):
        if self.created:
            return None
        kwargs = {
            "bot_id": "bot-a",
            "owner_id": "owner-a",
            "skill_set_id": "42",
            "actor_id": "actor-a",
            "skill_codes": ("public-a", "public-b"),
        }
        request_hash = SkillCenterReferenceService._request_hash(**kwargs)
        return (
            SkillCenterReferenceBatch(
                request_id="request-existing",
                bot_id="bot-a",
                owner_id="owner-a",
                skill_set_id="42",
                actor_id="actor-a",
                items=(
                    _item("reference-1", "request-existing", "public-a"),
                    _item("reference-2", "request-existing", "public-b"),
                ),
            ),
            request_hash,
        )

    def create_or_get_batch(self, **kwargs):
        self.calls.append(kwargs)
        request_id = "request-new" if self.created else "request-existing"
        return SkillCenterReferenceCreateResult(
            batch=SkillCenterReferenceBatch(
                request_id=request_id,
                bot_id=kwargs["bot_id"],
                owner_id=kwargs["owner_id"],
                skill_set_id=kwargs["skill_set_id"],
                actor_id=kwargs["actor_id"],
                items=tuple(
                    _item(f"reference-{index}", request_id, code)
                    for index, code in enumerate(kwargs["skill_codes"], start=1)
                ),
            ),
            created=self.created,
        )


class _SkillSets:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_set(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": kwargs["set_id"], "is_active": True, "is_default": False}


class _Tasks:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
        self.calls.append(
            {
                "task_type": task_type,
                "payload": payload,
                "deadline_seconds": deadline_seconds,
                **kwargs,
            }
        )
        return object()


@pytest.mark.parametrize("created", [True, False])
def test_acceptance_deduplicates_codes_and_always_ensures_the_batch_task(
    created: bool,
) -> None:
    references = _References(created=created)
    skill_sets = _SkillSets()
    tasks = _Tasks()
    service = SkillCenterReferenceService(
        references=references,
        skill_sets=skill_sets,
        tasks=tasks,
        env_provider=lambda: "pre",
    )

    batch = service.create(
        bot_id="bot-a",
        owner_id="owner-a",
        actor_id="actor-a",
        skill_set_id="42",
        idempotency_key="request-key",
        skill_codes=("public-a", "public-a", "public-b"),
    )

    if created:
        assert references.calls[0]["skill_codes"] == ("public-a", "public-b")
    else:
        assert references.calls == []
    assert skill_sets.calls == ([
        {
            "bot_id": "bot-a",
            "owner_id": "owner-a",
            "user_id": "actor-a",
            "set_id": "42",
        }
    ] if created else [])
    assert tasks.calls == [
        {
            "task_type": SKILL_CENTER_REFERENCE_TASK,
            "payload": {"request_id": batch.request_id},
            "deadline_seconds": 30 * 60,
            "idempotency_key": f"skill-center-reference:{batch.request_id}",
        }
    ]


def test_acceptance_rejects_more_than_twenty_unique_codes() -> None:
    service = SkillCenterReferenceService(
        references=_References(created=True),
        skill_sets=_SkillSets(),
        tasks=_Tasks(),
        env_provider=lambda: "pre",
    )

    with pytest.raises(ReferenceBatchSizeError):
        service.create(
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="actor-a",
            skill_set_id="42",
            idempotency_key="request-key",
            skill_codes=tuple(f"public-{index}" for index in range(21)),
        )

"""Worker contract for SC Public Reference materialize-then-add flow."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from agentclaw.community.core.repository.skill_center_reference_types import (
    PublicCenterVersionTarget,
    SkillCenterReferenceWorkBatch,
    SkillCenterReferenceWorkItem,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    PublishedMaterializedSkillVersion,
)
from agentclaw.community.core.skill_center.reference_contract import (
    SkillCenterReferenceStatus,
)
from agentclaw.community.core.skill_center.services.skill_center_reference_processor import (
    SkillCenterReferenceProcessor,
)
from agentclaw.community.core.skill_center.skill_set_batch import (
    SkillSetSkillOutcome,
)
from agentclaw.community.core.task_queue.types import Complete, Retry
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterAccessLevel,
    SkillCenterSkill,
    SkillCenterVersion,
)


def _work_item(code: str, reference_id: str) -> SkillCenterReferenceWorkItem:
    return SkillCenterReferenceWorkItem(
        reference_id=reference_id,
        skill_code=code,
        status=SkillCenterReferenceStatus.QUEUED,
        sc_version_number=None,
        skill_version_id=None,
        resolved_skill_id=None,
        attempt_count=0,
    )


class _References:
    def __init__(self, *, target_status="MATERIALIZING") -> None:
        self.batch = SkillCenterReferenceWorkBatch(
            request_id="request-a",
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_set_id="42",
            actor_id="actor-a",
            items=(_work_item("public-a", "ref-a"), _work_item("public-b", "ref-b")),
        )
        self.transitions: list[tuple[str, SkillCenterReferenceStatus, dict]] = []
        self._next_skill_id = 10
        self.target_status = target_status

    def get_work_batch(self, *, env, request_id):
        assert env == "pre"
        return self.batch if request_id == self.batch.request_id else None

    def update_item(self, *, env, reference_id, status, **fields):
        assert env == "pre"
        self.transitions.append((reference_id, status, fields))
        updated = []
        selected = None
        for item in self.batch.items:
            if item.reference_id == reference_id:
                selected = replace(item, status=status, **fields)
                updated.append(selected)
            else:
                updated.append(item)
        assert selected is not None
        self.batch = replace(self.batch, items=tuple(updated))
        return selected

    def ensure_public_version(self, **kwargs):
        skill_id = self._next_skill_id
        self._next_skill_id += 1
        return PublicCenterVersionTarget(
            skill_id=skill_id,
            skill_version_id=100 + skill_id,
            status=self.target_status,
        )


class _Gateway:
    def get_public_skill(self, request):
        return SkillCenterSkill(
            skill_code=request.skill_code,
            skill_name=f"name-{request.skill_code}",
            access_level=SkillCenterAccessLevel.PUBLIC,
            skill_id="9001" if request.skill_code == "public-a" else "9002",
            latest_version_number="1.0.0",
        )

    def list_versions(self, request):
        return (
            SkillCenterVersion(
                version_number="1.0.0",
                version_id="10001" if request.skill_code == "public-a" else "10002",
            ),
        )


class _Materializer:
    def __init__(self) -> None:
        self.calls = []

    def materialize(self, request):
        self.calls.append(request)
        return PublishedMaterializedSkillVersion(
            skill_version_id=request.skill_version_id,
            skill_id=request.skill_id,
            version_ordinal=1,
            status="PUBLISHED",
            skill_uuid=f"00000000-0000-4000-8000-{request.skill_id:012d}",
            sc_version_number="1.0.0",
            sc_skill_id=9000 + request.skill_id,
            sc_version_id=10000 + request.skill_version_id,
            name=f"skill-{request.skill_id}",
            description=None,
            metadata_json='{"mcp_dependencies":[]}',
            published_at=datetime(2026, 8, 30, tzinfo=UTC),
        )


class _SkillSets:
    def __init__(
        self, *, offline: bool = False, infrastructure_failure: bool = False
    ) -> None:
        self.offline = offline
        self.infrastructure_failure = infrastructure_failure
        self.add_calls: list[dict] = []
        self.membership_ids: set[str] = set()
        self.installation_ids: set[str] = set()

    def get_set(self, **_kwargs):
        return {"id": "42", "is_active": True, "is_default": False}

    async def add_skills(self, **kwargs):
        self.add_calls.append(kwargs)
        if self.offline:
            raise SkillSetControlPlaneConflictError("SKILL_OFFLINE")
        if self.infrastructure_failure:
            raise RuntimeError("database unavailable")
        self.membership_ids.update(kwargs["skill_ids"])
        self.installation_ids.update(kwargs["skill_ids"])
        return [
            SkillSetSkillOutcome(skill_id=skill_id, changed=True)
            for skill_id in kwargs["skill_ids"]
        ]


class _TrackLatest:
    def __init__(self) -> None:
        self.published = []

    def version_published(self, version) -> None:
        self.published.append(version)


def _processor(
    *,
    references: _References,
    skill_sets: _SkillSets,
    materializer,
    gateway=None,
    track_latest=None,
):
    track_latest = track_latest or _TrackLatest()
    return SkillCenterReferenceProcessor(
        references=references,
        gateway=gateway or _Gateway(),
        materializer=materializer,
        skill_sets=skill_sets,
        track_latest=track_latest,
        env_provider=lambda: "pre",
        max_concurrency=4,
    )


def test_successful_items_are_added_in_one_batch_after_materialization() -> None:
    references = _References()
    materializer = _Materializer()
    skill_sets = _SkillSets()

    outcome = asyncio.run(
        _processor(
            references=references,
            skill_sets=skill_sets,
            materializer=materializer,
        ).process("request-a")
    )

    assert isinstance(outcome, Complete)
    assert len(materializer.calls) == 2
    assert skill_sets.add_calls == [
        {
            "bot_id": "bot-a",
            "owner_id": "owner-a",
            "user_id": "actor-a",
            "set_id": "42",
            "skill_ids": ("10", "11"),
        }
    ]
    assert {item.status for item in references.batch.items} == {
        SkillCenterReferenceStatus.COMPLETED
    }


def test_concurrent_offline_after_materialization_fails_items_and_keeps_asset() -> None:
    references = _References()
    materializer = _Materializer()
    skill_sets = _SkillSets(offline=True)

    outcome = asyncio.run(
        _processor(
            references=references,
            skill_sets=skill_sets,
            materializer=materializer,
        ).process("request-a")
    )

    assert isinstance(outcome, Complete)
    assert len(materializer.calls) == 2  # shared PUBLISHED assets are retained
    assert len(skill_sets.add_calls) == 1  # final lock/check stays in formal service
    assert skill_sets.membership_ids == set()
    assert skill_sets.installation_ids == set()
    assert {item.status for item in references.batch.items} == {
        SkillCenterReferenceStatus.FAILED
    }
    failed = [
        fields
        for _reference_id, status, fields in references.transitions
        if status is SkillCenterReferenceStatus.FAILED
    ]
    assert {item["error_code"] for item in failed} == {"SKILL_OFFLINE"}


def test_one_missing_public_skill_does_not_block_successful_item() -> None:
    class _PartialGateway(_Gateway):
        def get_public_skill(self, request):
            if request.skill_code == "public-b":
                return None
            return super().get_public_skill(request)

    references = _References()
    materializer = _Materializer()
    skill_sets = _SkillSets()

    outcome = asyncio.run(
        _processor(
            references=references,
            skill_sets=skill_sets,
            materializer=materializer,
            gateway=_PartialGateway(),
        ).process("request-a")
    )

    assert isinstance(outcome, Complete)
    assert len(materializer.calls) == 1
    assert skill_sets.add_calls[0]["skill_ids"] == ("10",)
    assert [item.status for item in references.batch.items] == [
        SkillCenterReferenceStatus.COMPLETED,
        SkillCenterReferenceStatus.FAILED,
    ]


def test_reused_published_asset_reensures_level_triggered_track_latest() -> None:
    references = _References(target_status="PUBLISHED")
    track_latest = _TrackLatest()

    outcome = asyncio.run(
        _processor(
            references=references,
            skill_sets=_SkillSets(),
            materializer=_Materializer(),
            track_latest=track_latest,
        ).process("request-a")
    )

    assert isinstance(outcome, Complete)
    assert len(track_latest.published) == 2


def test_transient_sc_failure_retries_three_times_then_fails_each_item() -> None:
    class _UnavailableGateway:
        def get_public_skill(self, _request):
            from agentclaw.community.plugin_api.skill_center_gateway import (
                SkillCenterGatewayError,
                SkillCenterGatewayErrorCode,
            )

            raise SkillCenterGatewayError(
                SkillCenterGatewayErrorCode.UNAVAILABLE, "temporary outage"
            )

    references = _References()
    processor = _processor(
        references=references,
        skill_sets=_SkillSets(),
        materializer=_Materializer(),
        gateway=_UnavailableGateway(),
    )

    first = asyncio.run(processor.process("request-a"))
    second = asyncio.run(processor.process("request-a"))
    third = asyncio.run(processor.process("request-a"))

    assert isinstance(first, Retry)
    assert isinstance(second, Retry)
    assert isinstance(third, Complete)
    assert {item.status for item in references.batch.items} == {
        SkillCenterReferenceStatus.FAILED
    }
    assert {item.attempt_count for item in references.batch.items} == {3}
    assert {item.error_code for item in references.batch.items} == {
        "SC_MARKET_UNAVAILABLE"
    }


def test_final_membership_infrastructure_failure_propagates_for_task_retry() -> None:
    references = _References()
    processor = _processor(
        references=references,
        skill_sets=_SkillSets(infrastructure_failure=True),
        materializer=_Materializer(),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(processor.process("request-a"))

    assert all(
        item.status is SkillCenterReferenceStatus.PROJECTING_RUNTIME
        for item in references.batch.items
    )
    assert not any(
        status is SkillCenterReferenceStatus.FAILED
        for _reference_id, status, _fields in references.transitions
    )

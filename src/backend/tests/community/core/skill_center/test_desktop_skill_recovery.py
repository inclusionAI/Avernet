"""Desktop Skill recovery through its public ensure/handler seams."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.platform.task_queue import (
    TaskQueueRepository,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    CapabilityDesiredState,
    DesiredStateMutation,
)
from agentclaw.community.core.repository.track_latest_types import (
    PublishedTrackLatestVersion,
    TrackLatestCandidateFacts,
)
from agentclaw.community.core.repository.skill_center_reference_types import (
    PublicCenterVersionTarget,
    SkillCenterReferenceWorkBatch,
    SkillCenterReferenceWorkItem,
)
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel  # noqa: F401
from agentclaw.community.core.skill_center.center_content_distribution import (
    CenterContentPendingPackage,
    CenterContentReadyPackage,
    CenterContentUnavailablePackage,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    PublishedMaterializedSkillVersion,
)
from agentclaw.community.core.skill_center.reference_contract import (
    SkillCenterReferenceStatus,
)
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersionIdentity,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    ResolvedSkillPlan,
    RuntimeProjectionResult,
    RuntimeProjectionIssue,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.runtime_resolver import RuntimeSkillProjection
from agentclaw.community.core.skill_center.services.desktop_skill_recovery import (
    DESKTOP_SKILL_RECOVERY_DEADLINE_SECONDS,
    DESKTOP_SKILL_RECOVERY_DELAY_SECONDS,
    DesktopSkillRecoveryTaskHandler,
    DesktopSkillRecoveryService,
    DesktopSkillRecoverySweeper,
)
from agentclaw.community.core.skill_center.services.track_latest import (
    BotTrackLatestReconcileTaskHandler,
    TrackLatestFanoutTaskHandler,
)
from agentclaw.community.core.skill_center.services.skill_center_reference_processor import (
    SkillCenterReferenceProcessor,
)
from agentclaw.community.core.skill_center.services.skill_set_management_service import (
    SkillSetManagementService,
)
from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
    RegisteredSkillAsset,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayoutPhase,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.core.task_queue.services.wakeup import WorkerWakeup
from agentclaw.community.core.task_queue.types import (
    DEFAULT_APP,
    Complete,
    Reschedule,
    Retry,
)
from agentclaw.community.di.config import DesktopSkillRecoveryConfig, TaskQueueConfig
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterAccessLevel,
    SkillCenterSkill,
    SkillCenterVersion,
)


_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _bot(*, binding_id: int = 17) -> dict:
    return {
        "bot_id": "bot-a",
        "owner_id": "owner-a",
        "entity_id": "owner-a",
        "env": "dev",
        "bot_type": "desktop",
        "active_engine": "openclaw",
        "binding_id": binding_id,
    }


def _plan(version: str) -> ResolvedSkillPlan:
    mapping = PoolSkillMapping(
        corpus="center",
        relative_path=None,
        link_name="weather",
        skill_uuid=_UUID,
        sc_version_number=version,
    )
    return ResolvedSkillPlan(
        bot_id="bot-a",
        owner_id="owner-a",
        bot=_bot(),
        engine="openclaw",
        projection=RuntimeSkillProjection(
            skill_mappings=(mapping,),
            skill_assets=(),
        ),
    )


def _empty_plan() -> ResolvedSkillPlan:
    return ResolvedSkillPlan(
        bot_id="bot-a",
        owner_id="owner-a",
        bot=_bot(),
        engine="openclaw",
        projection=RuntimeSkillProjection(skill_mappings=(), skill_assets=()),
    )


def _combined_plan() -> ResolvedSkillPlan:
    return replace(
        _plan("3"),
        projection=RuntimeSkillProjection(
            skill_mappings=(
                _plan("3").projection.skill_mappings[0],
                replace(
                    _plan("7").projection.skill_mappings[0],
                    link_name="calculator",
                    skill_uuid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                ),
            ),
            skill_assets=(),
        ),
    )


class _Projector:
    def __init__(self) -> None:
        self.plans = [_plan("1"), _plan("2")]
        self.applied: list[ResolvedSkillPlan] = []
        self.retired: list[tuple[PoolSkillMapping, ...]] = []

    def resolve_plan(self, **_kwargs) -> ResolvedSkillPlan:
        return self.plans.pop(0)

    async def apply_plan(self, *, plan, retired_mappings, scope):
        assert scope == ProjectionScope(skills=True)
        self.applied.append(plan)
        self.retired.append(tuple(retired_mappings))
        return RuntimeProjectionResult.converged(
            components={"skills": RuntimeProjectionStatus.CONVERGED}
        )


class _Distribution:
    def __init__(self) -> None:
        self.prepared: list[CanonicalCenterVersionIdentity] = []

    def prepare(self, identity):
        self.prepared.append(identity)
        return CenterContentReadyPackage(
            identity,
            package_sha256="a" * 64,
            package_size=1,
            signed_url="https://objects.example/package.zip",
            expires_at="2026-09-10T01:00:00Z",
        )


def _handler(*, projector=None, distribution=None, layout_state=None):
    bots = MagicMock()
    bots.get_by_id_and_owner.side_effect = [_bot(), _bot()]
    layouts = MagicMock()
    layouts.get.return_value = layout_state or BotSkillLayoutState.legacy_default(
        BotSkillLayoutScope(env="dev", entity_id="owner-a", bot_id="bot-a")
    )
    return DesktopSkillRecoveryTaskHandler(
        bots=bots,
        projector=projector or _Projector(),
        distribution=distribution or _Distribution(),
        layouts=layouts,
        env_provider=lambda: "dev",
    )


def test_handler_prepares_cold_center_then_rereads_latest_plan_before_apply() -> None:
    projector = _Projector()
    distribution = _Distribution()
    handler = _handler(projector=projector, distribution=distribution)

    outcome = handler.handle({"owner_id": "owner-a", "bot_id": "bot-a"})

    assert isinstance(outcome, Complete)
    assert distribution.prepared == [
        CanonicalCenterVersionIdentity(_UUID, "1")
    ]
    assert [
        plan.projection.skill_mappings[0].sc_version_number
        for plan in projector.applied
    ] == ["2"]


def test_late_cache_does_not_reactivate_a_skill_removed_during_prepare() -> None:
    projector = _Projector()
    projector.plans = [_plan("1"), _empty_plan()]
    handler = _handler(projector=projector)

    outcome = handler.handle({"owner_id": "owner-a", "bot_id": "bot-a"})

    assert isinstance(outcome, Complete)
    assert projector.applied[0].projection.skill_mappings == ()
    assert projector.retired == [(_plan("1").projection.skill_mappings[0],)]


def test_stale_prepare_wait_does_not_keep_removed_skill_task_alive() -> None:
    projector = _Projector()
    projector.plans = [_plan("1"), _empty_plan()]
    distribution = MagicMock()
    distribution.prepare.side_effect = lambda identity: CenterContentPendingPackage(
        identity
    )

    outcome = _handler(
        projector=projector,
        distribution=distribution,
    ).handle({"owner_id": "owner-a", "bot_id": "bot-a"})

    assert isinstance(outcome, Complete)


def test_handler_uses_five_second_reschedule_for_normal_download_wait() -> None:
    projector = _Projector()

    async def pending(*, plan, retired_mappings, scope):
        return RuntimeProjectionResult.pending(
            code="CENTER_CONTENT_DOWNLOAD_PENDING",
            reason="download is in progress",
        )

    projector.apply_plan = pending
    outcome = _handler(projector=projector).handle(
        {"owner_id": "owner-a", "bot_id": "bot-a"}
    )

    assert isinstance(outcome, Reschedule)
    assert outcome.delay_seconds == DESKTOP_SKILL_RECOVERY_DELAY_SECONDS


def test_handler_retries_transient_network_failure_instead_of_fast_polling() -> None:
    projector = _Projector()
    projector.apply_plan = AsyncMock(
        return_value=RuntimeProjectionResult.pending(
            code="CENTER_CONTENT_DOWNLOAD_FAILED",
            reason="network unavailable",
        )
    )

    outcome = _handler(projector=projector).handle(
        {"owner_id": "owner-a", "bot_id": "bot-a"}
    )

    assert isinstance(outcome, Retry)


def test_permanent_prepare_failure_stops_derived_pending_fast_poll(caplog) -> None:
    projector = _Projector()
    projector.plans = [_plan("1"), _plan("1")]
    projector.apply_plan = AsyncMock(
        return_value=RuntimeProjectionResult(
            status=RuntimeProjectionStatus.PENDING,
            components={"skills": RuntimeProjectionStatus.PENDING},
            issues=(
                RuntimeProjectionIssue(
                    resource_type="SKILL",
                    code="CENTER_CONTENT_PACKAGE_PENDING",
                    reason="descriptor is absent",
                    status=RuntimeProjectionStatus.PENDING,
                    retryable=True,
                    name="weather",
                    corpus="CENTER",
                ),
            ),
        )
    )
    distribution = MagicMock()
    distribution.prepare.side_effect = lambda identity: (
        CenterContentUnavailablePackage(
            identity,
            code="CENTER_CONTENT_PACKAGE_CONFLICT",
            retryable=False,
        )
    )

    outcome = _handler(
        projector=projector, distribution=distribution
    ).handle({"owner_id": "owner-a", "bot_id": "bot-a"})

    assert isinstance(outcome, Complete)
    assert "CENTER_CONTENT_PACKAGE_CONFLICT" in caplog.text


def test_permanent_prepare_failure_does_not_hide_other_recoverable_item(
    caplog,
) -> None:
    projector = _Projector()
    projector.plans = [_combined_plan(), _combined_plan()]
    projector.apply_plan = AsyncMock(
        return_value=RuntimeProjectionResult(
            status=RuntimeProjectionStatus.PENDING,
            components={"skills": RuntimeProjectionStatus.PENDING},
            issues=(
                RuntimeProjectionIssue(
                    resource_type="SKILL",
                    code="CENTER_CONTENT_PACKAGE_PENDING",
                    reason="descriptor is absent",
                    status=RuntimeProjectionStatus.PENDING,
                    retryable=True,
                    name="weather",
                    corpus="CENTER",
                ),
                RuntimeProjectionIssue(
                    resource_type="SKILL",
                    code="CENTER_CONTENT_DOWNLOAD_PENDING",
                    reason="download is active",
                    status=RuntimeProjectionStatus.PENDING,
                    retryable=True,
                    name="calculator",
                    corpus="CENTER",
                ),
            ),
        )
    )
    distribution = MagicMock()

    def prepare(identity):
        if identity.skill_uuid == _UUID:
            return CenterContentUnavailablePackage(
                identity,
                code="CENTER_CONTENT_PACKAGE_CONFLICT",
                retryable=False,
            )
        return CenterContentPendingPackage(identity)

    distribution.prepare.side_effect = prepare

    outcome = _handler(
        projector=projector, distribution=distribution
    ).handle({"owner_id": "owner-a", "bot_id": "bot-a"})

    assert isinstance(outcome, Reschedule)
    assert "CENTER_CONTENT_PACKAGE_CONFLICT" in caplog.text


def test_handler_keeps_fast_polling_recoverable_item_beside_permanent_issue() -> None:
    projector = _Projector()
    projector.apply_plan = AsyncMock(
        return_value=RuntimeProjectionResult(
            status=RuntimeProjectionStatus.DEGRADED,
            components={"skills": RuntimeProjectionStatus.DEGRADED},
            issues=(
                RuntimeProjectionIssue(
                    resource_type="SKILL",
                    code="CENTER_CONTENT_CACHE_CONFLICT",
                    reason="permanent",
                    status=RuntimeProjectionStatus.DEGRADED,
                    retryable=False,
                ),
                RuntimeProjectionIssue(
                    resource_type="SKILL",
                    code="CENTER_CONTENT_DOWNLOAD_CAPACITY",
                    reason="waiting",
                    status=RuntimeProjectionStatus.PENDING,
                    retryable=True,
                ),
            ),
        )
    )

    outcome = _handler(projector=projector).handle(
        {"owner_id": "owner-a", "bot_id": "bot-a"}
    )

    assert isinstance(outcome, Reschedule)


def test_handler_does_not_write_mappings_during_pool_transition() -> None:
    projector = _Projector()
    transition = replace(
        BotSkillLayoutState.legacy_default(
            BotSkillLayoutScope(env="dev", entity_id="owner-a", bot_id="bot-a")
        ),
        phase=SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER,
    )

    outcome = _handler(projector=projector, layout_state=transition).handle(
        {"owner_id": "owner-a", "bot_id": "bot-a"}
    )

    assert isinstance(outcome, Reschedule)
    assert projector.applied == []


class _SqliteDB:
    def __init__(self, engine) -> None:
        self._sessions = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@pytest.fixture
def recovery_queue(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        "agentclaw.community.core.task_queue.services.task_queue_service.get_current_env",
        lambda: "dev",
    )
    return TaskQueueService(
        TaskQueueRepository(_SqliteDB(engine)),
        HandlerRegistry(),
        WorkerWakeup(),
        TaskQueueConfig(),
    )


class _ReferenceFacts:
    def __init__(self) -> None:
        self.batch = SkillCenterReferenceWorkBatch(
            request_id="reference-a",
            env="dev",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_set_id="42",
            actor_id="owner-a",
            items=(
                SkillCenterReferenceWorkItem(
                    reference_id="ref-a",
                    skill_code="public-weather",
                    status=SkillCenterReferenceStatus.QUEUED,
                    sc_version_number=None,
                    skill_version_id=None,
                    resolved_skill_id=None,
                    attempt_count=0,
                ),
            ),
        )

    def get_work_batch(self, *, env, request_id):
        return self.batch if (env, request_id) == ("dev", "reference-a") else None

    def update_item(self, *, env, reference_id, status, **fields):
        assert (env, reference_id) == ("dev", "ref-a")
        item = replace(self.batch.items[0], status=status, **fields)
        self.batch = replace(self.batch, items=(item,))
        return item

    def ensure_public_version(self, **_kwargs):
        return PublicCenterVersionTarget(
            skill_id=10, skill_version_id=103, status="MATERIALIZING"
        )


class _ReferenceGateway:
    def get_public_skill(self, request):
        assert request.skill_code == "public-weather"
        return SkillCenterSkill(
            skill_code="public-weather",
            skill_name="weather",
            access_level=SkillCenterAccessLevel.PUBLIC,
            skill_id="9001",
            latest_version_number="3",
        )

    def list_versions(self, _request):
        return (SkillCenterVersion(version_number="3", version_id="10003"),)


class _ReferenceMaterializer:
    def materialize(self, request):
        return PublishedMaterializedSkillVersion(
            skill_version_id=request.skill_version_id,
            skill_id=request.skill_id,
            version_ordinal=3,
            status="PUBLISHED",
            skill_uuid=_UUID,
            sc_version_number="3",
            sc_skill_id=9001,
            sc_version_id=10003,
            name="weather",
            description=None,
            metadata_json='{"mcp_dependencies":[]}',
            published_at=datetime(2026, 9, 10, tzinfo=UTC),
        )


@pytest.mark.integration
def test_track_latest_pending_enters_durable_recovery_and_applies_latest_plan(
    recovery_queue,
) -> None:
    bots = MagicMock()
    bots.get_by_id_and_owner.return_value = _bot()
    recovery = DesktopSkillRecoveryService(bots=bots, tasks=recovery_queue)
    mapping = _plan("3").projection.skill_mappings[0]

    class _VerticalProjector:
        def __init__(self) -> None:
            self.applied: list[ResolvedSkillPlan] = []

        async def snapshot_skill_mappings(self, **_kwargs):
            return (mapping,)

        async def project(self, **_kwargs):
            return RuntimeProjectionResult.pending(
                code="CENTER_CONTENT_PACKAGE_PENDING",
                reason="package is not cached yet",
            )

        def resolve_plan(self, **_kwargs):
            return _plan("3")

        async def apply_plan(self, *, plan, retired_mappings, scope):
            assert retired_mappings == ()
            assert scope == ProjectionScope(skills=True)
            self.applied.append(plan)
            return RuntimeProjectionResult.converged(
                components={"skills": RuntimeProjectionStatus.CONVERGED}
            )

    projector = _VerticalProjector()
    reader = MagicMock()
    reader.active_skill_assets.return_value = (
        RegisteredSkillAsset(
            skill_id=10,
            name="weather",
            git_path="center://public-weather",
            skill_uuid=_UUID,
            sc_version_number="3",
        ),
    )
    latest = MagicMock()
    latest.list_published_versions.return_value = (
        PublishedTrackLatestVersion(
            skill_version_id=103,
            metadata_json='{"mcp_dependencies":[]}',
        ),
    )
    foreground = BotTrackLatestReconcileTaskHandler(
        reader=reader,
        projector=projector,
        latest=latest,
        recovery=recovery,
        env_provider=lambda: "dev",
    )

    foreground_outcome = foreground.handle(
        {"owner_id": "owner-a", "bot_id": "bot-a", "skill_id": 10}
    )
    assert isinstance(foreground_outcome, Complete), foreground_outcome
    claimed = recovery_queue._repo.claim_batch(
        worker_id="worker-1",
        env="dev",
        app=DEFAULT_APP,
        limit=1,
        lease_seconds=60,
    )
    distribution = _Distribution()
    layouts = MagicMock()
    layouts.get.return_value = BotSkillLayoutState.legacy_default(
        BotSkillLayoutScope(env="dev", entity_id="owner-a", bot_id="bot-a")
    )
    recovery_handler = DesktopSkillRecoveryTaskHandler(
        bots=bots,
        projector=projector,
        distribution=distribution,
        layouts=layouts,
        env_provider=lambda: "dev",
    )

    recovery_outcome = recovery_handler.handle(claimed[0].payload)

    assert claimed[0].payload == {"owner_id": "owner-a", "bot_id": "bot-a"}
    assert isinstance(recovery_outcome, Complete)
    assert distribution.prepared == [CanonicalCenterVersionIdentity(_UUID, "3")]
    assert projector.applied[0].projection.skill_mappings == (mapping,)


@pytest.mark.integration
def test_reference_set_and_track_latest_share_one_task_for_latest_combined_plan(
    recovery_queue,
) -> None:
    bots = MagicMock()
    bots.get_by_id_and_owner.return_value = _bot()
    recovery = DesktopSkillRecoveryService(bots=bots, tasks=recovery_queue)
    combined = _combined_plan()

    class _CombinedProjector:
        def __init__(self) -> None:
            self.applied: list[ResolvedSkillPlan] = []
            self.recovery_phase = False

        async def snapshot_skill_mappings(self, **_kwargs):
            return combined.projection.skill_mappings

        async def project(self, **_kwargs):
            return RuntimeProjectionResult.pending(
                code="CENTER_CONTENT_PACKAGE_PENDING",
                reason="exact package is still being prepared",
            )

        def resolve_plan(self, **_kwargs):
            return combined

        async def apply_plan(self, *, plan, retired_mappings, scope):
            if not self.recovery_phase:
                return RuntimeProjectionResult.pending(
                    code="CENTER_CONTENT_PACKAGE_PENDING",
                    reason="exact package is still being prepared",
                )
            assert scope.skills is True
            self.applied.append(plan)
            return RuntimeProjectionResult.converged(
                components={"skills": RuntimeProjectionStatus.CONVERGED}
            )

    projector = _CombinedProjector()

    class _ZeroCandidateTrackLatest:
        def __init__(self) -> None:
            candidates = MagicMock()
            candidates.list_candidate_facts.return_value = TrackLatestCandidateFacts(
                installations=(), skill_sets=(), bots=()
            )
            self._fanout = TrackLatestFanoutTaskHandler(
                candidates=candidates,
                tasks=recovery_queue,
                env_provider=lambda: "dev",
            )
            self.outcomes = []

        def version_published(self, version) -> None:
            self.outcomes.append(self._fanout.handle({"skill_id": version.skill_id}))

    class _DesiredStateRepository:
        def get_set(self, **_kwargs):
            return {"id": "42", "is_active": True, "is_default": False}

        def add_skill(self, **kwargs):
            return DesiredStateMutation(
                item={"skill_id": kwargs["skill_id"]},
                changed=True,
                previous_state=CapabilityDesiredState(set(), {}, {}),
            )

        def restore_desired_state(self, **_kwargs):
            raise AssertionError("successful Desired State writes are not restored")

    class _DesktopBots:
        def get_by_id_and_owner(self, bot_id, owner_id):
            return {
                **_bot(),
                "status": "ACTIVE",
                "device_id": "device-a",
            } if (bot_id, owner_id) == ("bot-a", "owner-a") else None

    class _Allow:
        def can_manage_bot(self, **_kwargs):
            return True

    class _Audit:
        def insert(self, _record):
            return None

    fanout = _ZeroCandidateTrackLatest()
    skill_sets = SkillSetManagementService(
        repository=_DesiredStateRepository(),
        bot_repo=_DesktopBots(),
        runtime=projector,
        legacy_factory=object(),
        passport=object(),
        authorization=_Allow(),
        audit_log_repo=_Audit(),
        mcp_center=object(),
        mcp_auth=object(),
        ext_info_provider=lambda _bot_id: None,
        recovery=recovery,
    )
    references = _ReferenceFacts()
    processor = SkillCenterReferenceProcessor(
        references=references,
        gateway=_ReferenceGateway(),
        materializer=_ReferenceMaterializer(),
        skill_sets=skill_sets,
        track_latest=fanout,
        env_provider=lambda: "dev",
    )

    reference_outcome = asyncio.run(processor.process("reference-a"))
    asyncio.run(
        skill_sets.add_skills(
            bot_id="bot-a",
            owner_id="owner-a",
            user_id="owner-a",
            set_id="42",
            skill_ids=("20",),
        )
    )
    reader = MagicMock()
    reader.active_skill_assets.return_value = (
        RegisteredSkillAsset(
            skill_id=20,
            name="calculator",
            git_path="center://public-calculator",
            skill_uuid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            sc_version_number="7",
        ),
    )
    latest = MagicMock()
    latest.list_published_versions.return_value = (
        PublishedTrackLatestVersion(
            skill_version_id=207,
            metadata_json='{"mcp_dependencies":[]}',
        ),
    )
    track_latest_outcome = BotTrackLatestReconcileTaskHandler(
        reader=reader,
        projector=projector,
        latest=latest,
        recovery=recovery,
        env_provider=lambda: "dev",
    ).handle({"owner_id": "owner-a", "bot_id": "bot-a", "skill_id": 20})

    claimed = recovery_queue._repo.claim_batch(
        worker_id="worker-1",
        env="dev",
        app=DEFAULT_APP,
        limit=10,
        lease_seconds=60,
    )
    distribution = _Distribution()
    layouts = MagicMock()
    layouts.get.return_value = BotSkillLayoutState.legacy_default(
        BotSkillLayoutScope(env="dev", entity_id="owner-a", bot_id="bot-a")
    )
    recovery_handler = DesktopSkillRecoveryTaskHandler(
        bots=bots,
        projector=projector,
        distribution=distribution,
        layouts=layouts,
        env_provider=lambda: "dev",
    )
    projector.recovery_phase = True
    recovery_outcome = recovery_handler.handle(claimed[0].payload)

    assert isinstance(reference_outcome, Complete)
    assert all(isinstance(outcome, Complete) for outcome in fanout.outcomes)
    assert isinstance(track_latest_outcome, Complete)
    assert len(claimed) == 1
    assert isinstance(recovery_outcome, Complete)
    assert distribution.prepared == [
        CanonicalCenterVersionIdentity(_UUID, "3"),
        CanonicalCenterVersionIdentity(
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "7"
        ),
    ]
    assert projector.applied == [combined]


def test_ensure_uses_minimal_payload_and_thirty_minute_deadline() -> None:
    bots = MagicMock()
    bots.get_by_id_and_owner.return_value = _bot()
    tasks = MagicMock()
    service = DesktopSkillRecoveryService(bots=bots, tasks=tasks)

    service.ensure(owner_id="owner-a", bot_id="bot-a")

    _, payload = tasks.enqueue.call_args.args
    assert payload == {"owner_id": "owner-a", "bot_id": "bot-a"}
    assert tasks.enqueue.call_args.kwargs["deadline_seconds"] == (
        DESKTOP_SKILL_RECOVERY_DEADLINE_SECONDS
    )
    assert "delay_seconds" not in tasks.enqueue.call_args.kwargs


@pytest.mark.integration
def test_terminal_recovery_releases_the_bot_key_for_a_later_sweep(
    recovery_queue,
) -> None:
    bots = MagicMock()
    bots.get_by_id_and_owner.return_value = _bot()
    recovery = DesktopSkillRecoveryService(bots=bots, tasks=recovery_queue)
    first = recovery.ensure(owner_id="owner-a", bot_id="bot-a")
    assert first is not None
    claimed = recovery_queue._repo.claim_batch(
        worker_id="worker-1",
        env="dev",
        app=DEFAULT_APP,
        limit=1,
        lease_seconds=60,
    )
    assert recovery_queue._repo.complete(
        task_id=claimed[0].id,
        worker_id="worker-1",
    )

    second = recovery.ensure(owner_id="owner-a", bot_id="bot-a")

    assert second is not None
    assert second.created is True
    assert second.record.id != first.record.id


def test_sweeper_pages_all_live_bound_desktop_bots_and_only_ensures() -> None:
    bots = MagicMock()
    bots.search_bots.side_effect = [
        (
            3,
            [
                {"owner_id": "o1", "bot_id": "b1", "binding_id": 1},
                {"owner_id": "o2", "bot_id": "b2", "binding_id": None},
            ],
        ),
        (3, [{"owner_id": "o3", "bot_id": "b3", "binding_id": 3}]),
    ]
    recovery = MagicMock()
    recovery.ensure.return_value = object()
    sweeper = DesktopSkillRecoverySweeper(
        bots=bots,
        recovery=recovery,
        config=DesktopSkillRecoveryConfig(sweep_page_size=2),
    )

    assert sweeper.sweep_once() == (2, 2)
    assert recovery.ensure.call_args_list == [
        call(owner_id="o1", bot_id="b1"),
        call(owner_id="o3", bot_id="b3"),
    ]

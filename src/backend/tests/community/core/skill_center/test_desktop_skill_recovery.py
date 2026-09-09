"""Desktop Skill recovery through its public ensure/handler seams."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.platform.task_queue import (
    TaskQueueRepository,
)
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel  # noqa: F401
from agentclaw.community.core.skill_center.center_content_distribution import (
    CenterContentPendingPackage,
    CenterContentReadyPackage,
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
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
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


class _Projector:
    def __init__(self) -> None:
        self.plans = [_plan("1"), _plan("2")]
        self.applied: list[ResolvedSkillPlan] = []

    def resolve_plan(self, **_kwargs) -> ResolvedSkillPlan:
        return self.plans.pop(0)

    async def apply_plan(self, *, plan, retired_mappings, scope):
        assert retired_mappings == ()
        assert scope == ProjectionScope(skills=True)
        self.applied.append(plan)
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


@pytest.mark.integration
def test_market_set_and_track_latest_ensures_share_one_live_bot_task(
    recovery_queue,
) -> None:
    bots = MagicMock()
    bots.get_by_id_and_owner.return_value = _bot()
    recovery = DesktopSkillRecoveryService(bots=bots, tasks=recovery_queue)

    results = [
        recovery.ensure(owner_id="owner-a", bot_id="bot-a")
        for _source in ("market", "set", "track-latest")
    ]

    assert all(result is not None for result in results)
    assert [result.created for result in results] == [True, False, False]
    assert len({result.record.id for result in results}) == 1
    assert len({result.record.deadline_at for result in results}) == 1
    assert len({result.record.run_at for result in results}) == 1
    assert results[0].record.payload == {
        "owner_id": "owner-a",
        "bot_id": "bot-a",
    }


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

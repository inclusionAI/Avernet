"""Skills Pool durable reconciliation task and lifecycle wake-up tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.events.types import (
    BaasPublishCompletedEvent,
    DeviceAliveEvent,
)
from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    LAYOUT_CONTRACT_VERSION,
)
from agentclaw.community.core.skills_pool.claim_service import (
    MigrationClaimOutcome,
    MigrationClaimResult,
)
from agentclaw.community.core.skills_pool.reconcile_service import (
    SkillsPoolReconcileOutcome,
    SkillsPoolReconcileResult,
)
from agentclaw.community.core.skills_pool.reconcile_task import (
    SKILLS_POOL_RECONCILE_TASK,
    SkillsPoolReconcileTaskHandler,
    SkillsPoolReconcileWakeupListener,
    build_skills_pool_reconcile_payload,
)
from agentclaw.community.core.skills_pool.quarantine import QuarantineStatus
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule, Retry


SCOPE = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")


def _claimed_state(
    *,
    lease_owner: str = "skills-pool:wakeup-1",
    active_layout: SkillLayout = SkillLayout.LEGACY,
) -> BotSkillLayoutState:
    return BotSkillLayoutState(
        scope=SCOPE,
        active_layout=active_layout,
        target_layout=SkillLayout.POOL,
        phase=(
            SkillLayoutPhase.POOL_ACTIVE
            if active_layout is SkillLayout.POOL
            else SkillLayoutPhase.POOL_PREPARING
        ),
        migration_generation="generation-1",
        persisted=True,
        layout_contract_version=LAYOUT_CONTRACT_VERSION,
        lease_owner=lease_owner,
    )


def _binding(
    *,
    provider: str,
    props: dict[str, object],
    binding_id: int = 42,
):
    return SimpleNamespace(
        id=binding_id,
        env="pre",
        entity_id="entity-1",
        entity_type="staff",
        device_id="device-current",
        device_provider=provider,
        device_props=props,
    )


def _bot() -> dict[str, object]:
    return {
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "entity_id": "entity-1",
        "env": "pre",
        "active_engine": "openclaw",
        "bot_type": "personal",
    }


def _listener(
    *,
    binding,
) -> tuple[SkillsPoolReconcileWakeupListener, MagicMock]:
    bindings = MagicMock()
    bindings.get_by_id.return_value = binding
    bots = MagicMock()
    bots.get_by_binding_id.return_value = _bot()
    queue = MagicMock()
    return (
        SkillsPoolReconcileWakeupListener(
            binding_repository=bindings,
            bot_repository=bots,
            task_queue_service=queue,
        ),
        queue,
    )


def test_arca_wakeup_only_enqueues_durable_bot_identity() -> None:
    listener, queue = _listener(
        binding=_binding(
            provider="arca",
            props={"sandbox_id": "sandbox-current"},
        )
    )

    listener.handle(
        DeviceAliveEvent(
            device_id="device-current",
            binding_id=42,
            entity_id="entity-1",
            entity_type="staff",
            device_provider="arca",
            sandbox_id="sandbox-current",
        )
    )

    task_type, payload = queue.enqueue.call_args.args[:2]
    assert task_type == SKILLS_POOL_RECONCILE_TASK
    assert payload["scope"] == {
        "env": "pre",
        "entity_id": "entity-1",
        "bot_id": "bot-1",
    }
    assert payload["source"] == "arca_device_alive"
    assert payload["signal_identity"] == {
        "binding_id": 42,
        "device_id": "device-current",
        "sandbox_id": "sandbox-current",
    }


def test_arca_wakeup_rejects_obviously_stale_sandbox() -> None:
    listener, queue = _listener(
        binding=_binding(
            provider="arca",
            props={"sandbox_id": "sandbox-current"},
        )
    )

    listener.handle(
        DeviceAliveEvent(
            device_id="device-old",
            binding_id=42,
            entity_id="entity-1",
            entity_type="staff",
            device_provider="arca",
            sandbox_id="sandbox-old",
        )
    )

    queue.enqueue.assert_not_called()


def test_baas_publish_wakeup_validates_current_publish_before_enqueue() -> None:
    listener, queue = _listener(
        binding=_binding(
            provider="baas",
            props={"restart_publish_id": "1002"},
        )
    )

    listener.handle(
        BaasPublishCompletedEvent(
            binding_id=42,
            bot_id="bot-1",
            owner_id="owner-1",
            publish_id=1002,
            publish_kind="restart",
        )
    )

    payload = queue.enqueue.call_args.args[1]
    assert payload["source"] == "baas_publish_completed"
    assert payload["signal_identity"] == {
        "binding_id": 42,
        "publish_id": 1002,
        "publish_kind": "restart",
    }


def test_baas_publish_wakeup_rejects_obviously_stale_publish() -> None:
    listener, queue = _listener(
        binding=_binding(
            provider="baas",
            props={"restart_publish_id": "2002"},
        )
    )

    listener.handle(
        BaasPublishCompletedEvent(
            binding_id=42,
            bot_id="bot-1",
            owner_id="owner-1",
            publish_id=1002,
            publish_kind="restart",
        )
    )

    queue.enqueue.assert_not_called()


def test_wakeup_listener_subscribes_both_events_idempotently() -> None:
    reset_event_bus()
    try:
        listener, _ = _listener(binding=_binding(provider="arca", props={}))

        asyncio.run(listener.bootstrap())
        asyncio.run(listener.bootstrap())

        bus = get_event_bus()
        assert bus._handlers[DeviceAliveEvent].count(listener.handle) == 1
        assert bus._handlers[BaasPublishCompletedEvent].count(listener.handle) == 1
    finally:
        reset_event_bus()


def test_required_wakeup_retries_queue_handoff_on_next_arca_heartbeat() -> None:
    reset_event_bus()
    try:
        listener, queue = _listener(
            binding=_binding(
                provider="arca",
                props={"sandbox_id": "sandbox-current"},
            )
        )
        queue.enqueue.side_effect = [RuntimeError("queue unavailable"), MagicMock()]
        asyncio.run(listener.bootstrap())
        event = DeviceAliveEvent(
            device_id="device-current",
            binding_id=42,
            entity_id="entity-1",
            entity_type="staff",
            device_provider="arca",
            sandbox_id="sandbox-current",
        )

        with pytest.raises(RuntimeError, match="required handler failed"):
            get_event_bus().publish(event)
        get_event_bus().publish(event)

        assert queue.enqueue.call_count == 2
    finally:
        reset_event_bus()


class FakeClaimService:
    def __init__(self, results: list[MigrationClaimResult]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    def claim(self, **kwargs: object) -> MigrationClaimResult:
        self.calls.append(kwargs)
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


class FakeLayouts:
    def __init__(self, state: BotSkillLayoutState) -> None:
        self.state = state
        self.renew_calls: list[dict[str, object]] = []
        self.acquire_calls: list[dict[str, object]] = []
        self.renew_result = True
        self.acquire_result = False
        self.runtime_reconciliation_calls: list[dict[str, object]] = []
        self.runtime_reconciliation_failure_calls: list[dict[str, object]] = []

    def renew_lease(self, **kwargs: object) -> bool:
        self.renew_calls.append(kwargs)
        return self.renew_result

    def try_acquire_lease(self, **kwargs: object) -> bool:
        self.acquire_calls.append(kwargs)
        if self.acquire_result:
            self.state = replace(
                self.state,
                lease_owner=str(kwargs["lease_owner"]),
            )
        return self.acquire_result

    def record_runtime_reconciliation(self, **kwargs: object) -> bool:
        self.runtime_reconciliation_calls.append(kwargs)
        return True

    def record_runtime_reconciliation_failure(self, **kwargs: object) -> bool:
        self.runtime_reconciliation_failure_calls.append(kwargs)
        return True


class FakeReconcileService:
    def __init__(self, results: list[SkillsPoolReconcileResult]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    async def reconcile(self, **kwargs: object) -> SkillsPoolReconcileResult:
        self.calls.append(kwargs)
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


class FakeQuarantines:
    def __init__(self, status: QuarantineStatus) -> None:
        self.status = status

    def get_quarantine(
        self,
        scope: BotSkillLayoutScope,
        migration_generation: str,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            scope=scope,
            migration_generation=migration_generation,
            status=self.status,
        )


def _handler(
    *,
    claim_results: list[MigrationClaimResult],
    reconcile_results: list[SkillsPoolReconcileResult],
    state: BotSkillLayoutState | None = None,
) -> tuple[
    SkillsPoolReconcileTaskHandler,
    FakeClaimService,
    FakeLayouts,
    FakeReconcileService,
]:
    effective_state = state or claim_results[0].state or _claimed_state()
    claims = FakeClaimService(claim_results)
    layouts = FakeLayouts(effective_state)
    reconcile = FakeReconcileService(reconcile_results)
    return (
        SkillsPoolReconcileTaskHandler(
            claim_service=claims,
            layout_repository=layouts,
            reconcile_service=reconcile,
            quarantine_repository=FakeQuarantines(QuarantineStatus.RETAINED),
            lease_seconds=300,
        ),
        claims,
        layouts,
        reconcile,
    )


def _payload(
    *,
    wakeup_id: str = "wakeup-1",
    signal_identity: dict[str, object] | None = None,
) -> dict[str, object]:
    return build_skills_pool_reconcile_payload(
        scope=SCOPE,
        source="arca_device_alive",
        signal_identity=signal_identity
        or {"binding_id": 7, "device_id": "stale-device"},
        wakeup_id=wakeup_id,
    )


def test_ineligible_unclaimed_bot_never_probes_or_reconciles_runtime() -> None:
    legacy = BotSkillLayoutState.legacy_default(SCOPE)
    handler, claims, _, reconcile = _handler(
        claim_results=[
            MigrationClaimResult(MigrationClaimOutcome.INELIGIBLE, legacy)
        ],
        reconcile_results=[],
        state=legacy,
    )

    assert handler.handle(_payload()) == Complete()
    assert len(claims.calls) == 1
    assert reconcile.calls == []


def test_unclaimed_task_claims_then_reconciles_same_generation() -> None:
    state = _claimed_state()
    handler, claims, _, reconcile = _handler(
        claim_results=[MigrationClaimResult(MigrationClaimOutcome.CLAIMED, state)],
        reconcile_results=[
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.POOL_ACTIVE)
        ],
        state=state,
    )

    outcome = handler.handle(_payload())

    assert outcome == Complete()
    assert claims.calls == [
        {
            "scope": SCOPE,
            "layout_contract_version": LAYOUT_CONTRACT_VERSION,
            "lease_owner": "skills-pool:wakeup-1",
            "lease_seconds": 300,
        }
    ]
    assert reconcile.calls == [{"scope": SCOPE, "lease_owner": "skills-pool:wakeup-1"}]


def test_pool_activation_schedules_generation_scoped_seven_day_cleanup() -> None:
    state = _claimed_state()
    claims = FakeClaimService(
        [MigrationClaimResult(MigrationClaimOutcome.CLAIMED, state)]
    )
    layouts = FakeLayouts(state)
    reconcile = FakeReconcileService(
        [SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.POOL_ACTIVE)]
    )
    queue = MagicMock()
    handler = SkillsPoolReconcileTaskHandler(
        claim_service=claims,
        layout_repository=layouts,
        reconcile_service=reconcile,
        quarantine_repository=FakeQuarantines(QuarantineStatus.RETAINED),
        task_queue_service=queue,
    )

    assert handler.handle(_payload()) == Complete()

    task_type, payload = queue.enqueue.call_args.args[:2]
    assert task_type == "skills_pool.quarantine.cleanup"
    assert payload["migration_generation"] == "generation-1"
    assert queue.enqueue.call_args.kwargs["delay_seconds"] == 7 * 24 * 60 * 60


def test_stale_signal_metadata_is_not_forwarded_as_runtime_identity() -> None:
    state = _claimed_state()
    handler, _, _, reconcile = _handler(
        claim_results=[MigrationClaimResult(MigrationClaimOutcome.CLAIMED, state)],
        reconcile_results=[
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.POOL_ACTIVE)
        ],
        state=state,
    )

    outcome = handler.handle(
        _payload(
            signal_identity={
                "binding_id": 7,
                "device_id": "old-device",
                "sandbox_id": "old-sandbox",
            }
        )
    )

    assert outcome == Complete()
    assert reconcile.calls == [{"scope": SCOPE, "lease_owner": "skills-pool:wakeup-1"}]


def test_claimed_generation_renews_without_rechecking_rollout_gate() -> None:
    state = _claimed_state()
    handler, claims, layouts, reconcile = _handler(
        claim_results=[
            MigrationClaimResult(MigrationClaimOutcome.ALREADY_CLAIMED, state)
        ],
        reconcile_results=[
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.POOL_ACTIVE)
        ],
        state=state,
    )

    outcome = handler.handle(_payload())

    assert outcome == Complete()
    assert len(claims.calls) == 1
    assert layouts.renew_calls[0]["migration_generation"] == "generation-1"
    assert len(reconcile.calls) == 1


def test_pool_active_wakeup_records_runtime_reconciliation_after_ready_probe() -> None:
    state = _claimed_state(active_layout=SkillLayout.POOL)
    handler, _, layouts, reconcile = _handler(
        claim_results=[
            MigrationClaimResult(MigrationClaimOutcome.ALREADY_CLAIMED, state)
        ],
        reconcile_results=[
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.ALREADY_ACTIVE)
        ],
        state=state,
    )

    outcome = handler.handle(_payload())

    assert outcome == Complete()
    assert reconcile.calls == [{"scope": SCOPE, "lease_owner": "skills-pool:wakeup-1"}]
    assert layouts.runtime_reconciliation_calls[0]["scope"] == SCOPE
    assert (
        layouts.runtime_reconciliation_calls[0]["migration_generation"]
        == "generation-1"
    )
    assert layouts.runtime_reconciliation_calls[0]["evidence"]["source"] == (
        "arca_device_alive"
    )
    assert layouts.runtime_reconciliation_calls[0]["evidence"]["probe"] is None


def test_pool_active_wakeup_skips_probe_after_quarantine_is_cleaned() -> None:
    state = _claimed_state(active_layout=SkillLayout.POOL)
    claims = FakeClaimService(
        [MigrationClaimResult(MigrationClaimOutcome.ALREADY_CLAIMED, state)]
    )
    layouts = FakeLayouts(state)
    reconcile = FakeReconcileService(
        [SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.ALREADY_ACTIVE)]
    )
    handler = SkillsPoolReconcileTaskHandler(
        claim_service=claims,
        layout_repository=layouts,
        reconcile_service=reconcile,
        quarantine_repository=FakeQuarantines(QuarantineStatus.CLEANED),
    )

    assert handler.handle(_payload()) == Complete()

    assert reconcile.calls == []
    assert layouts.runtime_reconciliation_calls == []
    assert layouts.runtime_reconciliation_failure_calls == []


def test_pool_active_wakeup_does_not_enqueue_duplicate_cleanup_task() -> None:
    state = _claimed_state(active_layout=SkillLayout.POOL)
    claims = FakeClaimService(
        [MigrationClaimResult(MigrationClaimOutcome.ALREADY_CLAIMED, state)]
    )
    layouts = FakeLayouts(state)
    reconcile = FakeReconcileService(
        [SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.ALREADY_ACTIVE)]
    )
    queue = MagicMock()
    handler = SkillsPoolReconcileTaskHandler(
        claim_service=claims,
        layout_repository=layouts,
        reconcile_service=reconcile,
        quarantine_repository=FakeQuarantines(QuarantineStatus.RETAINED),
        task_queue_service=queue,
    )

    assert handler.handle(_payload()) == Complete()

    queue.enqueue.assert_not_called()


def test_pool_active_legacy_task_without_observed_at_completes_without_probe() -> None:
    state = _claimed_state(active_layout=SkillLayout.POOL)
    handler, _, layouts, reconcile = _handler(
        claim_results=[
            MigrationClaimResult(MigrationClaimOutcome.ALREADY_CLAIMED, state)
        ],
        reconcile_results=[
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.ALREADY_ACTIVE)
        ],
        state=state,
    )
    payload = _payload()
    payload.pop("observed_at")

    assert handler.handle(payload) == Complete()

    assert reconcile.calls == []
    assert layouts.runtime_reconciliation_calls == []


def test_pool_active_wakeup_does_not_record_failed_runtime_probe() -> None:
    state = _claimed_state(active_layout=SkillLayout.POOL)
    handler, _, layouts, _ = _handler(
        claim_results=[
            MigrationClaimResult(MigrationClaimOutcome.ALREADY_CLAIMED, state)
        ],
        reconcile_results=[
            SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.INVALID,
                evidence={"reason": "bridge_invalid"},
                retryable=False,
            )
        ],
        state=state,
    )

    outcome = handler.handle(_payload())

    assert outcome == Fail("skills pool reconciliation blocked: invalid")
    assert layouts.runtime_reconciliation_calls == []
    failure = layouts.runtime_reconciliation_failure_calls[0]
    assert failure["scope"] == SCOPE
    assert failure["migration_generation"] == "generation-1"
    assert failure["evidence"] == {
        "source": "arca_device_alive",
        "signal_identity": {
            "binding_id": 7,
            "device_id": "stale-device",
        },
        "wakeup_id": "wakeup-1",
        "outcome": "invalid",
        "probe": {"reason": "bridge_invalid"},
    }


def test_busy_generation_reschedules_until_lease_can_be_acquired() -> None:
    state = _claimed_state(lease_owner="another-worker")
    handler, _, layouts, reconcile = _handler(
        claim_results=[
            MigrationClaimResult(MigrationClaimOutcome.ALREADY_CLAIMED, state)
        ],
        reconcile_results=[
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.POOL_ACTIVE)
        ],
        state=state,
    )

    first = handler.handle(_payload())
    layouts.acquire_result = True
    second = handler.handle(_payload())

    assert first == Reschedule(5.0)
    assert second == Complete()
    assert len(reconcile.calls) == 1


def test_claim_race_retries_before_any_runtime_mutation() -> None:
    state = _claimed_state()
    handler, _, _, reconcile = _handler(
        claim_results=[
            MigrationClaimResult(
                MigrationClaimOutcome.CLAIM_RACE_LOST,
                BotSkillLayoutState.legacy_default(SCOPE),
            ),
            MigrationClaimResult(MigrationClaimOutcome.CLAIMED, state),
        ],
        reconcile_results=[
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.POOL_ACTIVE)
        ],
        state=state,
    )

    first = handler.handle(_payload())
    second = handler.handle(_payload())

    assert first == Retry("skills pool migration claim race lost")
    assert second == Complete()
    assert len(reconcile.calls) == 1


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.NOT_CAPABLE),
            Complete(),
        ),
        (
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.TRANSIENT_ERROR),
            Retry("skills pool reconciliation transient_error"),
        ),
        (
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.INVALID),
            Fail("skills pool reconciliation blocked: invalid"),
        ),
    ],
)
def test_probe_four_state_outcomes_drive_queue_policy(
    result: SkillsPoolReconcileResult,
    expected,
) -> None:
    state = _claimed_state()
    handler, _, _, _ = _handler(
        claim_results=[MigrationClaimResult(MigrationClaimOutcome.CLAIMED, state)],
        reconcile_results=[result],
        state=state,
    )

    assert handler.handle(_payload()) == expected


@pytest.mark.parametrize(
    "retryable_outcome",
    [
        SkillsPoolReconcileOutcome.TRANSIENT_ERROR,
        SkillsPoolReconcileOutcome.STATE_RACE_LOST,
        SkillsPoolReconcileOutcome.MAPPING_FAILED,
        SkillsPoolReconcileOutcome.MAPPING_VERIFY_FAILED,
        SkillsPoolReconcileOutcome.DATABASE_COMMIT_FAILED,
    ],
)
def test_retryable_failure_then_success_is_idempotent(
    retryable_outcome: SkillsPoolReconcileOutcome,
) -> None:
    state = _claimed_state()
    handler, claims, _, reconcile = _handler(
        claim_results=[
            MigrationClaimResult(MigrationClaimOutcome.CLAIMED, state),
            MigrationClaimResult(MigrationClaimOutcome.ALREADY_CLAIMED, state),
        ],
        reconcile_results=[
            SkillsPoolReconcileResult(retryable_outcome),
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.POOL_ACTIVE),
        ],
        state=state,
    )

    first = handler.handle(_payload())
    second = handler.handle(_payload())

    assert isinstance(first, Retry)
    assert second == Complete()
    assert len(claims.calls) == 2
    assert len(reconcile.calls) == 2


def test_retryable_cutover_failure_then_success_is_idempotent() -> None:
    state = _claimed_state()
    handler, _, _, reconcile = _handler(
        claim_results=[
            MigrationClaimResult(MigrationClaimOutcome.CLAIMED, state),
            MigrationClaimResult(MigrationClaimOutcome.ALREADY_CLAIMED, state),
        ],
        reconcile_results=[
            SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.CUTOVER_FAILED,
                retryable=True,
            ),
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.POOL_ACTIVE),
        ],
        state=state,
    )

    first = handler.handle(_payload())
    second = handler.handle(_payload())

    assert first == Retry("skills pool reconciliation cutover_failed")
    assert second == Complete()
    assert len(reconcile.calls) == 2


def test_non_retryable_cutover_failure_blocks() -> None:
    state = _claimed_state()
    handler, _, _, _ = _handler(
        claim_results=[MigrationClaimResult(MigrationClaimOutcome.CLAIMED, state)],
        reconcile_results=[
            SkillsPoolReconcileResult(
                SkillsPoolReconcileOutcome.CUTOVER_FAILED,
                retryable=False,
            )
        ],
        state=state,
    )

    assert handler.handle(_payload()) == Fail(
        "skills pool reconciliation blocked: cutover_failed"
    )


def test_manual_repair_state_stops_before_lease_reacquisition() -> None:
    state = replace(
        _claimed_state(),
        phase=SkillLayoutPhase.NEEDS_MANUAL_REPAIR,
        lease_owner=None,
    )
    handler, _, layouts, reconcile = _handler(
        claim_results=[
            MigrationClaimResult(MigrationClaimOutcome.ALREADY_CLAIMED, state)
        ],
        reconcile_results=[],
        state=state,
    )

    assert handler.handle(_payload()) == Fail(
        "skills pool migration requires manual repair"
    )
    assert layouts.acquire_calls == []
    assert reconcile.calls == []


def test_invalid_payload_fails_without_touching_domain_services() -> None:
    state = _claimed_state()
    handler, claims, _, reconcile = _handler(
        claim_results=[MigrationClaimResult(MigrationClaimOutcome.CLAIMED, state)],
        reconcile_results=[
            SkillsPoolReconcileResult(SkillsPoolReconcileOutcome.POOL_ACTIVE)
        ],
        state=state,
    )

    outcome = handler.handle({"scope": {"env": "pre"}})

    assert isinstance(outcome, Fail)
    assert claims.calls == []
    assert reconcile.calls == []

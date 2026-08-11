"""Migration Quarantine eligibility and cleanup orchestration tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.skills_pool.quarantine import QUARANTINE_RETENTION, QuarantineBlocker, QuarantineRecord, QuarantineStatus, RuntimeQuarantineCleanupResult, RuntimeQuarantineCleanupStatus, RuntimeReconciliationStatus, SkillsPoolQuarantineCleanupTaskHandler, SkillsPoolQuarantineService
from agentclaw.community.core.task_queue.types import Complete, Retry
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)


NOW = datetime(2026, 7, 24, 12, tzinfo=UTC)
SCOPE = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")


def _record(**changes: object) -> QuarantineRecord:
    value = QuarantineRecord(
        scope=SCOPE,
        migration_generation="generation-1",
        engine="openclaw",
        path=(
            "/home/admin/.openclaw/workspace/skills-pool/"
            ".migration-quarantine/generation-1/skills-local"
        ),
        status=QuarantineStatus.RETAINED,
        created_at=NOW - timedelta(days=8),
        pool_activated_at=NOW - timedelta(days=8),
        source_evidence={"cutover": "COMMITTED"},
        runtime_reconciled_at=NOW - timedelta(days=1),
        runtime_reconciliation_status=RuntimeReconciliationStatus.READY,
        runtime_evidence={"source": "arca_device_alive"},
    )
    return replace(value, **changes)


def _layout(
    phase: SkillLayoutPhase = SkillLayoutPhase.POOL_ACTIVE,
) -> BotSkillLayoutState:
    return BotSkillLayoutState(
        scope=SCOPE,
        active_layout=SkillLayout.POOL,
        target_layout=None,
        phase=phase,
        migration_generation="generation-1",
        persisted=True,
        pool_activated_at=NOW - timedelta(days=8),
    )


def test_eligibility_blocks_before_seven_days() -> None:
    decision = SkillsPoolQuarantineService.evaluate(
        _record(
            created_at=NOW - timedelta(days=6),
            pool_activated_at=NOW - timedelta(days=6),
        ),
        _layout(),
        now=NOW,
    )

    assert decision.eligible is False
    assert decision.blockers == (QuarantineBlocker.RETENTION_PERIOD,)
    assert decision.eligible_at == NOW + timedelta(days=1)


def test_eligibility_requires_post_activation_runtime_reconciliation() -> None:
    decision = SkillsPoolQuarantineService.evaluate(
        _record(runtime_reconciled_at=None, runtime_evidence=None),
        _layout(),
        now=NOW,
    )

    assert decision.eligible is False
    assert decision.blockers == (QuarantineBlocker.RUNTIME_EVIDENCE_MISSING,)


def test_latest_failed_runtime_reconciliation_revokes_cleanup_eligibility() -> None:
    decision = SkillsPoolQuarantineService.evaluate(
        _record(
            runtime_reconciled_at=NOW - timedelta(hours=1),
            runtime_reconciliation_status=RuntimeReconciliationStatus.FAILED,
            runtime_evidence={"outcome": "invalid"},
        ),
        _layout(),
        now=NOW,
    )

    assert decision.eligible is False
    assert decision.blockers == (QuarantineBlocker.RUNTIME_RECONCILIATION_FAILED,)


def test_eligibility_rejects_an_old_generation_after_remigration() -> None:
    decision = SkillsPoolQuarantineService.evaluate(
        _record(),
        replace(_layout(), migration_generation="generation-2"),
        now=NOW,
    )

    assert decision.eligible is False
    assert decision.blockers == (QuarantineBlocker.LAYOUT_UNHEALTHY,)


@pytest.mark.parametrize(
    "phase",
    [
        SkillLayoutPhase.NEEDS_MANUAL_REPAIR,
        SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING,
        SkillLayoutPhase.LEGACY_ROLLBACK_COMMITTED,
    ],
)
def test_eligibility_blocks_failed_or_rollback_states(
    phase: SkillLayoutPhase,
) -> None:
    decision = SkillsPoolQuarantineService.evaluate(
        _record(),
        _layout(phase),
        now=NOW,
    )

    assert decision.eligible is False
    assert QuarantineBlocker.LAYOUT_UNHEALTHY in decision.blockers


def test_cleanup_is_idempotent_and_audited() -> None:
    records = MagicMock()
    records.get_quarantine.return_value = _record()
    records.claim_cleanup.return_value = True
    records.mark_cleaned.return_value = True
    layouts = MagicMock()
    layouts.get.return_value = _layout()
    runtime = MagicMock()
    runtime.cleanup_quarantine = AsyncMock(
        return_value=RuntimeQuarantineCleanupResult(
            status=RuntimeQuarantineCleanupStatus.CLEANED,
            evidence={"path_absent": True},
        )
    )
    service = SkillsPoolQuarantineService(
        quarantine_repository=records,
        layout_repository=layouts,
        runtime=runtime,
        now=lambda: NOW,
    )

    result = service.cleanup(SCOPE, "generation-1")

    assert result.status is QuarantineStatus.CLEANED
    runtime.cleanup_quarantine.assert_awaited_once()
    records.mark_cleaned.assert_called_once()


def test_failed_probe_between_eligibility_and_claim_prevents_runtime_delete() -> None:
    records = MagicMock()
    records.get_quarantine.return_value = _record()
    records.claim_cleanup.return_value = False
    layouts = MagicMock()
    layouts.get.return_value = _layout()
    runtime = MagicMock()
    runtime.cleanup_quarantine = AsyncMock()
    service = SkillsPoolQuarantineService(
        quarantine_repository=records,
        layout_repository=layouts,
        runtime=runtime,
        now=lambda: NOW,
    )

    result = service.cleanup(SCOPE, "generation-1")

    assert result.status is QuarantineStatus.RETAINED
    assert records.claim_cleanup.call_args.kwargs["eligible_before"] == (
        NOW - QUARANTINE_RETENTION
    )
    runtime.cleanup_quarantine.assert_not_awaited()


def test_cleanup_timeout_keeps_cleanup_fence_until_lease_expires() -> None:
    records = MagicMock()
    records.get_quarantine.return_value = _record()
    records.claim_cleanup.return_value = True
    layouts = MagicMock()
    layouts.get.return_value = _layout()
    runtime = MagicMock()

    async def slow_cleanup(**_: object) -> RuntimeQuarantineCleanupResult:
        await asyncio.sleep(1)
        return RuntimeQuarantineCleanupResult(
            status=RuntimeQuarantineCleanupStatus.CLEANED,
            evidence={},
        )

    runtime.cleanup_quarantine = AsyncMock(side_effect=slow_cleanup)
    service = SkillsPoolQuarantineService(
        quarantine_repository=records,
        layout_repository=layouts,
        runtime=runtime,
        now=lambda: NOW,
        runtime_timeout_seconds=0.001,
    )

    result = service.cleanup(SCOPE, "generation-1")

    assert result.status is QuarantineStatus.CLEANUP_FAILED
    assert result.retryable is True
    records.record_cleanup_uncertain.assert_called_once()
    records.mark_cleanup_failed.assert_not_called()


def test_uncertain_invalid_runtime_response_is_retried() -> None:
    records = MagicMock()
    records.get_quarantine.return_value = _record()
    records.claim_cleanup.return_value = True
    layouts = MagicMock()
    layouts.get.return_value = _layout()
    runtime = MagicMock()
    runtime.cleanup_quarantine = AsyncMock(
        return_value=RuntimeQuarantineCleanupResult(
            status=RuntimeQuarantineCleanupStatus.INVALID,
            evidence={"reason": "invalid_runtime_response"},
        )
    )
    service = SkillsPoolQuarantineService(
        quarantine_repository=records,
        layout_repository=layouts,
        runtime=runtime,
        now=lambda: NOW,
    )
    handler = SkillsPoolQuarantineCleanupTaskHandler(service)

    outcome = handler.handle(
        {
            "scope": {
                "env": SCOPE.env,
                "entity_id": SCOPE.entity_id,
                "bot_id": SCOPE.bot_id,
            },
            "migration_generation": "generation-1",
        }
    )

    assert outcome == Retry("migration quarantine cleanup failed")
    records.record_cleanup_uncertain.assert_called_once()
    records.mark_cleanup_failed.assert_not_called()


def test_structurally_invalid_cleanup_is_recorded_and_not_retried() -> None:
    records = MagicMock()
    records.get_quarantine.return_value = _record()
    records.claim_cleanup.return_value = True
    layouts = MagicMock()
    layouts.get.return_value = _layout()
    runtime = MagicMock()
    runtime.cleanup_quarantine = AsyncMock(
        return_value=RuntimeQuarantineCleanupResult(
            status=RuntimeQuarantineCleanupStatus.INVALID,
            evidence={"reason": "generation_escapes_quarantine_root"},
        )
    )
    service = SkillsPoolQuarantineService(
        quarantine_repository=records,
        layout_repository=layouts,
        runtime=runtime,
        now=lambda: NOW,
    )
    handler = SkillsPoolQuarantineCleanupTaskHandler(service)

    outcome = handler.handle(
        {
            "scope": {
                "env": SCOPE.env,
                "entity_id": SCOPE.entity_id,
                "bot_id": SCOPE.bot_id,
            },
            "migration_generation": "generation-1",
        }
    )

    assert outcome == Complete()
    records.mark_cleanup_failed.assert_called_once()

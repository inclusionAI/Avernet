"""Skills Pool operator recovery and explicit rollback domain tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agentclaw.community.core.skills_pool.recovery_service import (
    ManualRepairResolution,
    SkillsPoolRollbackOutcome,
    SkillsPoolRollbackService,
    SkillsPoolRecoveryOutcome,
    SkillsPoolRecoveryService,
)
from agentclaw.community.core.skills_pool.models import (
    PoolCutoverResult,
    PoolCutoverStatus,
    RegisteredSkillAsset,
)
from agentclaw.community.core.skills_pool.reconcile_task import (
    SKILLS_POOL_RECONCILE_TASK,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)


SCOPE = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")


def _manual_state() -> BotSkillLayoutState:
    return BotSkillLayoutState(
        scope=SCOPE,
        active_layout=SkillLayout.LEGACY,
        target_layout=SkillLayout.POOL,
        phase=SkillLayoutPhase.NEEDS_MANUAL_REPAIR,
        migration_generation="generation-1",
        persisted=True,
        layout_contract_version="skills-pool-p3-v1",
        preparation_id="preparation-1",
        last_failure_evidence={"reason": "response_lost"},
    )


class _Layouts:
    def __init__(self, state: BotSkillLayoutState) -> None:
        self.state = state
        self.resolutions: list[dict[str, object]] = []

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        assert scope == SCOPE
        return self.state

    def resolve_repair(self, **kwargs: object) -> bool:
        self.resolutions.append(kwargs)
        committed = bool(kwargs["cutover_committed"])
        self.state = replace(
            self.state,
            phase=(
                SkillLayoutPhase.POOL_CUTOVER_COMMITTED
                if committed
                else SkillLayoutPhase.POOL_READY
            ),
            data_plane_cutover_committed=committed,
        )
        return True


class _Queue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def enqueue(
        self,
        task_type: str,
        payload: dict,
        deadline_seconds: int,
        *,
        delay_seconds: int = 0,
    ) -> object:
        self.calls.append(
            {
                "task_type": task_type,
                "payload": payload,
                "deadline_seconds": deadline_seconds,
                "delay_seconds": delay_seconds,
            }
        )
        return object()


def test_operator_resolution_records_note_and_durably_retriggers() -> None:
    layouts = _Layouts(_manual_state())
    queue = _Queue()
    service = SkillsPoolRecoveryService(
        layout_repository=layouts,
        task_queue_service=queue,
    )

    result = service.resolve_repair_state(
        scope=SCOPE,
        migration_generation="generation-1",
        operator="oncall-1",
        note="已在当前容器确认 legacy bridge 指向 Pool",
        resolution=ManualRepairResolution.POOL_COMMITTED,
    )

    assert result.outcome is SkillsPoolRecoveryOutcome.RETRIGGERED
    assert layouts.resolutions == [
        {
            "scope": SCOPE,
            "migration_generation": "generation-1",
            "operator": "oncall-1",
            "note": "已在当前容器确认 legacy bridge 指向 Pool",
            "cutover_committed": True,
        }
    ]
    assert len(queue.calls) == 1
    assert queue.calls[0]["task_type"] == SKILLS_POOL_RECONCILE_TASK
    payload = queue.calls[0]["payload"]
    assert payload["scope"] == {
        "env": "pre",
        "entity_id": "entity-1",
        "bot_id": "bot-1",
    }
    assert payload["source"] == "manual_repair_resolution"
    assert payload["signal_identity"] == {
        "operator": "oncall-1",
        "resolution": "pool_committed",
    }


def test_non_manual_or_stale_generation_is_not_retriggered() -> None:
    layouts = _Layouts(
        replace(_manual_state(), phase=SkillLayoutPhase.POOL_CUTOVER_COMMITTED)
    )
    queue = _Queue()
    service = SkillsPoolRecoveryService(
        layout_repository=layouts,
        task_queue_service=queue,
    )

    result = service.resolve_repair_state(
        scope=SCOPE,
        migration_generation="stale-generation",
        operator="oncall-1",
        note="checked",
        resolution=ManualRepairResolution.LEGACY_NOT_COMMITTED,
    )

    assert result.outcome is SkillsPoolRecoveryOutcome.NOT_REPAIR_REQUIRED
    assert layouts.resolutions == []
    assert queue.calls == []


def _pool_state() -> BotSkillLayoutState:
    return BotSkillLayoutState(
        scope=SCOPE,
        active_layout=SkillLayout.POOL,
        target_layout=None,
        phase=SkillLayoutPhase.POOL_ACTIVE,
        migration_generation="generation-1",
        persisted=True,
        layout_contract_version="skills-pool-p3-v1",
        preparation_id="preparation-1",
    )


class _RollbackLayouts:
    def __init__(self) -> None:
        self.state = _pool_state()
        self.events: list[str] = []
        self.locators: dict[int, str] | None = None

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        assert scope == SCOPE
        return self.state

    def begin_legacy_rollback(self, **kwargs: object) -> bool:
        self.events.append("begin")
        self.state = replace(
            self.state,
            target_layout=SkillLayout.LEGACY,
            phase=SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING,
            migration_generation=str(kwargs["rollback_generation"]),
            lease_owner=str(kwargs["lease_owner"]),
        )
        return True

    def record_legacy_rollback_committed(self, **kwargs: object) -> bool:
        self.events.append("cutover")
        self.state = replace(
            self.state,
            phase=SkillLayoutPhase.LEGACY_ROLLBACK_COMMITTED,
            data_plane_cutover_committed=False,
        )
        return True

    def try_acquire_rollback_lease(self, **kwargs: object) -> bool:
        self.state = replace(
            self.state,
            lease_owner=str(kwargs["lease_owner"]),
        )
        return True

    def record_rollback_failure(self, **kwargs: object) -> bool:
        self.events.append("failure")
        self.state = replace(
            self.state,
            last_failure_stage=str(kwargs["failure_stage"]),
            last_failure_retryable=bool(kwargs["retryable"]),
            last_failure_evidence=dict(kwargs["evidence"]),
        )
        return True

    def commit_legacy_active(
        self, *, local_locators: dict[int, str], **kwargs: object
    ) -> bool:
        self.events.append("database")
        self.locators = local_locators
        self.state = replace(
            self.state,
            active_layout=SkillLayout.LEGACY,
            target_layout=None,
            phase=SkillLayoutPhase.LEGACY_ACTIVE,
            layout_contract_version=None,
            preparation_id=None,
            lease_owner=None,
        )
        return True


class _Bots:
    def get_by_id_and_entity(
        self, bot_id: str, entity_id: str
    ) -> dict[str, object]:
        assert (bot_id, entity_id) == (SCOPE.bot_id, SCOPE.entity_id)
        return {
            "bot_id": SCOPE.bot_id,
            "entity_id": SCOPE.entity_id,
            "owner_id": "owner-1",
            "env": SCOPE.env,
            "active_engine": "openclaw",
        }


class _Skills:
    local = [
        RegisteredSkillAsset(
            skill_id=11,
            name="local-a",
            git_path=(
                "local:///home/admin/.openclaw/workspace/"
                "skills-pool/skills-local/local-a"
            ),
        )
    ]
    active = [
        *local,
        RegisteredSkillAsset(
            skill_id=21,
            name="repo-a",
            git_path="git://business/repo-a",
        ),
    ]

    def list_bot_local_assets(self, **kwargs: object) -> list[RegisteredSkillAsset]:
        return self.local

    def list_bot_active_assets(
        self, **kwargs: object
    ) -> list[RegisteredSkillAsset]:
        return self.active


class _RollbackRuntime:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.publish_results = [True, False]

    async def rollback_to_legacy(self, **kwargs: object) -> PoolCutoverResult:
        self.events.append("rollback")
        assert kwargs["registered_local_names"] == ["local-a"]
        return PoolCutoverResult(
            committed=True,
            status=PoolCutoverStatus.COMMITTED,
            evidence={"source": "current_pool"},
        )

    async def publish_mappings(self, **kwargs: object) -> bool:
        self.events.append("mapping")
        mappings = kwargs["mappings"]
        assert [item.source for item in mappings] == [
            "/home/admin/.openclaw/workspace/skills/skills-local/local-a",
            "/home/admin/.openclaw/workspace/skills/skills-repo/business/repo-a",
        ]
        return self.publish_results.pop(0)

    async def verify_mappings(self, **kwargs: object) -> bool:
        self.events.append("verify")
        return True


@pytest.mark.asyncio
async def test_explicit_rollback_only_moves_forward_and_preserves_pool_writes() -> None:
    layouts = _RollbackLayouts()
    runtime = _RollbackRuntime()
    service = SkillsPoolRollbackService(
        bot_repository=_Bots(),
        layout_repository=layouts,
        skill_repository=_Skills(),
        runtime=runtime,
    )

    first = await service.rollback(
        scope=SCOPE,
        rollback_generation="rollback-1",
        lease_owner="operator-task-1",
        operator="oncall-1",
        note="业务确认回滚",
    )

    assert first.outcome is SkillsPoolRollbackOutcome.MAPPING_FAILED
    assert layouts.state.phase is SkillLayoutPhase.LEGACY_ROLLBACK_COMMITTED
    assert layouts.events == ["begin", "cutover", "failure"]
    assert runtime.events == [
        "mapping",
        "verify",
        "rollback",
        "mapping",
    ]

    runtime.publish_results = [True]
    runtime.events.clear()
    layouts.events.clear()
    layouts.state = replace(layouts.state, lease_owner="dead-worker")
    second = await service.rollback(
        scope=SCOPE,
        rollback_generation="rollback-1",
        lease_owner="operator-task-2",
        operator="oncall-1",
        note="重试同一回滚",
    )

    assert second.outcome is SkillsPoolRollbackOutcome.LEGACY_ACTIVE
    assert runtime.events == ["mapping", "verify"]
    assert layouts.events == ["database"]
    assert layouts.locators == {
        11: (
            "local:///home/admin/.openclaw/workspace/"
            "skills/skills-local/local-a"
        )
    }

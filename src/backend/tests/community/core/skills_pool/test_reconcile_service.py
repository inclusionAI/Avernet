"""多引擎 Skills Pool 已登记技能激活闭环测试。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.skills_pool.claim_service import (
    MigrationClaimOutcome,
    MigrationClaimResult,
)
from agentclaw.community.core.skills_pool.models import (
    PoolCutoverResult,
    PoolCutoverStatus,
    RegisteredSkillAsset,
    pool_paths_for_engine,
)
from agentclaw.community.core.skills_pool.reconcile_service import (
    SkillsPoolReconcileOutcome,
    SkillsPoolReconcileService,
)
from agentclaw.community.core.skills_pool.reconcile_task import (
    SkillsPoolReconcileTaskHandler,
    build_skills_pool_reconcile_payload,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.core.task_queue.types import Complete, Retry
from agentclaw.community.plugins.skills_pool_runtime import OpenClawSkillsPoolRuntime


SCOPE = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")
GENERATION = "generation-1"
PREPARATION_ID = "5ab2890d-ea0f-43f5-bfae-458457f3e54e"


def claimed_state(**changes: object) -> BotSkillLayoutState:
    state = BotSkillLayoutState(
        scope=SCOPE,
        active_layout=SkillLayout.LEGACY,
        target_layout=SkillLayout.POOL,
        phase=SkillLayoutPhase.POOL_PREPARING,
        migration_generation=GENERATION,
        persisted=True,
        layout_contract_version="skills-pool-p3-v1",
        lease_owner="worker-1",
    )
    return replace(state, **changes)


class FakeBotRepository:
    def __init__(self, engine: str = "openclaw") -> None:
        self.bot: dict[str, object] | None = {
            "bot_id": SCOPE.bot_id,
            "entity_id": SCOPE.entity_id,
            "owner_id": "owner-1",
            "env": SCOPE.env,
            "active_engine": engine,
        }

    def get_by_id_and_entity(
        self, bot_id: str, entity_id: str
    ) -> dict[str, object] | None:
        return self.bot


class FakeLayoutRepository:
    def __init__(self, state: BotSkillLayoutState | None = None) -> None:
        self.state = state or claimed_state()
        self.events: list[str] = []
        self.committed_locators: dict[int, str] | None = None
        self.lease_valid = True
        self.fail_once_at: str | None = None

    def _should_fail(self, stage: str) -> bool:
        if self.fail_once_at != stage:
            return False
        self.fail_once_at = None
        return True

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        assert scope == SCOPE
        return self.state

    def record_ready_probe(self, **kwargs: object) -> bool:
        if self._should_fail("ready"):
            return False
        if not self._owns(kwargs):
            return False
        self.events.append("ready")
        self.state = replace(
            self.state,
            phase=SkillLayoutPhase.POOL_READY,
            preparation_id=str(kwargs["preparation_id"]),
            last_probe_result="READY",
        )
        return True

    def release_not_capable_claim(self, **kwargs: object) -> bool:
        if self._should_fail("not_capable_release"):
            return False
        if not self._owns(kwargs):
            return False
        self.events.append("not_capable_release")
        self.state = replace(
            self.state,
            target_layout=None,
            phase=SkillLayoutPhase.LEGACY_ACTIVE,
            migration_generation=None,
            preparation_id=None,
            last_probe_result="NOT_CAPABLE",
            last_probe_evidence=dict(kwargs["evidence"]),
            lease_owner=None,
            lease_expires_at=None,
        )
        return True

    def holds_lease(self, **kwargs: object) -> bool:
        return self.lease_valid and self._owns(kwargs)

    def record_cutover_committed(self, **kwargs: object) -> bool:
        if self._should_fail("cutover_commit"):
            return False
        if not self._owns(kwargs):
            return False
        self.events.append("cutover")
        self.state = replace(
            self.state,
            phase=SkillLayoutPhase.POOL_CUTOVER_COMMITTED,
            data_plane_cutover_committed=True,
        )
        return True

    def begin_cutover(self, **kwargs: object) -> bool:
        if self._should_fail("begin"):
            return False
        if not self._owns(kwargs):
            return False
        self.events.append("begin")
        self.state = replace(
            self.state,
            phase=SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER,
        )
        return True

    def record_pre_cutover_failure(self, **kwargs: object) -> bool:
        if not self._owns(kwargs):
            return False
        self.events.append("failure")
        self.state = replace(
            self.state,
            last_failure_code=str(kwargs["failure_code"]),
            last_failure_stage=str(kwargs["failure_stage"]),
            last_failure_retryable=bool(kwargs["retryable"]),
            last_failure_evidence=dict(kwargs["evidence"]),
        )
        return True

    def record_post_cutover_failure(self, **kwargs: object) -> bool:
        if not self._owns(kwargs):
            return False
        self.events.append("post_failure")
        self.state = replace(
            self.state,
            last_failure_code=str(kwargs["failure_code"]),
            last_failure_stage=str(kwargs["failure_stage"]),
            last_failure_retryable=bool(kwargs["retryable"]),
            last_failure_evidence=dict(kwargs["evidence"]),
        )
        return True

    def mark_repair_required(self, **kwargs: object) -> bool:
        if not self._owns(kwargs):
            return False
        self.events.append("manual_repair")
        self.state = replace(
            self.state,
            phase=SkillLayoutPhase.NEEDS_MANUAL_REPAIR,
            last_failure_code=str(kwargs["failure_code"]),
            last_failure_stage=str(kwargs["failure_stage"]),
            last_failure_retryable=False,
            last_failure_evidence=dict(kwargs["evidence"]),
        )
        return True

    def record_cutover_finalizing(self, **kwargs: object) -> bool:
        if not self._owns(kwargs):
            return False
        self.events.append("finalizing")
        self.state = replace(
            self.state,
            phase=SkillLayoutPhase.POOL_CUTOVER_FINALIZING,
            data_plane_cutover_committed=True,
            last_failure_code="POST_CUTOVER_SYNC_PENDING",
            last_failure_stage="post_cutover_sync",
            last_failure_retryable=True,
            last_failure_evidence=dict(kwargs["evidence"]),
        )
        return True

    def commit_pool_active(
        self,
        *,
        local_locators: dict[int, str],
        **kwargs: object,
    ) -> bool:
        if self._should_fail("database"):
            return False
        if not self._owns(kwargs):
            return False
        self.events.append("database")
        self.committed_locators = local_locators
        self.state = replace(
            self.state,
            active_layout=SkillLayout.POOL,
            target_layout=None,
            phase=SkillLayoutPhase.POOL_ACTIVE,
            lease_owner=None,
            preparation_id=PREPARATION_ID,
        )
        return True

    def renew_lease(self, **kwargs: object) -> bool:
        return self._owns(kwargs)

    def try_acquire_lease(self, **kwargs: object) -> bool:
        return False

    def _owns(self, values: dict[str, object]) -> bool:
        return (
            values["scope"] == SCOPE
            and values["migration_generation"] == GENERATION
            and values["lease_owner"] == self.state.lease_owner
        )


class FakeSkillRepository:
    def __init__(self, engine: str = "openclaw") -> None:
        self.engine = engine
        self.registered = [
            RegisteredSkillAsset(
                skill_id=11,
                name="local-a",
                git_path="local:///legacy/skills-local/local-a",
            ),
            RegisteredSkillAsset(
                skill_id=12,
                name="local-b",
                git_path="local://local-b",
            ),
        ]
        self.active = [
            *self.registered,
            RegisteredSkillAsset(
                skill_id=21,
                name="repo-skill",
                git_path="git://business/repo-skill",
            ),
        ]

    def list_bot_local_assets(
        self, *, env: str, bot_id: str
    ) -> list[RegisteredSkillAsset]:
        assert (env, bot_id) == (SCOPE.env, SCOPE.bot_id)
        return self.registered

    def list_bot_active_assets(
        self,
        *,
        env: str,
        bot_id: str,
        user_id: str,
        engine: str,
    ) -> list[RegisteredSkillAsset]:
        assert (env, bot_id) == (SCOPE.env, SCOPE.bot_id)
        assert (user_id, engine) == ("owner-1", self.engine)
        return self.active


class FakeRuntime:
    def __init__(
        self,
        *,
        engine: str = "openclaw",
        pool_local: str | None = None,
        pool_repo: str | None = None,
    ) -> None:
        paths = pool_paths_for_engine(engine)
        self.engine = engine
        self.pool_local = pool_local or paths.pool_local
        self.pool_repo = pool_repo or paths.pool_repo
        self.events: list[str] = []
        self.publish_success = True
        self.verify_success = True
        self.physical_cutovers = 0
        self.cutover_result = PoolCutoverResult(
            committed=True,
            status=PoolCutoverStatus.COMMITTED,
            evidence={
                "local_inventory": {
                    "registered": 2,
                    "unregistered": 1,
                    "total": 3,
                }
            },
        )
        self.probe_result = RuntimeLayoutProbeResult(
            status=RuntimeLayoutProbeStatus.READY,
            engine=engine,
            layout_contract_version="skills-pool-p3-v1",
            preparation_id=PREPARATION_ID,
            evidence={"marker": "valid"},
        )

    async def probe(self, **kwargs: object) -> RuntimeLayoutProbeResult:
        self.events.append("probe")
        assert kwargs["engine"] == self.engine
        return self.probe_result

    async def cutover(self, **kwargs: object) -> PoolCutoverResult:
        self.events.append("cutover")
        assert kwargs["registered_local_names"] == ["local-a", "local-b"]
        mappings = kwargs["mappings"]
        assert [mapping.source for mapping in mappings] == [
            f"{self.pool_local}/local-a",
            f"{self.pool_local}/local-b",
            f"{self.pool_repo}/business/repo-skill",
        ]
        if (
            self.cutover_result.committed
            and self.cutover_result.status is PoolCutoverStatus.COMMITTED
        ):
            self.physical_cutovers += 1
        return self.cutover_result

    async def publish_mappings(self, **kwargs: object) -> bool:
        self.events.append("mapping")
        return self.publish_success

    async def verify_mappings(self, **kwargs: object) -> bool:
        self.events.append("verify")
        return self.verify_success


def build_service(
    layouts: FakeLayoutRepository,
    runtime: FakeRuntime,
    *,
    engine: str = "openclaw",
) -> SkillsPoolReconcileService:
    return SkillsPoolReconcileService(
        bot_repository=FakeBotRepository(engine),
        layout_repository=layouts,
        skill_repository=FakeSkillRepository(engine),
        runtime=runtime,
    )


@pytest.mark.asyncio
async def test_ready_claimed_bot_completes_pool_activation() -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.POOL_ACTIVE
    assert runtime.events == ["probe", "cutover", "mapping", "verify"]
    assert layouts.events == ["ready", "begin", "cutover", "database"]
    assert layouts.committed_locators == {
        11: (
            "local:///home/admin/.openclaw/workspace/skills-pool/skills-local/local-a"
        ),
        12: (
            "local:///home/admin/.openclaw/workspace/skills-pool/skills-local/local-b"
        ),
    }


@pytest.mark.asyncio
async def test_claude_code_uses_its_own_pool_paths_for_full_activation() -> None:
    layouts = FakeLayoutRepository()
    pool_local = "/home/admin/.claude_code/workspace/skills-pool/skills-local"
    pool_repo = "/home/admin/.claude_code/workspace/skills-pool/skills-repo"
    runtime = FakeRuntime(
        engine="claude_code",
        pool_local=pool_local,
        pool_repo=pool_repo,
    )

    result = await build_service(
        layouts,
        runtime,
        engine="claude_code",
    ).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.POOL_ACTIVE
    assert runtime.events == ["probe", "cutover", "mapping", "verify"]
    assert layouts.committed_locators == {
        11: f"local://{pool_local}/local-a",
        12: f"local://{pool_local}/local-b",
    }


@pytest.mark.asyncio
async def test_aicoding_uses_its_own_pool_paths_for_full_activation() -> None:
    layouts = FakeLayoutRepository()
    pool_local = "/home/admin/.aicoding/workspace/skills-pool/skills-local"
    pool_repo = "/home/admin/.aicoding/workspace/skills-pool/skills-repo"
    runtime = FakeRuntime(
        engine="aicoding",
        pool_local=pool_local,
        pool_repo=pool_repo,
    )

    result = await build_service(
        layouts,
        runtime,
        engine="aicoding",
    ).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.POOL_ACTIVE
    assert runtime.events == ["probe", "cutover", "mapping", "verify"]
    assert layouts.committed_locators == {
        11: f"local://{pool_local}/local-a",
        12: f"local://{pool_local}/local-b",
    }


@pytest.mark.asyncio
async def test_hermes_h0_ready_uses_its_own_pool_paths_for_full_activation() -> None:
    layouts = FakeLayoutRepository()
    pool_local = "/home/admin/.hermes/workspace/skills-pool/skills-local"
    pool_repo = "/home/admin/.hermes/workspace/skills-pool/skills-repo"
    runtime = FakeRuntime(
        engine="hermes",
        pool_local=pool_local,
        pool_repo=pool_repo,
    )
    runtime.probe_result = replace(
        runtime.probe_result,
        evidence={
            "checks": {
                "legacy_local_bridge_valid": True,
                "stable_repo_bridge_valid": True,
            }
        },
    )

    result = await build_service(
        layouts,
        runtime,
        engine="hermes",
    ).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.POOL_ACTIVE
    assert runtime.events == ["probe", "cutover", "mapping", "verify"]
    assert layouts.committed_locators == {
        11: f"local://{pool_local}/local-a",
        12: f"local://{pool_local}/local-b",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "aicoding", "hermes"])
async def test_mapping_failure_after_cutover_does_not_commit_database(
    engine: str,
) -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime(engine=engine)
    runtime.publish_success = False

    result = await build_service(
        layouts,
        runtime,
        engine=engine,
    ).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.MAPPING_FAILED
    assert layouts.state.data_plane_cutover_committed is True
    assert layouts.state.active_layout is SkillLayout.LEGACY
    assert layouts.committed_locators is None
    assert runtime.events == ["probe", "cutover", "mapping"]
    assert layouts.events == [
        "ready",
        "begin",
        "cutover",
        "post_failure",
    ]
    assert layouts.state.last_failure_stage == "mapping_publish"
    assert layouts.state.last_failure_retryable is True

    runtime.publish_success = True
    runtime.events.clear()
    layouts.events.clear()
    retried = await build_service(
        layouts,
        runtime,
        engine=engine,
    ).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert retried.outcome is SkillsPoolReconcileOutcome.POOL_ACTIVE
    assert runtime.events == ["probe", "mapping", "verify"]
    assert layouts.events == ["database"]


@pytest.mark.asyncio
async def test_unknown_cutover_enters_manual_repair_and_stops_automation() -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()
    runtime.cutover_result = PoolCutoverResult(
        committed=False,
        status=PoolCutoverStatus.UNKNOWN,
        evidence={"reason": "response_lost_after_request"},
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.MANUAL_REPAIR_REQUIRED
    assert result.retryable is False
    assert layouts.events == ["ready", "begin", "manual_repair"]
    assert layouts.state.phase is SkillLayoutPhase.NEEDS_MANUAL_REPAIR
    assert layouts.state.last_failure_stage == "cutover_outcome_unknown"
    assert layouts.state.last_failure_evidence == runtime.cutover_result.to_dict()

    runtime.events.clear()
    repeated = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )
    assert repeated.outcome is SkillsPoolReconcileOutcome.MANUAL_REPAIR_REQUIRED
    assert runtime.events == []


@pytest.mark.asyncio
async def test_resolved_pool_committed_repair_skips_duplicate_cutover_ledger_cas() -> None:
    layouts = FakeLayoutRepository(
        claimed_state(
            phase=SkillLayoutPhase.POOL_CUTOVER_COMMITTED,
            preparation_id=PREPARATION_ID,
            data_plane_cutover_committed=True,
            last_failure_code="MANUAL_REPAIR_RESOLVED",
        )
    )
    runtime = FakeRuntime()
    runtime.cutover_result = PoolCutoverResult(
        committed=True,
        status=PoolCutoverStatus.ALREADY_COMMITTED,
        evidence={"active_marker": "same-generation"},
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.POOL_ACTIVE
    assert runtime.events == ["probe", "cutover", "mapping", "verify"]
    assert layouts.events == ["database"]
    assert layouts.state.active_layout is SkillLayout.POOL


@pytest.mark.asyncio
async def test_post_cutover_sync_pending_retries_finalization_before_mappings() -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()
    runtime.cutover_result = PoolCutoverResult(
        committed=False,
        status=PoolCutoverStatus.POST_CUTOVER_SYNC_PENDING,
        evidence={"reason": "post_cutover_sync_failed"},
    )

    first = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert first.outcome is SkillsPoolReconcileOutcome.CUTOVER_FAILED
    assert first.retryable is True
    assert layouts.state.phase is SkillLayoutPhase.POOL_CUTOVER_FINALIZING
    assert layouts.state.data_plane_cutover_committed is True
    assert runtime.events == ["probe", "cutover"]
    assert layouts.events == ["ready", "begin", "finalizing"]

    runtime.events.clear()
    layouts.events.clear()
    runtime.cutover_result = PoolCutoverResult(
        committed=True,
        status=PoolCutoverStatus.ALREADY_COMMITTED,
        evidence={"post_sync": {"copied": 1}},
    )
    second = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert second.outcome is SkillsPoolReconcileOutcome.POOL_ACTIVE
    assert runtime.events == ["probe", "cutover", "mapping", "verify"]
    assert layouts.events == ["cutover", "database"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_status", "expected_outcome"),
    [
        (
            "DATA_INCONSISTENT",
            SkillsPoolReconcileOutcome.DATA_INCONSISTENT,
        ),
        (
            "ACTIVE_ENTRY_CONFLICT",
            SkillsPoolReconcileOutcome.ACTIVE_ENTRY_CONFLICT,
        ),
    ],
)
async def test_structural_cutover_failure_is_persisted_without_data_plane_change(
    runtime_status: str,
    expected_outcome: SkillsPoolReconcileOutcome,
) -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()
    runtime.cutover_result = PoolCutoverResult(
        committed=False,
        status=PoolCutoverStatus(runtime_status),
        evidence={
            "reason": "unsafe_filesystem_truth",
            "affected": ["handmade"],
        },
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is expected_outcome
    assert layouts.events == ["ready", "begin", "failure"]
    assert layouts.state.active_layout is SkillLayout.LEGACY
    assert layouts.state.data_plane_cutover_committed is False
    assert layouts.state.last_failure_code == runtime_status
    assert layouts.state.last_failure_stage == "pre_cutover_validation"
    assert layouts.state.last_failure_retryable is False
    assert layouts.state.last_failure_evidence == runtime.cutover_result.to_dict()
    assert runtime.events == ["probe", "cutover"]
    assert layouts.committed_locators is None


@pytest.mark.asyncio
async def test_non_atomic_cutover_is_persisted_and_keeps_legacy() -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()
    runtime.cutover_result = PoolCutoverResult(
        committed=False,
        status=PoolCutoverStatus.NOT_ATOMIC,
        evidence={"reason": "atomic_exchange_unavailable"},
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.CUTOVER_FAILED
    assert layouts.events == ["ready", "begin", "failure"]
    assert layouts.state.active_layout is SkillLayout.LEGACY
    assert layouts.state.last_failure_code == "NOT_ATOMIC"
    assert layouts.state.last_failure_stage == "atomic_cutover"
    assert layouts.state.last_failure_retryable is False
    assert layouts.state.last_failure_evidence == runtime.cutover_result.to_dict()
    assert runtime.events == ["probe", "cutover"]


@pytest.mark.asyncio
async def test_non_ready_runtime_keeps_legacy_without_data_plane_changes() -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()
    runtime.probe_result = replace(
        runtime.probe_result,
        status=RuntimeLayoutProbeStatus.NOT_CAPABLE,
        preparation_id=None,
        evidence={"reason": "pool_marker_missing"},
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.NOT_CAPABLE
    assert runtime.events == ["probe"]
    assert layouts.events == ["not_capable_release"]
    assert layouts.state.active_layout is SkillLayout.LEGACY
    assert layouts.state.target_layout is None
    assert layouts.state.phase is SkillLayoutPhase.LEGACY_ACTIVE
    assert layouts.state.migration_generation is None
    assert layouts.state.last_probe_result == "NOT_CAPABLE"
    assert layouts.state.last_probe_evidence == {"reason": "pool_marker_missing"}


@pytest.mark.asyncio
async def test_not_capable_release_race_is_not_reported_as_complete() -> None:
    layouts = FakeLayoutRepository()
    layouts.fail_once_at = "not_capable_release"
    runtime = FakeRuntime()
    runtime.probe_result = replace(
        runtime.probe_result,
        status=RuntimeLayoutProbeStatus.NOT_CAPABLE,
        preparation_id=None,
        evidence={"reason": "pool_marker_missing"},
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.STATE_RACE_LOST
    assert layouts.state.phase is SkillLayoutPhase.POOL_PREPARING
    assert layouts.state.migration_generation == GENERATION


@pytest.mark.asyncio
async def test_mixed_image_bots_reconcile_independently_in_one_environment() -> None:
    old_scope = BotSkillLayoutScope(
        env="pre",
        entity_id="entity-2",
        bot_id="bot-2",
    )

    class MultiBotRepository:
        def get_by_id_and_entity(
            self,
            bot_id: str,
            entity_id: str,
        ) -> dict[str, object] | None:
            bots = {
                (SCOPE.bot_id, SCOPE.entity_id): {
                    "bot_id": SCOPE.bot_id,
                    "entity_id": SCOPE.entity_id,
                    "owner_id": "owner-1",
                    "env": SCOPE.env,
                    "active_engine": "openclaw",
                },
                (old_scope.bot_id, old_scope.entity_id): {
                    "bot_id": old_scope.bot_id,
                    "entity_id": old_scope.entity_id,
                    "owner_id": "owner-2",
                    "env": old_scope.env,
                    "active_engine": "openclaw",
                },
            }
            return bots.get((bot_id, entity_id))

    class MultiLayoutRepository(FakeLayoutRepository):
        def __init__(self) -> None:
            super().__init__(claimed_state())
            self._current_scope = SCOPE
            self._states = {
                SCOPE: self.state,
                old_scope: replace(
                    claimed_state(),
                    scope=old_scope,
                    migration_generation="generation-2",
                    lease_owner="worker-2",
                ),
            }

        def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
            self._states[self._current_scope] = self.state
            self._current_scope = scope
            self.state = self._states[scope]
            return self.state

        def state_for(
            self,
            scope: BotSkillLayoutScope,
        ) -> BotSkillLayoutState:
            self._states[self._current_scope] = self.state
            return self._states[scope]

        def _owns(self, values: dict[str, object]) -> bool:
            return (
                values["scope"] == self._current_scope
                and values["migration_generation"]
                == self.state.migration_generation
                and values["lease_owner"] == self.state.lease_owner
            )

    class MixedImageRuntime(FakeRuntime):
        async def probe(self, **kwargs: object) -> RuntimeLayoutProbeResult:
            if kwargs["bot_id"] == old_scope.bot_id:
                self.events.append("probe:old-image")
                return replace(
                    self.probe_result,
                    status=RuntimeLayoutProbeStatus.NOT_CAPABLE,
                    preparation_id=None,
                )
            return await super().probe(**kwargs)

    layouts = MultiLayoutRepository()
    runtime = MixedImageRuntime()
    service = SkillsPoolReconcileService(
        bot_repository=MultiBotRepository(),
        layout_repository=layouts,
        skill_repository=FakeSkillRepository(),
        runtime=runtime,
    )

    ready_result = await service.reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )
    old_result = await service.reconcile(
        scope=old_scope,
        lease_owner="worker-2",
    )

    assert ready_result.outcome is SkillsPoolReconcileOutcome.POOL_ACTIVE
    assert old_result.outcome is SkillsPoolReconcileOutcome.NOT_CAPABLE
    assert layouts.state_for(SCOPE).active_layout is SkillLayout.POOL
    old_state = layouts.state_for(old_scope)
    assert old_state.active_layout is SkillLayout.LEGACY
    assert old_state.target_layout is None
    assert old_state.phase is SkillLayoutPhase.LEGACY_ACTIVE
    assert old_state.migration_generation is None
    assert old_state.last_probe_result == "NOT_CAPABLE"
    assert runtime.physical_cutovers == 1
    assert runtime.events == [
        "probe",
        "cutover",
        "mapping",
        "verify",
        "probe:old-image",
    ]


@pytest.mark.asyncio
async def test_invalid_probe_is_persisted_as_non_retryable_blocker() -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()
    runtime.probe_result = replace(
        runtime.probe_result,
        status=RuntimeLayoutProbeStatus.INVALID,
        preparation_id=None,
        evidence={"reason": "marker_contract_mismatch"},
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.INVALID
    assert result.retryable is False
    assert layouts.events == ["failure"]
    assert layouts.state.last_failure_code == "INVALID"
    assert layouts.state.last_failure_stage == "runtime_probe"
    assert layouts.state.last_failure_retryable is False
    assert layouts.state.last_failure_evidence == {"reason": "marker_contract_mismatch"}
    assert runtime.events == ["probe"]


@pytest.mark.asyncio
async def test_transient_probe_failure_is_persisted_as_retryable() -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()
    runtime.probe_result = replace(
        runtime.probe_result,
        status=RuntimeLayoutProbeStatus.TRANSIENT_ERROR,
        preparation_id=None,
        evidence={"reason": "runtime_unreachable"},
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.TRANSIENT_ERROR
    assert result.retryable is True
    assert layouts.events == ["failure"]
    assert layouts.state.last_failure_code == "TRANSIENT_ERROR"
    assert layouts.state.last_failure_stage == "runtime_probe"
    assert layouts.state.last_failure_retryable is True


@pytest.mark.asyncio
async def test_ready_probe_contract_mismatch_is_persisted_as_blocker() -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()
    runtime.probe_result = replace(
        runtime.probe_result,
        layout_contract_version="future-contract",
        evidence={"reason": "contract_drift"},
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.INVALID
    assert result.retryable is False
    assert layouts.events == ["failure"]
    assert layouts.state.last_failure_code == "INVALID"
    assert layouts.state.last_failure_stage == "runtime_probe"
    assert layouts.state.last_failure_evidence == {"reason": "contract_drift"}
    assert runtime.events == ["probe"]


@pytest.mark.asyncio
async def test_post_cutover_invalid_probe_records_forward_only_failure() -> None:
    layouts = FakeLayoutRepository(
        claimed_state(
            phase=SkillLayoutPhase.POOL_CUTOVER_COMMITTED,
            data_plane_cutover_committed=True,
        )
    )
    runtime = FakeRuntime()
    runtime.probe_result = replace(
        runtime.probe_result,
        status=RuntimeLayoutProbeStatus.INVALID,
        preparation_id=None,
        evidence={"reason": "post_cutover_probe_invalid"},
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.INVALID
    assert result.retryable is False
    assert layouts.events == ["post_failure"]
    assert layouts.state.last_failure_stage == "runtime_probe"
    assert runtime.events == ["probe"]


@pytest.mark.asyncio
async def test_stale_generation_or_lease_cannot_continue_activation() -> None:
    layouts = FakeLayoutRepository(
        claimed_state(lease_owner="another-worker"),
    )
    runtime = FakeRuntime()

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.LEASE_NOT_HELD
    assert runtime.events == []


@pytest.mark.asyncio
async def test_expired_lease_cannot_probe_or_publish_mappings() -> None:
    layouts = FakeLayoutRepository()
    layouts.lease_valid = False
    runtime = FakeRuntime()

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.LEASE_NOT_HELD
    assert runtime.events == []


@pytest.mark.asyncio
async def test_pool_active_reconciliation_probes_current_runtime() -> None:
    layouts = FakeLayoutRepository(
        claimed_state(
            active_layout=SkillLayout.POOL,
            target_layout=None,
            phase=SkillLayoutPhase.POOL_ACTIVE,
            lease_owner=None,
            preparation_id=PREPARATION_ID,
        )
    )
    runtime = FakeRuntime()

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="post-restart-worker",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.ALREADY_ACTIVE
    assert result.evidence == {"marker": "valid"}
    assert runtime.events == ["probe"]


@pytest.mark.asyncio
async def test_pool_active_reconciliation_rejects_invalid_current_runtime() -> None:
    layouts = FakeLayoutRepository(
        claimed_state(
            active_layout=SkillLayout.POOL,
            target_layout=None,
            phase=SkillLayoutPhase.POOL_ACTIVE,
            lease_owner=None,
        )
    )
    runtime = FakeRuntime()
    runtime.probe_result = replace(
        runtime.probe_result,
        status=RuntimeLayoutProbeStatus.INVALID,
        preparation_id=None,
        evidence={"reason": "bridge_invalid"},
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="post-restart-worker",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.INVALID
    assert result.evidence == {"reason": "bridge_invalid"}
    assert runtime.events == ["probe"]


class StickyClaimService:
    """Expose the real reconciliation service through the durable task seam."""

    def __init__(self, layouts: FakeLayoutRepository) -> None:
        self._layouts = layouts
        self._calls = 0

    def claim(self, **kwargs: object) -> MigrationClaimResult:
        self._calls += 1
        if self._calls == 1:
            self._layouts.state = replace(
                self._layouts.state,
                lease_owner=str(kwargs["lease_owner"]),
            )
        return MigrationClaimResult(
            (
                MigrationClaimOutcome.CLAIMED
                if self._calls == 1
                else MigrationClaimOutcome.ALREADY_CLAIMED
            ),
            self._layouts.state,
        )


class NoQuarantineRepository:
    def get_quarantine(
        self,
        scope: BotSkillLayoutScope,
        migration_generation: str,
    ) -> None:
        return None


def build_task_handler(
    layouts: FakeLayoutRepository,
    runtime: FakeRuntime,
) -> SkillsPoolReconcileTaskHandler:
    return SkillsPoolReconcileTaskHandler(
        claim_service=StickyClaimService(layouts),
        layout_repository=layouts,
        reconcile_service=build_service(layouts, runtime),
        quarantine_repository=NoQuarantineRepository(),
    )


def task_payload() -> dict[str, object]:
    return build_skills_pool_reconcile_payload(
        scope=SCOPE,
        source="test",
        signal_identity={"binding_id": 1},
        wakeup_id="wakeup-1",
    )


@pytest.mark.parametrize(
    "failure",
    [
        "transient_probe",
        "ready_state_race",
        "begin_state_race",
        "retryable_cutover",
        "cutover_ledger_state_race",
        "mapping",
        "verify",
        "database",
    ],
)
def test_real_reconciliation_retry_resumes_same_generation_without_repeating_cutover(
    failure: str,
) -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()
    original_generation = layouts.state.migration_generation

    if failure == "transient_probe":
        ready = runtime.probe_result
        runtime.probe_result = replace(
            ready,
            status=RuntimeLayoutProbeStatus.TRANSIENT_ERROR,
            preparation_id=None,
        )
    elif failure == "ready_state_race":
        layouts.fail_once_at = "ready"
    elif failure == "begin_state_race":
        layouts.fail_once_at = "begin"
    elif failure == "retryable_cutover":
        runtime.cutover_result = PoolCutoverResult(
            committed=False,
            status=PoolCutoverStatus.TRANSIENT_ERROR,
            evidence={"reason": "runtime unavailable"},
        )
    elif failure == "cutover_ledger_state_race":
        layouts.fail_once_at = "cutover_commit"
    elif failure == "mapping":
        runtime.publish_success = False
    elif failure == "verify":
        runtime.verify_success = False
    elif failure == "database":
        layouts.fail_once_at = "database"

    handler = build_task_handler(layouts, runtime)
    first = handler.handle(task_payload())
    cutovers_after_first = runtime.events.count("cutover")

    runtime.probe_result = RuntimeLayoutProbeResult(
        status=RuntimeLayoutProbeStatus.READY,
        engine="openclaw",
        layout_contract_version=LAYOUT_CONTRACT_VERSION,
        preparation_id=PREPARATION_ID,
        evidence={"marker": "valid"},
    )
    runtime.cutover_result = PoolCutoverResult(
        committed=True,
        status=(
            PoolCutoverStatus.ALREADY_COMMITTED
            if failure == "cutover_ledger_state_race"
            else PoolCutoverStatus.COMMITTED
        ),
        evidence={"local_inventory": {"registered": 2}},
    )
    runtime.publish_success = True
    runtime.verify_success = True

    second = handler.handle(task_payload())

    assert isinstance(first, Retry)
    assert second == Complete()
    assert layouts.state.migration_generation == original_generation
    assert layouts.state.active_layout is SkillLayout.POOL
    if failure in {"mapping", "verify", "database"}:
        assert cutovers_after_first == 1
        assert runtime.events.count("cutover") == 1
    if failure == "cutover_ledger_state_race":
        assert cutovers_after_first == 1
        assert runtime.events.count("cutover") == 2
        assert runtime.physical_cutovers == 1


class CurrentBindingResolver:
    def __init__(self) -> None:
        self.current_binding = "binding-new"
        self.calls: list[tuple[str, str]] = []

    def resolve_current_runtime_for_bot(self, bot_id: str, user_id: str):
        self.calls.append((bot_id, user_id))
        return SimpleNamespace(
            conn_info={"binding": self.current_binding},
        )


class RecordingTransport:
    def __init__(self) -> None:
        self.bindings: list[str] = []

    async def invoke(
        self,
        conn_info,
        method,
        path,
        *,
        body,
        timeout,
    ):
        self.bindings.append(conn_info["binding"])
        if path.endswith("/activate"):
            return {
                "success": True,
                "data": {
                    "committed": True,
                    "status": "COMMITTED",
                    "evidence": {},
                },
            }
        if path.endswith("/publish"):
            return {"success": True}
        return {"success": True, "data": {"valid": True}}


class ReadyProbeService:
    async def probe_bot(self, **kwargs):
        return RuntimeLayoutProbeResult(
            status=RuntimeLayoutProbeStatus.READY,
            engine="openclaw",
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            preparation_id=PREPARATION_ID,
            evidence={"marker": "valid"},
        )


def test_stale_signal_uses_current_resolved_binding_for_real_mutations() -> None:
    layouts = FakeLayoutRepository()
    resolver = CurrentBindingResolver()
    transport = RecordingTransport()
    runtime = OpenClawSkillsPoolRuntime(
        resolver=resolver,
        adapter_transport=transport,
        probe_service=ReadyProbeService(),
    )
    reconcile = SkillsPoolReconcileService(
        bot_repository=FakeBotRepository(),
        layout_repository=layouts,
        skill_repository=FakeSkillRepository(),
        runtime=runtime,
    )
    handler = SkillsPoolReconcileTaskHandler(
        claim_service=StickyClaimService(layouts),
        layout_repository=layouts,
        reconcile_service=reconcile,
        quarantine_repository=NoQuarantineRepository(),
    )
    payload = build_skills_pool_reconcile_payload(
        scope=SCOPE,
        source="arca_device_alive",
        signal_identity={
            "binding_id": 7,
            "device_id": "device-old",
            "sandbox_id": "sandbox-old",
        },
        wakeup_id="stale-wakeup",
    )

    outcome = handler.handle(payload)

    assert outcome == Complete()
    assert resolver.calls == [
        ("bot-1", "owner-1"),
        ("bot-1", "owner-1"),
        ("bot-1", "owner-1"),
    ]
    assert transport.bindings == ["binding-new", "binding-new", "binding-new"]

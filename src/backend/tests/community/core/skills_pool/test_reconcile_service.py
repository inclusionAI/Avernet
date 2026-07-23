"""OpenClaw Skills Pool 已登记技能激活闭环测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.skills_pool.models import (
    OpenClawPoolPaths,
    RegisteredSkillAsset,
)
from agentclaw.community.core.skills_pool.reconcile_service import (
    SkillsPoolReconcileOutcome,
    SkillsPoolReconcileService,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)


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
    def __init__(self) -> None:
        self.bot: dict[str, object] | None = {
            "bot_id": SCOPE.bot_id,
            "entity_id": SCOPE.entity_id,
            "owner_id": "owner-1",
            "env": SCOPE.env,
            "active_engine": "openclaw",
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

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        assert scope == SCOPE
        return self.state

    def record_ready_probe(self, **kwargs: object) -> bool:
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

    def holds_lease(self, **kwargs: object) -> bool:
        return self.lease_valid and self._owns(kwargs)

    def record_cutover_committed(self, **kwargs: object) -> bool:
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
        if not self._owns(kwargs):
            return False
        self.events.append("begin")
        self.state = replace(
            self.state,
            phase=SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER,
        )
        return True

    def commit_pool_active(
        self,
        *,
        local_locators: dict[int, str],
        **kwargs: object,
    ) -> bool:
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
        )
        return True

    def _owns(self, values: dict[str, object]) -> bool:
        return (
            values["scope"] == SCOPE
            and values["migration_generation"] == GENERATION
            and values["lease_owner"] == "worker-1"
        )


class FakeSkillRepository:
    def __init__(self) -> None:
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
        assert (user_id, engine) == ("owner-1", "openclaw")
        return self.active


class FakeRuntime:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.publish_success = True
        self.probe_result = RuntimeLayoutProbeResult(
            status=RuntimeLayoutProbeStatus.READY,
            engine="openclaw",
            layout_contract_version="skills-pool-p3-v1",
            preparation_id=PREPARATION_ID,
            evidence={"marker": "valid"},
        )

    async def probe(self, **kwargs: object) -> RuntimeLayoutProbeResult:
        self.events.append("probe")
        return self.probe_result

    async def cutover(self, **kwargs: object) -> dict[str, object]:
        self.events.append("cutover")
        assert kwargs["registered_local_names"] == ["local-a", "local-b"]
        mappings = kwargs["mappings"]
        assert [mapping.source for mapping in mappings] == [
            f"{OpenClawPoolPaths().pool_local}/local-a",
            f"{OpenClawPoolPaths().pool_local}/local-b",
            f"{OpenClawPoolPaths().pool_repo}/business/repo-skill",
        ]
        return {"committed": True, "bridge": "valid"}

    async def publish_mappings(self, **kwargs: object) -> bool:
        self.events.append("mapping")
        return self.publish_success

    async def verify_mappings(self, **kwargs: object) -> bool:
        self.events.append("verify")
        return True


def build_service(
    layouts: FakeLayoutRepository,
    runtime: FakeRuntime,
) -> SkillsPoolReconcileService:
    return SkillsPoolReconcileService(
        bot_repository=FakeBotRepository(),
        layout_repository=layouts,
        skill_repository=FakeSkillRepository(),
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
            "local:///home/admin/.openclaw/workspace/"
            "skills-pool/skills-local/local-a"
        ),
        12: (
            "local:///home/admin/.openclaw/workspace/"
            "skills-pool/skills-local/local-b"
        ),
    }


@pytest.mark.asyncio
async def test_mapping_failure_after_cutover_does_not_commit_database() -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()
    runtime.publish_success = False

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.MAPPING_FAILED
    assert layouts.state.data_plane_cutover_committed is True
    assert layouts.state.active_layout is SkillLayout.LEGACY
    assert layouts.committed_locators is None
    assert runtime.events == ["probe", "cutover", "mapping"]

    runtime.publish_success = True
    runtime.events.clear()
    layouts.events.clear()
    retried = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert retried.outcome is SkillsPoolReconcileOutcome.POOL_ACTIVE
    assert runtime.events == ["probe", "mapping", "verify"]
    assert layouts.events == ["database"]


@pytest.mark.asyncio
async def test_non_ready_runtime_keeps_legacy_without_data_plane_changes() -> None:
    layouts = FakeLayoutRepository()
    runtime = FakeRuntime()
    runtime.probe_result = replace(
        runtime.probe_result,
        status=RuntimeLayoutProbeStatus.NOT_CAPABLE,
        preparation_id=None,
    )

    result = await build_service(layouts, runtime).reconcile(
        scope=SCOPE,
        lease_owner="worker-1",
    )

    assert result.outcome is SkillsPoolReconcileOutcome.NOT_CAPABLE
    assert runtime.events == ["probe"]
    assert layouts.events == []
    assert layouts.state.active_layout is SkillLayout.LEGACY


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

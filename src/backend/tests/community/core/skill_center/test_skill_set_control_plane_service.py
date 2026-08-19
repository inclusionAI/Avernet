"""Control-plane invariants that cannot live in the HTTP adapter."""

from __future__ import annotations

import pytest

from agentclaw.community.core.repository.implementations.skill_center.skill_set_control_plane import (
    SkillSetDesiredState,
    SkillSetMutation,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneNotFoundError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.services.skill_set_control_plane import (
    SkillSetControlPlaneService,
)
from agentclaw.community.core.skill_center.services.bot_capability_mutation_guard import (
    BotCapabilityMutationGuard,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class _Guard:
    def __init__(self) -> None:
        self.held = False
        self.scopes = []

    def acquire_for_edit(self, *, scope):
        assert not self.held
        self.held = True
        self.scopes.append(scope)
        return object()

    def release(self, _lease) -> bool:
        assert self.held
        self.held = False
        return True


class _MutationGuard:
    def acquire(self, *, scope):
        assert scope.bot_id == "bot-1"
        return object()

    def release(self, _lease) -> bool:
        return True


class _Repository:
    def __init__(self) -> None:
        self.restore_calls = []

    def set_active(self, **_kwargs) -> SkillSetMutation:
        return SkillSetMutation(
            item={"id": "set-1", "name": "set", "is_default": False, "is_active": True},
            changed=True,
            previous_state=SkillSetDesiredState(set(), {}, {}),
        )

    def restore_desired_state(self, **kwargs) -> None:
        self.restore_calls.append(kwargs)


class _Bots:
    def get_by_id(self, bot_id: str) -> dict:
        assert bot_id == "bot-1"
        return {
            "owner_id": "true-owner",
            "env": "dev",
            "entity_id": "entity-1",
            "active_engine": "openclaw",
            "entity_type": "staff",
            "status": "ACTIVE",
        }


class _Collaborators:
    def __init__(self) -> None:
        self.calls = []

    def check_collaborator_permission(self, *args):
        self.calls.append(args)
        return {"has_permission": True}


class _Runtime:
    def __init__(self, guard: _Guard) -> None:
        self._guard = guard
        self.owners = []

    async def reconcile(self, *, bot_id: str, owner_id: str) -> None:
        assert bot_id == "bot-1"
        assert self._guard.held
        self.owners.append(owner_id)
        if len(self.owners) == 1:
            raise RuntimeError("runtime failed")


class _Audit:
    def insert(self, _data) -> None:
        return None


class _Cache:
    def __init__(self) -> None:
        self.held: dict[str, str] = {}

    def acquire_lock_strict(self, key: str, ttl: int) -> str | None:
        if key in self.held:
            return None
        self.held[key] = "token"
        return "token"

    def release_lock(self, key: str, token: str) -> bool:
        if self.held.get(key) != token:
            return False
        del self.held[key]
        return True


class _BlockingRuntime:
    def __init__(self, started, unblock) -> None:
        self._started = started
        self._unblock = unblock

    async def reconcile(self, **_kwargs) -> None:
        self._started.set()
        await self._unblock.wait()


class _SuccessfulRuntime:
    async def reconcile(self, **_kwargs) -> None:
        return None


class _FailingReleaseGuard(_Guard):
    def release(self, lease) -> bool:
        super().release(lease)
        raise RuntimeError("pool release failed")


class _LegacyResolutionRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.identifiers: list[str] = []

    def resolve_legacy_skill_id(self, *, bot_id: str, identifier: str) -> str:
        assert bot_id == "bot-1"
        self.identifiers.append(identifier)
        raise SkillSetControlPlaneNotFoundError()


class _LegacySkillSetService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def resolve_or_create_legacy_market_skill(
        self, *, identifier: str, owner_id: str, bot_id: str
    ) -> str:
        self.calls.append((identifier, owner_id, bot_id))
        return "stable-skill-id"


class _LegacyFactory:
    def __init__(self) -> None:
        self.service = _LegacySkillSetService()
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.service


@pytest.mark.asyncio
async def test_collaborator_command_keeps_one_guard_through_restore_and_uses_true_owner():
    guard = _Guard()
    repository = _Repository()
    collaborators = _Collaborators()
    runtime = _Runtime(guard)
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        collaborators=collaborators,
        mutation_guard=_MutationGuard(),
        edit_guard=guard,
        audit_log_repo=_Audit(),
    )

    with pytest.raises(SkillSetRuntimeReconcileError):
        await service.activate(bot_id="bot-1", actor_id="collaborator", set_id="set-1")

    assert collaborators.calls == [("bot-1", "true-owner", "collaborator", 1)]
    assert runtime.owners == ["true-owner", "true-owner"]
    assert len(repository.restore_calls) == 1
    assert guard.held is False


@pytest.mark.asyncio
async def test_legacy_bot_uses_layout_neutral_fence_for_the_full_runtime_window():
    """A non-Pool Bot still rejects a concurrent mutation while reconcile runs."""
    started = __import__("asyncio").Event()
    unblock = __import__("asyncio").Event()
    repository = _Repository()
    cache = _Cache()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_BlockingRuntime(started, unblock),
        legacy_factory=object(),
        passport=object(),
        collaborators=_Collaborators(),
        mutation_guard=BotCapabilityMutationGuard(cache),
        # This stand-in deliberately has no Pool participation behaviour.  The
        # capability guard, not the Pool guard, is the assertion under test.
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
    )

    first = __import__("asyncio").create_task(
        service.activate(bot_id="bot-1", actor_id="true-owner", set_id="set-1")
    )
    await started.wait()
    with pytest.raises(Exception, match="BOT_MUTATION_BUSY"):
        await service.deactivate(bot_id="bot-1", actor_id="true-owner", set_id="set-1")
    unblock.set()
    await first
    assert cache.held == {}


def test_mutation_guard_keeps_same_env_bot_ids_isolated_by_tenant():
    cache = _Cache()
    guard = BotCapabilityMutationGuard(cache)
    scope = BotSkillLayoutScope(env="dev", entity_id="entity-1", bot_id="bot-1")

    with avernet_tenant_scope("tenant-a"):
        first = guard.acquire(scope=scope)
    with avernet_tenant_scope("tenant-b"):
        second = guard.acquire(scope=scope)
    assert first.key != second.key
    assert guard.release(first)
    assert guard.release(second)


@pytest.mark.asyncio
async def test_skill_set_releases_mutation_lease_when_pool_release_fails():
    cache = _Cache()
    service = SkillSetControlPlaneService(
        repository=_Repository(),
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        collaborators=_Collaborators(),
        mutation_guard=BotCapabilityMutationGuard(cache),
        edit_guard=_FailingReleaseGuard(),
        audit_log_repo=_Audit(),
    )

    with pytest.raises(RuntimeError, match="pool release failed"):
        await service.activate(bot_id="bot-1", actor_id="true-owner", set_id="set-1")
    assert cache.held == {}


def test_legacy_name_or_git_path_materializes_market_repo_skill_before_membership():
    """The legacy batch adapter keeps its historical implicit Repo creation."""
    repository = _LegacyResolutionRepository()
    factory = _LegacyFactory()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=factory,
        passport=object(),
        collaborators=_Collaborators(),
        mutation_guard=_MutationGuard(),
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
    )

    stable_id = service.resolve_legacy_skill_id(
        bot_id="bot-1", actor_id="true-owner", identifier="market/example"
    )

    assert stable_id == "stable-skill-id"
    assert repository.identifiers == ["market/example"]
    assert factory.calls == [
        {
            "entity_id": "entity-1",
            "bot_id": "bot-1",
            "engine_type": "openclaw",
            "entity_type": "staff",
        }
    ]
    assert factory.service.calls == [("market/example", "true-owner", "bot-1")]

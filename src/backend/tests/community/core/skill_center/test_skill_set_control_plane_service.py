"""Control-plane invariants that cannot live in the HTTP adapter."""

from __future__ import annotations

import time

import pytest

from agentclaw.community.core.repository.implementations.skill_center.skill_set_control_plane import (
    SkillSetDesiredState,
    SkillSetMutation,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillSetAccessDeniedError,
    SkillSetControlPlaneNotFoundError,
    SkillSetRuntimeReconcileError,
    SkillEngineNotSupportedError,
    McpPermissionDeniedError,
)
from agentclaw.community.core.skill_center.services.skill_set_control_plane import (
    SkillSetControlPlaneService,
    SkillSetRuntimeReconciler,
)
from agentclaw.community.core.skill_center.services.bot_capability_mutation_guard import (
    BotCapabilityMutationGuard,
    BotCapabilityMutationLockUnavailableError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.core.mcp.services._defaults import get_default_mcp_servers
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

    def ensure_valid(self, _lease) -> None:
        return None


class _AnyMutationGuard(_MutationGuard):
    def acquire(self, *, scope):
        self.scope = scope
        return object()


class _Repository:
    def __init__(self) -> None:
        self.restore_calls = []
        self.set_active_calls = []

    def set_active(self, **kwargs) -> SkillSetMutation:
        self.set_active_calls.append(kwargs)
        return SkillSetMutation(
            item={"id": "set-1", "name": "set", "is_default": False, "is_active": True},
            changed=True,
            previous_state=SkillSetDesiredState(set(), {}, {}),
        )

    def restore_desired_state(self, **kwargs) -> None:
        self.restore_calls.append(kwargs)

    def list_mcps(self, **_kwargs):
        return []

    def get_set(self, **_kwargs):
        return {"is_default": False}


class _CreateRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.create_calls = []

    def create_set(self, **kwargs):
        self.create_calls.append(kwargs)
        return {
            "id": "set-1",
            "name": kwargs["name"],
            "bolt_id": kwargs["bot_id"],
            "is_default": False,
            "is_active": False,
        }


class _LegacyReadRepository(_Repository):
    def __init__(self, *, owner_id: str) -> None:
        super().__init__()
        self.owner_id = owner_id

    def get_set(self, **_kwargs):
        return {
            "id": "set-1",
            "bolt_id": "default",
            "user_id": self.owner_id,
            "is_default": False,
            "is_active": False,
        }


class _Bots:
    def get_unique_by_id(self, bot_id: str) -> dict:
        assert bot_id == "bot-1"
        return {
            "owner_id": "true-owner",
            "env": "dev",
            "entity_id": "entity-1",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "entity_type": "staff",
            "status": "ACTIVE",
        }


class _Collaborators:
    def __init__(self) -> None:
        self.calls = []

    def can_manage_bot(self, **kwargs):
        self.calls.append(kwargs)
        return True


class _DeniedCollaborators(_Collaborators):
    def can_manage_bot(self, **kwargs):
        self.calls.append(kwargs)
        return False


class _MissingBots:
    def get_unique_by_id(self, _bot_id: str):
        return None


class _UnsupportedBots(_Bots):
    def get_unique_by_id(self, bot_id: str) -> dict:
        bot = super().get_unique_by_id(bot_id)
        return {**bot, "bot_type": "desktop", "active_engine": "claude_code"}


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

    def renew_lock_strict(self, key: str, token: str, ttl: int) -> bool:
        return self.held.get(key) == token


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


class _McpRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.direct_calls: list[dict] = []

    def activate_mcp_direct(self, **kwargs) -> SkillSetMutation:
        self.direct_calls.append(kwargs)
        return SkillSetMutation({}, True, SkillSetDesiredState(set(), {}, {}))


class _McpAuth:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str]] = []

    def check_mcp_permission_detail(self, actor_id: str, server_code: str) -> dict:
        self.calls.append((actor_id, server_code))
        return {
            "has_permission": self.allowed,
            "access_level": "LOCAL" if self.allowed else "PRIVATE",
        }

    def apply_permission(self, **_kwargs) -> dict:
        return {"success": True, "process_url": None, "error": None}


class _McpCenter:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str]] = []

    def check_mcp_permission_detail(self, actor_id: str, server_code: str) -> dict:
        self.calls.append((actor_id, server_code))
        return {
            "has_permission": self.allowed,
            "access_level": "LOCAL" if self.allowed else "PRIVATE",
        }

    def get_mcp_detail(self, _server_code: str) -> dict:
        return {"accessLevel": "PUBLIC"}


class _RuntimeFactoryService:
    def __init__(self) -> None:
        self.mcp_codes: set[str] | None = None

    def sync_runtime(self, *, desired_skills: list[dict]) -> bool:
        assert desired_skills == []
        return True

    async def sync_mcp_desired_state(self, *, server_codes: set[str]) -> bool:
        self.mcp_codes = server_codes
        return True


class _RuntimeFactory:
    def __init__(self) -> None:
        self.service = _RuntimeFactoryService()

    def create(self, **_kwargs):
        return self.service


class _RuntimeBots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        assert (bot_id, owner_id) == ("bot-1", "true-owner")
        return {
            "entity_id": "entity-1",
            "active_engine": "openclaw",
            "entity_type": "staff",
            "env": "pre",
        }


class _McpInstallations:
    def list_installed_mcps(self, *, bot_id: str) -> set[str]:
        assert bot_id == "bot-1"
        return {"mcp.weather"}


class _RuntimeSkills:
    def list_bot_active_assets(self, **kwargs):
        assert kwargs == {
            "env": "pre",
            "bot_id": "bot-1",
            "user_id": "true-owner",
            "engine": "openclaw",
        }
        return []


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
        authorization=collaborators,
        mutation_guard=_MutationGuard(),
        edit_guard=guard,
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    with pytest.raises(SkillSetRuntimeReconcileError):
        await service.activate(bot_id="bot-1", actor_id="collaborator", set_id="set-1")

    assert collaborators.calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "actor_id": "collaborator",
        }
    ]
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
        authorization=_Collaborators(),
        mutation_guard=BotCapabilityMutationGuard(cache),
        # This stand-in deliberately has no Pool participation behaviour.  The
        # capability guard, not the Pool guard, is the assertion under test.
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
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


def test_legacy_create_rejects_missing_bot_instead_of_creating_orphan_set():
    service = SkillSetControlPlaneService(
        repository=_Repository(),
        bot_repo=_MissingBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        mutation_guard=_MutationGuard(),
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    with pytest.raises(LocalSkillNotFoundError):
        service.create_legacy_set(
            bot_id="missing",
            actor_id="actor",
            name="set",
            description=None,
            idempotency_key="request-1",
        )


def test_legacy_create_retains_only_virtual_default_bot_compatibility():
    repository = _CreateRepository()
    mutation_guard = _AnyMutationGuard()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_MissingBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        mutation_guard=mutation_guard,
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    result = service.create_legacy_set(
        bot_id="default",
        actor_id="actor",
        name="set",
        description=None,
        idempotency_key="request-1",
    )

    assert result["bolt_id"] == "default"
    assert repository.create_calls[0]["owner_id"] == "actor"
    assert mutation_guard.scope.entity_id == "actor"


def test_legacy_virtual_default_read_is_owner_scoped():
    service = SkillSetControlPlaneService(
        repository=_LegacyReadRepository(owner_id="owner"),
        bot_repo=_MissingBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        mutation_guard=_MutationGuard(),
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    assert service.get_legacy_set(
        bot_id="default", actor_id="owner", set_id="set-1"
    )["id"] == "set-1"
    with pytest.raises(SkillSetAccessDeniedError):
        service.get_legacy_set(
            bot_id="default", actor_id="other", set_id="set-1"
        )


@pytest.mark.asyncio
async def test_legacy_sync_activates_additively_without_replacing_other_sets():
    repository = _Repository()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        mutation_guard=_MutationGuard(),
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    await service.sync(bot_id="bot-1", actor_id="true-owner", set_id="set-1")

    assert repository.set_active_calls == [
        {
            "bot_id": "bot-1",
            "set_id": "set-1",
            "active": True,
            "engine_type": "openclaw",
        }
    ]


def test_skill_set_acl_denial_is_forbidden_not_not_found():
    service = SkillSetControlPlaneService(
        repository=_Repository(),
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_DeniedCollaborators(),
        mutation_guard=_MutationGuard(),
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    with pytest.raises(SkillSetAccessDeniedError):
        service.list_sets(bot_id="bot-1", actor_id="collaborator")


@pytest.mark.asyncio
async def test_skill_set_mutation_fails_closed_for_unsupported_bot_engine_pair():
    repository = _Repository()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_UnsupportedBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        mutation_guard=_MutationGuard(),
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    with pytest.raises(SkillEngineNotSupportedError):
        await service.activate(
            bot_id="bot-1", actor_id="true-owner", set_id="set-1"
        )

    assert repository.set_active_calls == []


def test_skill_set_metadata_mutations_share_the_runtime_matrix_gate():
    service = SkillSetControlPlaneService(
        repository=_Repository(),
        bot_repo=_UnsupportedBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        mutation_guard=_MutationGuard(),
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    operations = [
        lambda: service.create_set(
            bot_id="bot-1",
            actor_id="true-owner",
            name="set",
            description=None,
            idempotency_key="request-1",
        ),
        lambda: service.update_set(
            bot_id="bot-1",
            actor_id="true-owner",
            set_id="set-1",
            name="new-name",
            description=None,
        ),
        lambda: service.delete_set(
            bot_id="bot-1", actor_id="true-owner", set_id="set-1"
        ),
    ]

    for operation in operations:
        with pytest.raises(SkillEngineNotSupportedError):
            operation()


def test_mutation_guard_heartbeat_fails_closed_and_stops_on_release(monkeypatch):
    cache = _Cache()
    guard = BotCapabilityMutationGuard(cache)
    monkeypatch.setattr(guard, "_HEARTBEAT_SECONDS", 0.01)
    cache.renew_lock_strict = lambda *_args, **_kwargs: False
    lease = guard.acquire(
        scope=BotSkillLayoutScope(env="dev", entity_id="entity-1", bot_id="bot-1")
    )

    deadline = time.time() + 1
    while not lease.lost.is_set() and time.time() < deadline:
        time.sleep(0.01)

    with pytest.raises(BotCapabilityMutationLockUnavailableError):
        guard.ensure_valid(lease)
    assert guard.release(lease) is False
    assert lease.heartbeat is not None
    assert not lease.heartbeat.is_alive()


@pytest.mark.asyncio
async def test_skill_set_releases_mutation_lease_when_pool_release_fails():
    cache = _Cache()
    service = SkillSetControlPlaneService(
        repository=_Repository(),
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        mutation_guard=BotCapabilityMutationGuard(cache),
        edit_guard=_FailingReleaseGuard(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
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
        authorization=_Collaborators(),
        mutation_guard=_MutationGuard(),
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
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


@pytest.mark.asyncio
async def test_mcp_direct_activation_checks_permission_before_writing_desired_state():
    repository = _McpRepository()
    auth = _McpAuth(allowed=True)
    mcp_center = _McpCenter(allowed=True)
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        mutation_guard=_MutationGuard(),
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
        mcp_center=mcp_center,
        mcp_auth=auth,
    )

    await service.activate_mcp_direct(
        bot_id="bot-1", actor_id="true-owner", server_code="mcp.weather"
    )

    assert mcp_center.calls == [("true-owner", "mcp.weather")]
    assert repository.direct_calls == [
        {"bot_id": "bot-1", "server_code": "mcp.weather", "engine_type": "openclaw"}
    ]


@pytest.mark.asyncio
async def test_mcp_direct_activation_denies_before_writing_desired_state():
    repository = _McpRepository()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        mutation_guard=_MutationGuard(),
        edit_guard=_Guard(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=False),
        mcp_auth=_McpAuth(allowed=False),
    )

    with pytest.raises(McpPermissionDeniedError):
        await service.activate_mcp_direct(
            bot_id="bot-1", actor_id="true-owner", server_code="mcp.weather"
        )
    assert repository.direct_calls == []


@pytest.mark.asyncio
async def test_runtime_reconcile_projects_full_mcp_desired_state():
    factory = _RuntimeFactory()
    runtime = SkillSetRuntimeReconciler(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        pool_skills=_RuntimeSkills(),
    )

    await runtime.reconcile(bot_id="bot-1", owner_id="true-owner")

    assert factory.service.mcp_codes == {
        "mcp.weather",
        *{
            item["server_code"]
            for item in get_default_mcp_servers("openclaw", None)
            if item.get("server_code")
        },
    }

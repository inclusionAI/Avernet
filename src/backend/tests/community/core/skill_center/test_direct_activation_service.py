"""Unit coverage for the direct (Set-free) activation command service."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dataclasses import replace

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    CapabilityDesiredState,
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    McpPermissionDeniedError,
    SkillSetAccessDeniedError,
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.core.skill_center.services.direct_activation_service import (
    DirectActivationService,
)

pytestmark = pytest.mark.unit

_SCOPE = {"engine_type": "openclaw", "default_engine_types": ("openclaw",)}


class _Repository:
    def __init__(self) -> None:
        self.install_mcp_calls: list[dict] = []
        self.uninstall_mcp_calls: list[dict] = []
        self.install_skill_calls: list[dict] = []
        self.uninstall_skill_calls: list[dict] = []
        self.restore_calls: list[dict] = []

    #: Dependencies the Skill under test declares, mirrored onto the mutation
    #: result the way the real repository fills it under the row lock.
    skill_mcp_codes: frozenset[str] = frozenset()

    def _mutation(self) -> DesiredStateMutation:
        return DesiredStateMutation({}, True, CapabilityDesiredState(set(), {}, {}))

    def _skill_mutation(self) -> DesiredStateMutation:
        # Derived from ``_mutation`` rather than rebuilt, so a subclass that
        # overrides it (to return ``changed=False``, say) still governs.
        return replace(self._mutation(), mcp_codes=self.skill_mcp_codes)

    def install_mcp(self, **kwargs) -> DesiredStateMutation:
        self.install_mcp_calls.append(kwargs)
        return replace(
            self._mutation(), mcp_codes=frozenset({kwargs["server_code"]})
        )

    def uninstall_mcp(self, **kwargs) -> DesiredStateMutation:
        self.uninstall_mcp_calls.append(kwargs)
        return replace(
            self._mutation(), mcp_codes=frozenset({kwargs["server_code"]})
        )

    def install_skill(self, **kwargs) -> DesiredStateMutation:
        self.install_skill_calls.append(kwargs)
        return self._skill_mutation()

    def uninstall_skill(self, **kwargs) -> DesiredStateMutation:
        self.uninstall_skill_calls.append(kwargs)
        return self._skill_mutation()

    def restore_desired_state(self, **kwargs) -> None:
        self.restore_calls.append(kwargs)


class _Bots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        if owner_id != "true-owner":
            return None
        return {
            "bot_id": bot_id,
            "owner_id": "true-owner",
            "env": "dev",
            "entity_id": "entity-1",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "entity_type": "staff",
            "status": "ACTIVE",
        }


class _Skills:
    """One Local row and one shared Repo row."""

    _ROWS = {
        "7": {
            "id": 7,
            "name": "local-skill",
            "git_path": "local://local-skill",
            "user_id": "true-owner",
            "bolt_id": "bot-1",
        },
        "8": {
            "id": 8,
            "name": "repo-skill",
            "git_path": "git://team/repo-skill",
            "user_id": None,
            "bolt_id": "default",
        },
    }

    def get_by_id(self, skill_id: str) -> dict | None:
        return self._ROWS.get(skill_id)

    def get_bot_local_skill(self, *, skill_id: str, bot_id: str, user_id: str):
        row = self._ROWS.get(skill_id)
        if row and row["bolt_id"] == bot_id and row["user_id"] == user_id:
            return {**row, "active": False}
        return None


class _Authorization:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[dict] = []

    def can_manage_bot(self, *, bot_id, owner_id, actor_id) -> bool:
        self.calls.append(
            {"bot_id": bot_id, "owner_id": owner_id, "actor_id": actor_id}
        )
        return self.allowed


class _Audit:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def insert(self, data) -> None:
        self.actions.append(data["detail"])


class _SuccessfulRuntime:
    async def snapshot_skill_mappings(self, **_kwargs):
        return ()

    async def project(self, **_kwargs) -> None:
        return None

    def resolve_plan(self, *, bot_id: str, owner_id: str, **_kwargs):
        return SimpleNamespace(
            bot_id=bot_id,
            owner_id=owner_id,
            projection=SimpleNamespace(skill_mappings=()),
        )

    async def apply_plan(self, *, plan, **kwargs) -> None:
        await self.project(
            bot_id=plan.bot_id,
            owner_id=plan.owner_id,
            **kwargs,
        )


class _ScopeRecordingRuntime(_SuccessfulRuntime):
    """Captures the ``ProjectionScope`` each projection was handed."""

    def __init__(self) -> None:
        self.scopes: list[ProjectionScope] = []

    async def project(self, *, scope, **_kwargs) -> None:
        self.scopes.append(scope)


class _FailingRuntime(_SuccessfulRuntime):
    """The first projection fails; the compensating counter-projection holds."""

    def __init__(self) -> None:
        self.calls = 0

    async def project(self, **_kwargs) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("engine write failed")


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


class _Reader:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def active_mcp_server_codes(self, *, bot_id, owner_id, bot=None):
        self.calls.append({"bot_id": bot_id, "owner_id": owner_id, "bot": bot})
        return frozenset({"mcp.weather"})


class _PlatformDefaultMcpPolicy:
    def __init__(self, *codes: str) -> None:
        self.codes = frozenset(codes)
        self.bots: list[dict] = []

    def require_direct_control_allowed(
        self, *, bot: dict, server_code: str
    ) -> frozenset[str]:
        self.bots.append(bot)
        if server_code in self.codes:
            raise SkillSetControlPlaneConflictError(
                "RESOURCE_MANAGED_BY_PLATFORM_POLICY"
            )
        return self.codes


def _service(
    *,
    repository=None,
    runtime=None,
    authorization=None,
    mcp_center=None,
    reader=None,
    audit=None,
    platform_default_mcp_policy=None,
) -> DirectActivationService:
    return DirectActivationService(
        repository if repository is not None else _Repository(),
        _Bots(),
        _Skills(),
        runtime if runtime is not None else _SuccessfulRuntime(),
        authorization if authorization is not None else _Authorization(),
        audit if audit is not None else _Audit(),
        mcp_center if mcp_center is not None else _McpCenter(allowed=True),
        reader if reader is not None else _Reader(),
        platform_default_mcp_policy
        if platform_default_mcp_policy is not None
        else _PlatformDefaultMcpPolicy(),
    )


@pytest.mark.asyncio
async def test_mcp_direct_activation_checks_permission_before_writing():
    repository = _Repository()
    mcp_center = _McpCenter(allowed=True)
    service = _service(repository=repository, mcp_center=mcp_center)

    await service.activate_mcp(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner",
        server_code="mcp.weather",
    )

    assert mcp_center.calls == [("true-owner", "mcp.weather")]
    assert repository.install_mcp_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "server_code": "mcp.weather",
            "platform_default_codes": frozenset(),
            **_SCOPE,
        }
    ]


@pytest.mark.asyncio
async def test_mcp_direct_activation_denies_before_writing():
    repository = _Repository()
    service = _service(repository=repository, mcp_center=_McpCenter(allowed=False))

    with pytest.raises(McpPermissionDeniedError):
        await service.activate_mcp(
            bot_id="bot-1", owner_id="true-owner", actor_id="true-owner",
            server_code="mcp.weather",
        )
    assert repository.install_mcp_calls == []


@pytest.mark.asyncio
async def test_mcp_direct_deactivation_needs_no_marketplace_permission():
    repository = _Repository()
    mcp_center = _McpCenter(allowed=False)
    service = _service(repository=repository, mcp_center=mcp_center)

    await service.deactivate_mcp(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner",
        server_code="mcp.weather",
    )

    assert mcp_center.calls == []
    assert repository.uninstall_mcp_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "server_code": "mcp.weather",
            "platform_default_codes": frozenset(),
            **_SCOPE,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["activate_mcp", "deactivate_mcp"])
async def test_platform_default_mcp_refuses_direct_control(method_name: str):
    repository = _Repository()
    mcp_center = _McpCenter(allowed=True)
    policy = _PlatformDefaultMcpPolicy("mcp.policy")
    service = _service(
        repository=repository,
        mcp_center=mcp_center,
        platform_default_mcp_policy=policy,
    )

    with pytest.raises(
        SkillSetControlPlaneConflictError,
        match="RESOURCE_MANAGED_BY_PLATFORM_POLICY",
    ):
        await getattr(service, method_name)(
            bot_id="bot-1",
            owner_id="true-owner",
            actor_id="true-owner",
            server_code="mcp.policy",
        )

    assert policy.bots[0]["bot_id"] == "bot-1"
    assert repository.install_mcp_calls == []
    assert repository.uninstall_mcp_calls == []
    assert mcp_center.calls == []


@pytest.mark.asyncio
async def test_mcp_commands_refuse_a_non_collaborating_actor():
    repository = _Repository()
    service = _service(repository=repository, authorization=_Authorization(False))

    with pytest.raises(SkillSetAccessDeniedError):
        await service.activate_mcp(
            bot_id="bot-1", owner_id="true-owner", actor_id="stranger",
            server_code="mcp.weather",
        )
    assert repository.install_mcp_calls == []


def test_list_installed_mcps_answers_through_the_reader():
    reader = _Reader()
    service = _service(reader=reader)

    codes = service.list_installed_mcps(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )

    assert codes == {"mcp.weather"}
    assert reader.calls[0]["bot_id"] == "bot-1"
    assert reader.calls[0]["owner_id"] == "true-owner"
    # The resolved Bot row rides along so the reader's flush does not re-read.
    assert reader.calls[0]["bot"]["owner_id"] == "true-owner"


@pytest.mark.asyncio
async def test_skill_direct_activation_writes_and_reports_the_asset():
    repository = _Repository()
    audit = _Audit()
    service = _service(repository=repository, audit=audit)

    result = await service.activate_skill(
        skill_id="7", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner",
    )

    assert repository.install_skill_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "skill_id": "7",
            **_SCOPE,
        }
    ]
    assert result["active"] is True
    assert result["changed"] is True
    assert result["name"] == "local-skill"
    assert audit.actions == ['{"action":"skill_direct_activate"}']


@pytest.mark.asyncio
async def test_repo_skill_takes_the_addressed_bot_and_owner():
    repository = _Repository()
    service = _service(repository=repository)

    result = await service.deactivate_skill(
        skill_id="8", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner",
    )

    assert repository.uninstall_skill_calls[0]["skill_id"] == "8"
    assert result["bolt_id"] == "bot-1"
    assert result["user_id"] == "true-owner"
    assert result["active"] is False


@pytest.mark.asyncio
async def test_skill_wire_masks_authorization_failure_as_not_found():
    service = _service(authorization=_Authorization(False))

    with pytest.raises(LocalSkillNotFoundError):
        await service.activate_skill(
            skill_id="7", bot_id="bot-1", owner_id="true-owner",
            actor_id="stranger",
        )


@pytest.mark.asyncio
async def test_skill_runtime_failure_keeps_committed_desired_state_and_reports_pending():
    repository = _Repository()
    service = _service(repository=repository, runtime=_FailingRuntime())

    result = await service.activate_skill(
        skill_id="7", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner",
    )

    assert repository.restore_calls == []
    assert result["active"] is True
    assert result["runtime_projection"]["status"] == "PENDING"
    assert result["runtime_projection"]["issues"][0]["code"] == "RUNTIME_PROJECTION_UNAVAILABLE"


@pytest.mark.asyncio
async def test_a_not_ready_bot_commits_desired_state_and_returns_pending():
    class _PendingBots(_Bots):
        def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
            bot = super().get_by_id_and_owner(bot_id, owner_id)
            return {**bot, "status": "PENDING"} if bot else None

    repository = _Repository()
    service = DirectActivationService(
        repository, _PendingBots(), _Skills(), _SuccessfulRuntime(),
        _Authorization(), _Audit(), _McpCenter(allowed=True), _Reader(),
        _PlatformDefaultMcpPolicy(),
    )

    result = await service.activate_skill(
        skill_id="7", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner",
    )

    assert len(repository.install_skill_calls) == 1
    assert result["runtime_projection"]["status"] == "PENDING"
    assert result["runtime_projection"]["issues"][0]["code"] == "BOT_RUNTIME_NOT_READY"


@pytest.mark.asyncio
async def test_a_space_asset_and_a_mismatched_local_row_are_masked_as_not_found():
    class _MoreSkills(_Skills):
        _ROWS = {
            **_Skills._ROWS,
            "9": {
                "id": 9,
                "name": "space-skill",
                "git_path": "center://space-skill",
                "user_id": None,
                "bolt_id": None,
            },
        }

    repository = _Repository()
    service = DirectActivationService(
        repository, _Bots(), _MoreSkills(), _SuccessfulRuntime(),
        _Authorization(), _Audit(), _McpCenter(allowed=True), _Reader(),
        _PlatformDefaultMcpPolicy(),
    )

    # A Space (center://) asset has no direct-activation wire.
    with pytest.raises(LocalSkillNotFoundError):
        await service.activate_skill(
            skill_id="9", bot_id="bot-1", owner_id="true-owner",
            actor_id="true-owner",
        )
    # A Local row carries its own Bot: addressing it through another Bot
    # must not resolve.
    with pytest.raises(LocalSkillNotFoundError):
        await service.activate_skill(
            skill_id="7", bot_id="another-bot", owner_id="true-owner",
            actor_id="true-owner",
        )
    assert repository.install_skill_calls == []


@pytest.mark.asyncio
async def test_an_idempotent_deactivate_still_projects_the_runtime():
    """`changed=False` is not "skip the engine": the projection converges the
    runtime with desired state even when the row was already absent."""

    class _UnchangedRepository(_Repository):
        @staticmethod
        def _mutation() -> DesiredStateMutation:
            return DesiredStateMutation(
                {}, False, CapabilityDesiredState(set(), {}, {})
            )

    class _CountingRuntime(_SuccessfulRuntime):
        def __init__(self) -> None:
            self.projections = 0

        async def project(self, **_kwargs) -> None:
            self.projections += 1

    runtime = _CountingRuntime()
    service = _service(repository=_UnchangedRepository(), runtime=runtime)

    result = await service.deactivate_skill(
        skill_id="7", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner",
    )

    assert result["changed"] is False
    assert result["active"] is False
    assert runtime.projections == 1


# ── Direct activation declares the half it actually changed ──────────
#
# These commands are Set-free, but they move the same runtime facts a Set
# mutation does, so projecting both halves for each of them re-sent a whole
# snapshot nothing had touched.


@pytest.mark.asyncio
async def test_activating_one_mcp_claims_only_that_code():
    runtime = _ScopeRecordingRuntime()
    service = _service(runtime=runtime)

    await service.activate_mcp(
        server_code="mcp.weather", bot_id="bot-1",
        owner_id="true-owner", actor_id="true-owner",
    )

    (scope,) = runtime.scopes
    assert scope.mcp is True
    assert scope.skills is False, "activating an MCP touches no Skill"
    assert scope.claimed_mcp == frozenset({"mcp.weather"})
    assert scope.released_mcp == frozenset()


@pytest.mark.asyncio
async def test_deactivating_one_mcp_releases_only_that_code():
    runtime = _ScopeRecordingRuntime()
    service = _service(runtime=runtime)

    await service.deactivate_mcp(
        server_code="mcp.weather", bot_id="bot-1",
        owner_id="true-owner", actor_id="true-owner",
    )

    (scope,) = runtime.scopes
    assert scope.mcp is True
    assert scope.skills is False
    assert scope.released_mcp == frozenset({"mcp.weather"})
    assert scope.claimed_mcp == frozenset()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["activate_mcp", "deactivate_mcp"])
async def test_an_unchanged_direct_mcp_command_declares_no_runtime_delta(
    method_name: str,
) -> None:
    class _UnchangedMcpRepository(_Repository):
        def install_mcp(self, **kwargs) -> DesiredStateMutation:
            self.install_mcp_calls.append(kwargs)
            return DesiredStateMutation(
                {}, False, CapabilityDesiredState(set(), {}, {})
            )

        def uninstall_mcp(self, **kwargs) -> DesiredStateMutation:
            self.uninstall_mcp_calls.append(kwargs)
            return DesiredStateMutation(
                {}, False, CapabilityDesiredState(set(), {}, {})
            )

    runtime = _ScopeRecordingRuntime()
    service = _service(repository=_UnchangedMcpRepository(), runtime=runtime)

    result = await getattr(service, method_name)(
        server_code="mcp.weather",
        bot_id="bot-1",
        owner_id="true-owner",
        actor_id="true-owner",
    )

    assert result["changed"] is False
    assert runtime.scopes == [ProjectionScope()]


@pytest.mark.asyncio
async def test_activating_a_dependency_free_skill_leaves_the_mcp_half_alone():
    runtime = _ScopeRecordingRuntime()
    service = _service(runtime=runtime)

    await service.activate_skill(
        skill_id="8", bot_id="bot-1", owner_id="true-owner", actor_id="true-owner",
    )

    (scope,) = runtime.scopes
    assert scope.skills is True
    assert scope.mcp is False, "no dependencies, so no MCP projection"


@pytest.mark.asyncio
async def test_activating_a_skill_claims_the_mcps_it_depends_on():
    """The reason this cannot simply be ``ProjectionScope(skills=True)``.

    A Skill's ``mcp_dependencies`` join the Bot's projected MCP set along with
    it, so declaring ``mcp=False`` regardless would whitelist a dependency the
    device is never configured for.
    """
    repository = _Repository()
    repository.skill_mcp_codes = frozenset({"mcp.weather"})
    runtime = _ScopeRecordingRuntime()
    service = _service(repository=repository, runtime=runtime)

    await service.activate_skill(
        skill_id="8", bot_id="bot-1", owner_id="true-owner", actor_id="true-owner",
    )

    (scope,) = runtime.scopes
    assert scope.skills is True
    assert scope.mcp is True
    assert scope.claimed_mcp == frozenset({"mcp.weather"})


@pytest.mark.asyncio
async def test_deactivating_a_skill_releases_the_mcps_it_depended_on():
    repository = _Repository()
    repository.skill_mcp_codes = frozenset({"mcp.weather"})
    runtime = _ScopeRecordingRuntime()
    service = _service(repository=repository, runtime=runtime)

    await service.deactivate_skill(
        skill_id="8", bot_id="bot-1", owner_id="true-owner", actor_id="true-owner",
    )

    (scope,) = runtime.scopes
    assert scope.skills is True
    assert scope.released_mcp == frozenset({"mcp.weather"})
    assert scope.claimed_mcp == frozenset()


# ── record-only activation (W8) ─────────────────────────────────────────────


class _NeverProjects(_SuccessfulRuntime):
    """A runtime that must not be reached: record-only skips projection."""

    async def snapshot_skill_mappings(self, **_kwargs):
        raise AssertionError("record-only activation must not snapshot the runtime")

    async def project(self, **_kwargs) -> None:
        raise AssertionError("record-only activation must not project")

    def resolve_plan(self, **_kwargs):
        raise AssertionError("record-only activation must not resolve a plan")


class _PendingBotsForRecordOnly(_Bots):
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        bot = super().get_by_id_and_owner(bot_id, owner_id)
        return {**bot, "status": "PENDING"} if bot else None


@pytest.mark.asyncio
async def test_record_only_mcp_activation_writes_desired_state_on_a_pending_bot():
    repository = _Repository()
    audit = _Audit()
    service = DirectActivationService(
        repository, _PendingBotsForRecordOnly(), _Skills(), _NeverProjects(),
        _Authorization(), audit, _McpCenter(allowed=True), _Reader(),
        _PlatformDefaultMcpPolicy(),
    )
    result = await service.activate_mcp(
        server_code="github", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner", project=False,
    )
    assert result["changed"] is True
    assert [c["server_code"] for c in repository.install_mcp_calls] == ["github"]
    assert audit.actions  # the audit row is still written
    await service.deactivate_mcp(
        server_code="github", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner", project=False,
    )
    assert [c["server_code"] for c in repository.uninstall_mcp_calls] == ["github"]


@pytest.mark.asyncio
async def test_record_only_skill_activation_writes_desired_state_on_a_pending_bot():
    repository = _Repository()
    service = DirectActivationService(
        repository, _PendingBotsForRecordOnly(), _Skills(), _NeverProjects(),
        _Authorization(), _Audit(), _McpCenter(allowed=True), _Reader(),
        _PlatformDefaultMcpPolicy(),
    )
    await service.activate_skill(
        skill_id="7", bot_id="bot-1", owner_id="true-owner", actor_id="true-owner",
        project=False,
    )
    assert len(repository.install_skill_calls) == 1
    await service.deactivate_skill(
        skill_id="7", bot_id="bot-1", owner_id="true-owner", actor_id="true-owner",
        project=False,
    )
    assert len(repository.uninstall_skill_calls) == 1


@pytest.mark.asyncio
async def test_record_only_skips_the_runtime_where_the_default_reports_it_pending():
    """On a bot that is not ready the default commits desired state and
    reports the projection *pending* (the runtime is not consulted either
    way); record-only says the runtime was not required at all, which is
    what the teclaw strategy means: the artifact is the projection."""
    repository = _Repository()
    service = DirectActivationService(
        repository, _PendingBotsForRecordOnly(), _Skills(), _NeverProjects(),
        _Authorization(), _Audit(), _McpCenter(allowed=True), _Reader(),
        _PlatformDefaultMcpPolicy(),
    )
    by_default = await service.activate_mcp(
        server_code="github", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner",
    )
    assert by_default["runtime_projection"]["status"] == "PENDING"
    assert by_default["runtime_projection"]["issues"][0]["code"] == "BOT_RUNTIME_NOT_READY"
    record_only = await service.activate_mcp(
        server_code="github", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner", project=False,
    )
    assert record_only["runtime_projection"]["status"] == "SKIPPED"
    assert record_only["runtime_projection"]["reason"] == "RUNTIME_NOT_REQUIRED"
    assert [c["server_code"] for c in repository.install_mcp_calls] == ["github", "github"]

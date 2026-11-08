from __future__ import annotations
import pytest
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.skill_set_batch import (
    SkillSetSkillOutcome,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)
from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
)

from tests.community.core.skill_center._test_skill_set_management_service_support import (
    _Repository as _Repository,
    _CreateRepository as _CreateRepository,
    _LegacyScopeRepository as _LegacyScopeRepository,
    _InactiveMembershipRepository as _InactiveMembershipRepository,
    _ActiveMembershipRepository as _ActiveMembershipRepository,
    _Bots as _Bots,
    _MissingBots as _MissingBots,
    _SharedDefaultBots as _SharedDefaultBots,
    _UnsupportedBots as _UnsupportedBots,
    _AicodingImageBots as _AicodingImageBots,
    _PlainClaudeCodeBots as _PlainClaudeCodeBots,
    _LiteralAicodingBots as _LiteralAicodingBots,
    _NotReadyApplicationCodingBots as _NotReadyApplicationCodingBots,
    _Runtime as _Runtime,
    _Authorization as _Authorization,
    _Audit as _Audit,
    _SuccessfulRuntime as _SuccessfulRuntime,
    _LegacyResolutionRepository as _LegacyResolutionRepository,
    _LegacySkillSetService as _LegacySkillSetService,
    _LegacyFactory as _LegacyFactory,
    _McpRepository as _McpRepository,
    _ResourceRepository as _ResourceRepository,
    _DefaultResourceRepository as _DefaultResourceRepository,
    _MixedResourceRepository as _MixedResourceRepository,
    _ResourceLegacyFactory as _ResourceLegacyFactory,
    _McpAuth as _McpAuth,
    _McpConfig as _McpConfig,
    _OwnerSensitiveMcpConfig as _OwnerSensitiveMcpConfig,
    _McpCenter as _McpCenter,
    _registry as _registry,
    _RuntimeFactoryService as _RuntimeFactoryService,
    _RuntimeFactory as _RuntimeFactory,
    _OverlappingCollectService as _OverlappingCollectService,
    _OverlappingCollectFactory as _OverlappingCollectFactory,
    _FailingPolicyCollectService as _FailingPolicyCollectService,
    _FailingPolicyCollectFactory as _FailingPolicyCollectFactory,
    _RuntimeBots as _RuntimeBots,
    _AicodingImageRuntimeBots as _AicodingImageRuntimeBots,
    _PlainClaudeCodeRuntimeBots as _PlainClaudeCodeRuntimeBots,
    _HistoricalAicodingRuntimeBots as _HistoricalAicodingRuntimeBots,
    _TeclawRuntimeBots as _TeclawRuntimeBots,
    _McpInstallations as _McpInstallations,
    _FailingFlushRepository as _FailingFlushRepository,
    _RuntimeSkills as _RuntimeSkills,
    _RuntimePool as _RuntimePool,
    _FailingPublishRuntimePool as _FailingPublishRuntimePool,
    _CenterRuntimePool as _CenterRuntimePool,
    _CenterMountMissingRuntimePool as _CenterMountMissingRuntimePool,
    _CenterRuntimeSkills as _CenterRuntimeSkills,
    _TeclawRuntimeSkills as _TeclawRuntimeSkills,
    _reader as _reader,
    _RuntimeLayouts as _RuntimeLayouts,
    _RuntimePassport as _RuntimePassport,
    _ResourcePassport as _ResourcePassport,
    _RuntimeCallerIdentity as _RuntimeCallerIdentity,
    _FailingRuntimePassport as _FailingRuntimePassport,
    _ungated_control_plane_routes as _ungated_control_plane_routes,
    _passport_mcp_items as _passport_mcp_items,
    _PkLessRuntimeBots as _PkLessRuntimeBots,
    _teclaw_runtime as _teclaw_runtime,
    _SkillRepository as _SkillRepository,
    _skill_service as _skill_service,
    _scoped_projector as _scoped_projector,
    _DefaultTargetRepository as _DefaultTargetRepository,
    _ProjectionCountingRuntime as _ProjectionCountingRuntime,
    _default_wire_service as _default_wire_service,
)

@pytest.mark.asyncio
async def test_add_skill_claims_the_skill_s_mcp_dependencies():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _Runtime(fail_first=False)

    await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7"],
    )

    (call,) = runtime.reconcile_calls
    assert call["scope"].skills is True
    assert call["scope"].mcp is True
    assert call["scope"].claimed_mcp == frozenset({"mcp.weather"})
    assert call["scope"].released_mcp == frozenset()


@pytest.mark.asyncio
async def test_add_skill_without_dependencies_leaves_the_mcp_half_alone():
    """The whole point of the lookup: a dependency-free Skill is Skills-only.

    Without it every Skill change re-declared the MCP allow-list and re-pushed
    the Passport manifest to say nothing had changed.
    """
    repository = _SkillRepository()
    runtime = _Runtime(fail_first=False)

    await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7"],
    )

    (call,) = runtime.reconcile_calls
    assert call["scope"].skills is True
    assert call["scope"].mcp is False


@pytest.mark.asyncio
async def test_batch_add_to_an_active_set_projects_the_final_state_once():
    """A legacy batch must not publish one runtime artifact per member."""
    repository = _SkillRepository({"mcp.weather"})
    runtime = _ProjectionCountingRuntime()

    outcomes = await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7", "8"],
    )

    assert outcomes == [
        SkillSetSkillOutcome(skill_id="7", changed=True),
        SkillSetSkillOutcome(skill_id="8", changed=True),
    ]
    assert [call["skill_id"] for call in repository.skill_calls] == ["7", "8"]
    assert runtime.projections == 1
    assert runtime.scopes == [
        ProjectionScope(
            skills=True,
            mcp=True,
            claimed_mcp=frozenset({"mcp.weather"}),
        )
    ]


@pytest.mark.asyncio
async def test_batch_add_keeps_legacy_partial_success_and_projects_once():
    class _PartialRepository(_SkillRepository):
        def add_skill(self, **kwargs) -> DesiredStateMutation:
            if kwargs["skill_id"] == "missing":
                raise SkillSetControlPlaneNotFoundError()
            return super().add_skill(**kwargs)

    repository = _PartialRepository()
    runtime = _ProjectionCountingRuntime()

    outcomes = await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7", "missing"],
    )

    assert outcomes[0] == SkillSetSkillOutcome(skill_id="7", changed=True)
    assert outcomes[1].skill_id == "missing"
    assert isinstance(outcomes[1].error, SkillSetControlPlaneNotFoundError)
    assert runtime.projections == 1


@pytest.mark.asyncio
async def test_batch_add_runtime_failure_keeps_the_whole_committed_batch_once():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _Runtime()

    outcomes = await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7", "8"],
    )

    assert [outcome.changed for outcome in outcomes] == [True, True]
    assert [call["skill_id"] for call in repository.skill_calls] == ["7", "8"]
    assert repository.restore_calls == []
    assert len(runtime.reconcile_calls) == 1


@pytest.mark.asyncio
async def test_batch_add_restores_prior_members_when_a_later_write_fails():
    class _FailingSecondWriteRepository(_SkillRepository):
        def add_skill(self, **kwargs) -> DesiredStateMutation:
            if kwargs["skill_id"] == "8":
                raise RuntimeError("database unavailable")
            return super().add_skill(**kwargs)

    repository = _FailingSecondWriteRepository()
    runtime = _ProjectionCountingRuntime()

    with pytest.raises(RuntimeError, match="database unavailable"):
        await _skill_service(repository, runtime).add_skills(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="set-1",
            skill_ids=["7", "8"],
        )

    assert len(repository.restore_calls) == 1
    assert runtime.projections == 0


@pytest.mark.asyncio
async def test_batch_remove_from_an_active_set_projects_the_final_state_once():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _ProjectionCountingRuntime()

    outcomes = await _skill_service(repository, runtime).remove_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7", "8"],
    )

    assert outcomes == [
        SkillSetSkillOutcome(skill_id="7", changed=True),
        SkillSetSkillOutcome(skill_id="8", changed=True),
    ]
    assert runtime.projections == 1
    assert runtime.scopes == [
        ProjectionScope(
            skills=True,
            mcp=True,
            released_mcp=frozenset({"mcp.weather"}),
        )
    ]


@pytest.mark.asyncio
async def test_batch_remove_runtime_failure_keeps_the_whole_committed_batch_once():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _Runtime()

    outcomes = await _skill_service(repository, runtime).remove_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7", "8"],
    )

    assert [outcome.changed for outcome in outcomes] == [True, True]
    assert repository.restore_calls == []
    (forward,) = runtime.reconcile_calls
    assert forward["scope"].released_mcp == frozenset({"mcp.weather"})


@pytest.mark.asyncio
async def test_remove_skill_releases_the_skill_s_mcp_dependencies():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _Runtime(fail_first=False)

    await _skill_service(repository, runtime).remove_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7"],
    )

    (call,) = runtime.reconcile_calls
    assert call["scope"].released_mcp == frozenset({"mcp.weather"})
    assert call["scope"].claimed_mcp == frozenset()


@pytest.mark.asyncio
async def test_a_failed_skill_projection_keeps_the_dependencies_it_claimed():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _Runtime()

    outcomes = await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7"],
    )

    assert outcomes == [SkillSetSkillOutcome(skill_id="7", changed=True)]
    (forward,) = runtime.reconcile_calls
    assert forward["scope"].claimed_mcp == frozenset({"mcp.weather"})


@pytest.mark.asyncio
async def test_an_inactive_set_still_skips_projection_entirely():
    """Declaring a scope must not resurrect projection for an inactive Set."""

    class _InactiveSetRepository(_SkillRepository):
        def get_set(self, **_kwargs):
            return {"id": "set-1", "is_default": False, "is_active": False}

    repository = _InactiveSetRepository({"mcp.weather"})
    runtime = _Runtime(fail_first=False)

    await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7"],
    )

    assert runtime.reconcile_calls == []


@pytest.mark.asyncio
async def test_an_mcp_only_scope_does_not_touch_the_skill_runtime():
    """``add_mcp`` / ``remove_mcp`` declare ``mcp=True`` and nothing else.

    Republishing the Skill snapshot for them would restate mappings no
    mutation touched — the fan-out this change exists to stop, in its other
    half.
    """
    pool, factory = _RuntimePool(), _RuntimeFactory()
    runtime = _scoped_projector(pool=pool, factory=factory)

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.weather"})),
    )

    assert factory.service.desired_skills is None
    assert pool.publish_calls == []
    assert pool.verify_calls == []
    # ...while the half it did declare still ran in full.
    assert factory.service.mcp_codes is not None


@pytest.mark.asyncio
async def test_an_mcp_config_update_redelivers_only_the_updated_server():
    factory = _RuntimeFactory()
    runtime = _scoped_projector(factory=factory)

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(
            mcp=True,
            updated_mcp=frozenset({"mcp.weather"}),
        ),
    )

    assert factory.service.deliveries == [(frozenset({"mcp.weather"}), frozenset())]


@pytest.mark.asyncio
async def test_a_skill_only_scope_touches_neither_the_device_mcps_nor_passport():
    """The MCP allow-list and the Passport manifest are both overwrite-style.

    A mutation that changed no MCP has nothing new to say to either, and
    saying it anyway is a device write plus an authorization-service call per
    mutation.
    """
    passport, factory = _RuntimePassport(), _RuntimeFactory()
    repository, identity = _McpInstallations(), _RuntimeCallerIdentity()
    runtime = _scoped_projector(
        passport=passport,
        factory=factory,
        repository=repository,
        identity=identity,
    )

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=ProjectionScope(skills=True)
    )

    assert factory.service.desired_skills is not None
    assert factory.service.mcp_codes is None
    assert factory.service.deliveries == []
    assert passport.calls == []
    # No MCP mutation means no MCP/CLI read-side pre-flight either.  Those
    # facts would only be consumed by a device-MCP write or Passport update,
    # both of which this scope explicitly omits.
    assert factory.service.collect_calls == []
    assert repository.list_installed_calls == []
    assert passport.query_calls == []
    assert identity.calls == []


@pytest.mark.asyncio
async def test_projector_exposes_skill_and_complete_plan_shapes_at_the_engine_seam():
    """The engine seam receives an honest type, never an incomplete full plan."""
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        EngineRuntimeProjection,
        ResolvedCapabilityPlan,
        ResolvedSkillPlan,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.registry import (
        EngineRuntimeProjectionRegistry,
    )

    plans = []

    class _RecordingProjection(EngineRuntimeProjection):
        def validate_plan(self, *, skill_assets, retired_mappings=()) -> None:
            return None

        async def apply(
            self, *, plan, scope, retired_mappings=(), service_factory
        ) -> None:
            plans.append(plan)

    projection = _RecordingProjection()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=EngineRuntimeProjectionRegistry(default=projection),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(skills=True),
    )
    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True),
    )

    assert type(plans[0]) is ResolvedSkillPlan
    assert type(plans[1]) is ResolvedCapabilityPlan


@pytest.mark.asyncio
async def test_retired_mappings_project_skills_even_when_the_scope_omits_them():
    """Retirements are evidence, not a declaration.

    They are computed from the actual before/after snapshots, so they outrank
    a scope that says Skills did not change — skipping them would strand a
    published mapping the desired state no longer holds.
    """
    pool, factory = _RuntimePool(), _RuntimeFactory()
    runtime = _scoped_projector(pool=pool, factory=factory)

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        retired_mappings=[
            PoolSkillMapping(corpus="repo", relative_path="gone", link_name="gone")
        ],
        scope=ProjectionScope(mcp=True),
    )

    assert pool.publish_calls, "a retirement must still reach the runtime"


def test_claiming_every_code_is_independent_of_which_halves_run():
    """``claim_all_mcp`` answers "which MCPs are claimed", not "which halves".

    An MCP-only reconcile is a real shape — it is what the cutover listener
    passes to ``project_mcp_and_cli``, where declaring the Skill half would
    fight the task that owns the mappings.
    """
    mcp_only = ProjectionScope(mcp=True, claim_all_mcp=True)

    assert mcp_only.skills is False
    assert mcp_only.claim_all_mcp is True
    assert ProjectionScope.everything().claim_all_mcp is True


@pytest.mark.asyncio
async def test_a_reconcile_scope_still_projects_both_halves():
    """The undeclared default is unchanged: everything, as before."""
    passport, factory = _RuntimePassport(), _RuntimeFactory()
    runtime = _scoped_projector(passport=passport, factory=factory)

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.service.desired_skills is not None
    assert factory.service.mcp_codes is not None
    assert passport.calls


@pytest.mark.asyncio
async def test_the_projector_makes_one_mcp_call_not_two():
    """Delivery and declaration reach the device through a single entry point.

    How many device writes an MCP projection takes, and in what order, belongs
    to the service that owns device resolution — not to the projector.
    """
    calls: list[dict] = []

    class _RecordingService(_RuntimeFactoryService):
        async def sync_mcp_delivery(self, **kwargs):
            raise AssertionError("the projector must not call delivery directly")

        async def sync_mcp_desired_state(self, **kwargs):
            raise AssertionError("the projector must not call declaration directly")

        async def project_mcps(self, **kwargs) -> bool:
            calls.append(kwargs)
            return True

    class _RecordingFactory(_RuntimeFactory):
        def __init__(self) -> None:
            super().__init__()
            self.service = _RecordingService()

    runtime = _scoped_projector(factory=_RecordingFactory())

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.weather"})),
    )

    assert len(calls) == 1
    assert set(calls[0]) == {"claimed", "released", "declared"}


@pytest.mark.asyncio
async def test_removing_a_default_member_performs_the_exclusion_and_reconciles():
    repository = _DefaultTargetRepository()
    runtime = _ProjectionCountingRuntime()
    service = _default_wire_service(repository, runtime)

    (result,) = await service.remove_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="9",
        skill_ids=["7"],
    )

    assert result.changed is True
    assert [name for name, _ in repository.exclusion_calls] == ["exclude_default_skill"]
    assert repository.exclusion_calls[0][1]["skill_id"] == "7"
    assert runtime.projections == 1


@pytest.mark.asyncio
async def test_adding_back_an_excluded_default_member_unexcludes():
    repository = _DefaultTargetRepository(excluded_ids={7})
    service = _default_wire_service(repository)

    (result,) = await service.add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="9",
        skill_ids=["7"],
    )

    assert result.changed is True
    assert [name for name, _ in repository.exclusion_calls] == [
        "unexclude_default_skill"
    ]


@pytest.mark.asyncio
async def test_default_mcp_exclusion_passes_the_platform_default_policy():
    """The service resolves the unmaterialized half and hands it to the UoW.

    The exclusion command can read association rows itself, but the
    engine/template default codes are read-time policy (spec A.2) — the
    service resolves them with the same context the read-side union uses,
    ext info included, so the command's stray-code gate cannot refuse a
    genuine platform default.
    """
    from agentclaw.community.core.mcp.services._defaults import (
        get_default_mcp_server_codes,
    )

    repository = _DefaultTargetRepository()
    ext_calls: list[str] = []

    def _ext(bot_id: str):
        ext_calls.append(bot_id)
        return None

    service = _default_wire_service(repository, ext_info_provider=_ext)

    await service.remove_mcp(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="9",
        server_code="mcp.gone",
    )

    assert ext_calls == ["bot-1"]
    name, kwargs = repository.exclusion_calls[0]
    assert name == "exclude_default_mcp"
    assert kwargs["platform_default_codes"] == frozenset(
        get_default_mcp_server_codes("openclaw", None, ext_info=None)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_ordinary_set_passes_platform_default_codes_to_transaction(active):
    class Repository(_Repository):
        def get_set(self, **kwargs):
            return {"id": "set-1", "is_default": False, "is_active": active}

    repository = Repository()
    runtime = _ProjectionCountingRuntime()
    service = _default_wire_service(repository, runtime)
    await service.add_mcp(
        bot_id="bot-1", owner_id="true-owner", user_id="true-owner",
        set_id="set-1", server_code="mcp.ant.arkai.dimamcpserver",
    )
    assert len(repository.add_mcp_calls) == 1
    assert "mcp.ant.arkai.dimamcpserver" in repository.add_mcp_calls[0]["platform_default_codes"]
    assert runtime.projections == int(active)


@pytest.mark.asyncio
async def test_ordinary_set_policy_lookup_failure_does_not_write():
    def unavailable(_bot_id):
        raise RuntimeError("policy unavailable")

    repository = _Repository()
    service = _default_wire_service(repository, ext_info_provider=unavailable)
    with pytest.raises(RuntimeError, match="policy unavailable"):
        await service.add_mcp(
            bot_id="bot-1", owner_id="true-owner", user_id="true-owner",
            set_id="set-1", server_code="mcp.new",
        )
    assert repository.add_mcp_calls == []


@pytest.mark.asyncio
async def test_default_mcp_exclusion_propagates_a_template_context_failure():
    """A failed ext lookup is an error, never a silently narrower gate.

    Template presets can be the only place a default MCP exists (the
    AICoding resolver merges them onto the engine defaults), so degrading
    to base defaults on a transient template-service failure would make the
    exclusion command mis-read that member as a stray and report the
    removal as an unchanged no-op — persisting the wrong answer instead of
    surfacing a retryable failure.
    """
    repository = _DefaultTargetRepository()

    def _broken(_bot_id: str):
        raise RuntimeError("template service unavailable")

    service = _default_wire_service(repository, ext_info_provider=_broken)

    with pytest.raises(RuntimeError, match="template service unavailable"):
        await service.remove_mcp(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="9",
            server_code="mcp.gone",
        )
    assert repository.exclusion_calls == []


@pytest.mark.asyncio
async def test_adding_a_new_member_to_the_default_stays_immutable():
    repository = _DefaultTargetRepository()
    service = _default_wire_service(repository)

    with pytest.raises(
        SkillSetControlPlaneConflictError, match="SYSTEM_DEFAULT_IMMUTABLE"
    ):
        await service.add_skills(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="9",
            skill_ids=["7"],
        )
    assert repository.exclusion_calls == []

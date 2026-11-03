from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)
from agentclaw.community.core.skills_pool.models import (
    RegisteredSkillAsset,
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
async def test_teclaw_mcp_only_scope_still_delivers_the_skill_bearing_artifact():
    """An MCP-only scope still delivers the Skills, because it must.

    Pins the behaviour most likely to be optimised back out: on a per-domain
    engine ``skills=False`` means "leave the Skill half alone", but here there
    is no Skill half to leave alone — the one document carries both.
    """
    factory = _RuntimeFactory()
    runtime = _teclaw_runtime(factory)

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.a"})),
    )

    assert len(factory.service.runtime_syncs) == 1
    assert factory.service.runtime_syncs[0] == [
        {
            "id": "8",
            "name": "repo-skill",
            "git_path": "git://team/repo-skill",
            "skill_uuid": None,
            "sc_version_number": None,
        }
    ]


@pytest.mark.asyncio
async def test_teclaw_delivery_carries_the_mcp_set_plan_resolution_read():
    """One projection reads the effective MCP set once, then hands it over.

    Plan resolution has to collect it — the projected codes and the Passport
    scope are derived from it — and the whole-artifact delivery recomposes the
    bot's document from the same database moments later. Without the handover
    that compose repeats the identical ``collect_bot_active_mcps`` query for an
    answer the projection is already holding; both sides ask with
    ``strict_policy_context=True``, so it is the same answer by contract.

    Asserted on the collect count and the delivered value rather than on
    timing: what makes the second read unnecessary is that the first one's
    result reaches the composer, and that is what would silently regress.
    """
    factory = _RuntimeFactory()
    runtime = _teclaw_runtime(factory)

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=ProjectionScope.everything(),
    )

    assert len(factory.service.collect_calls) == 1
    assert factory.service.delivered_effective_mcps == [
        [
            {"server_code": "mcp.template-preset"},
            {"server_code": "hitl", "source": "local"},
        ]
    ]


@pytest.mark.asyncio
async def test_a_per_domain_delivery_carries_no_mcp_set():
    """The handover is for engines that compose; a per-domain one does not.

    Its Skill call writes symlinks and its MCP half goes to a separate
    endpoint, so there is no compose behind ``project_skills`` to spare a read
    — passing the set would only imply a re-use that never happens.
    """
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(
            pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()
        ),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=ProjectionScope.everything(),
    )

    assert factory.service.delivered_effective_mcps == [None]


@pytest.mark.asyncio
async def test_teclaw_still_updates_the_passport_with_identity_coloured_items():
    """The Passport is the platform's record, not the runtime's — it still runs.

    A whole-artifact container is issued a passport-service token as an egress
    rule, so the manifest must keep pace with the configuration the artifact
    delivered. And it must still carry ``identity_mode``: sending codes alone
    asserts Owner for every MCP.
    """
    passport = _RuntimePassport()
    runtime = _teclaw_runtime(_RuntimeFactory(), passport=passport)

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.a"})),
    )

    assert len(passport.calls) == 1
    scope_sent = passport.calls[0]["resource_scope"]
    assert "mcp_codes" in scope_sent
    assert "cli_items" in scope_sent
    assert all("identity_mode" in item for item in scope_sent["mcp_items"])


@pytest.mark.asyncio
async def test_teclaw_skill_only_scope_makes_no_passport_call():
    """No MCP change declared, no manifest write — unchanged from today."""
    passport = _RuntimePassport()
    factory = _RuntimeFactory()
    repository, identity = _McpInstallations(), _RuntimeCallerIdentity()
    runtime = _teclaw_runtime(
        factory,
        passport=passport,
        repository=repository,
        identity=identity,
    )

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner",
        scope=ProjectionScope(skills=True),
    )

    assert passport.calls == []
    # Teclaw still delivers exactly one full artifact, whose ConfigComposer
    # independently reads persisted MCP/CLI state. The projector must not
    # duplicate those reads for a Skill-only mutation.
    assert len(factory.service.runtime_syncs) == 1
    assert factory.service.delivered_effective_mcps == [None]
    assert factory.service.collect_calls == []
    assert repository.list_installed_calls == []
    assert passport.query_calls == []
    assert identity.calls == []


@pytest.mark.asyncio
async def test_teclaw_empty_scope_delivers_nothing():
    """A scope declaring neither half stays a no-op, as it is today."""
    passport = _RuntimePassport()
    factory = _RuntimeFactory()
    runtime = _teclaw_runtime(factory, passport=passport)

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=ProjectionScope(),
    )

    assert factory.service.runtime_syncs == []
    assert factory.service.mcp_projections == []
    assert passport.calls == []


@pytest.mark.asyncio
async def test_teclaw_failed_delivery_returns_pending_and_keeps_other_domains():
    passport = _RuntimePassport()
    factory = _RuntimeFactory()
    async def _refuse(**_):
        return False

    factory.service.project_whole_artifact = _refuse
    runtime = _teclaw_runtime(factory, passport=passport)

    result = await runtime.project(
        bot_id="bot-1", owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.a"})),
    )

    assert result.status == "PENDING"
    assert result.issues[0].code == "ARTIFACT_APPLY_FAILED"
    assert passport.calls


@pytest.mark.asyncio
async def test_runtime_delivery_never_runs_on_the_event_loop():
    """``project_skills`` dispatches its blocking work off the loop's thread.

    Behind it sits a device resolution with a blocking ws-info HTTP call, and
    on a whole-artifact engine a full artifact compose and the outbound apply
    request. Callers reach it from async HTTP handlers
    (``DirectActivationService`` is one), so running it inline lets one slow
    container stall unrelated requests on the same worker — the reason
    ``sync_mcp_desired_state`` already wraps its device calls.

    Asserted against ``SkillSetService`` itself rather than at the projection
    call sites, because that is where the guarantee now lives. It used to be
    the caller's job, parametrised over each call site so an edit that
    un-wrapped one would be caught; making ``project_skills`` async moved the
    ``to_thread`` inside it, so a call site can no longer get this wrong —
    awaiting is the only way to invoke it. One test at the real boundary
    replaces three at its callers.

    Driven through the unbound method over a stub ``self``: the body touches
    only these three attributes, and constructing a whole ``SkillSetService``
    would test its collaborators rather than this dispatch.

    Asserted by thread identity, not by patching ``asyncio.to_thread``: what
    matters is that the blocking work left the loop's thread, not which API
    moved it.
    """
    import threading

    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetService,
    )

    loop_thread = threading.get_ident()
    seen: list[int] = []

    class _StubService:
        user_id = "owner-1"
        entity_id = "owner-1"

        def _sync_symlinks_to_device_if_needed(
            self, user_id, desired_skills, effective_mcps
        ):
            seen.append(threading.get_ident())
            return True

    assert await SkillSetService.project_skills(
        _StubService(), desired_skills=[{"id": "1"}]
    )

    assert seen, "the blocking call never ran — this test proves nothing"
    assert loop_thread not in seen, (
        "project_skills ran its blocking work on the event loop thread; it "
        "must dispatch through asyncio.to_thread so a slow device cannot "
        "block the worker"
    )


def test_engine_projections_declare_the_protocol_as_a_base():
    """Both implementations extend ``EngineRuntimeProjection`` nominally.

    ``runtime_checkable`` would accept them structurally, so this is not about
    making them work — it is about making the contract they answer to visible
    at the class statement, the way ``BaasDeviceSyncService(DeviceSync)`` and
    ``TeclawDeviceSyncService(DeviceSync)`` already do. An implementation that
    dropped the base would still pass every behavioural test here while
    hiding which seam it belongs to.
    """
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        EngineRuntimeProjection,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
        PerDomainRuntimeProjection,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.whole_artifact import (
        WholeArtifactRuntimeProjection,
    )

    for impl in (PerDomainRuntimeProjection, WholeArtifactRuntimeProjection):
        assert EngineRuntimeProjection in impl.__mro__, (
            f"{impl.__name__} must declare EngineRuntimeProjection as a base, "
            "not merely satisfy it structurally"
        )


def test_an_incomplete_engine_projection_cannot_be_constructed():
    """Omitting half the contract must fail at construction, not at use.

    ``@abstractmethod`` is what turns the explicit base into a check. Without
    it a subclass could declare the protocol, implement only ``apply``, pass
    every registry and wiring test, and then raise ``AttributeError`` the
    first time plan resolution asked it to validate — deep inside a mutation,
    with the flush already run.
    """
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        EngineRuntimeProjection,
    )

    class MissingValidate(EngineRuntimeProjection):
        async def apply(self, *, plan, scope, retired_mappings=()) -> None:
            return None

    class MissingApply(EngineRuntimeProjection):
        def validate_plan(self, *, skill_assets, retired_mappings=()) -> None:
            return None

    for incomplete, missing in (
        (MissingValidate, "validate_plan"),
        (MissingApply, "apply"),
    ):
        with pytest.raises(TypeError) as excinfo:
            incomplete()
        assert missing in str(excinfo.value)


def test_the_capability_plan_names_a_boundary_not_a_service():
    """``ResolvedCapabilityPlan.service`` must stay a declared boundary.

    The seam is only replaceable if the contract names what a projection may
    *do*, not who happens to do it today. Importing ``SkillSetService`` here
    would make every implementation and every contract-only reader depend on
    that class, so substituting the runtime service would mean editing the
    contract — which is the coupling the registry exists to remove.

    Both halves matter, so both are asserted: the contract must not reach for
    the concrete service, *and* the concrete service must still satisfy the
    narrowed boundary. Checking only the first would let the boundary drift
    away from the class the composition root actually pairs it with, and the
    mismatch would not surface until a projection ran.
    """
    import inspect
    from pathlib import Path

    from agentclaw.community.core.skill_center import (
        runtime_projection_contract,
    )
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        CapabilityRuntimeBoundary,
    )
    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetService,
    )

    source = Path(runtime_projection_contract.__file__).read_text(encoding="utf-8")
    # Matched on the module path, not on ``import SkillSetService``: the form
    # this replaced was a parenthesised multi-line import, which that narrower
    # string would have walked straight past.
    assert "skill_set_service" not in source, (
        "runtime_projection_contract must not import SkillSetService. Type "
        "ResolvedCapabilityPlan.service against CapabilityRuntimeBoundary and "
        "let the composition root pair the boundary with an implementation."
    )

    def shape(func) -> tuple:
        # ``eval_str`` resolves the contract's annotations, which are strings
        # because that module has ``from __future__ import annotations`` and
        # the service does not. Without it the two sides would differ on
        # nothing but quoting.
        signature = inspect.signature(func, eval_str=True)
        return (
            inspect.iscoroutinefunction(func),
            signature.return_annotation,
            tuple(
                (name, parameter.kind, parameter.annotation, parameter.default)
                for name, parameter in signature.parameters.items()
            ),
        )

    assert issubclass(SkillSetService, CapabilityRuntimeBoundary)
    for name in ("project_skills", "project_mcps"):
        assert shape(getattr(SkillSetService, name)) == shape(
            getattr(CapabilityRuntimeBoundary, name)
        ), (
            f"SkillSetService.{name} no longer matches the boundary it is "
            "wired to. Update CapabilityRuntimeBoundary alongside it."
        )


def test_registry_defaults_unknown_engines_to_the_per_domain_projection():
    """An unregistered engine gets the per-domain contract.

    What keeps ``claude_code`` / ``aicoding`` / ``hermes`` working without an
    entry each, and what makes mis-routing an engine take a wrong entry rather
    than a forgotten right one.
    """
    from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
        PerDomainRuntimeProjection,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.whole_artifact import (
        WholeArtifactRuntimeProjection,
    )

    registry = _registry(
        pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()
    )

    for engine in ("openclaw", "claude_code", "aicoding", "hermes", "unheard-of"):
        assert isinstance(
            registry.for_engine(engine), PerDomainRuntimeProjection
        ), engine
    assert isinstance(
        registry.for_engine("teclaw"), WholeArtifactRuntimeProjection
    )


def test_projector_and_per_domain_contain_no_engine_identity_test():
    """Neither module may test which engine it is looking at.

    The point of the seam: how a runtime consumes a projection is the engine's
    fact, answered by its own implementation. A reintroduced ``== "teclaw"``
    here would work, which is exactly why it needs catching mechanically.
    """
    from pathlib import Path

    from agentclaw.community.core.skill_center.services import (
        bot_runtime_projector,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections import (
        per_domain,
    )

    for module in (bot_runtime_projector, per_domain):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "teclaw" not in source, (
            f"{Path(module.__file__).name} names an engine. Engine-specific "
            "runtime behaviour belongs in that engine's "
            "EngineRuntimeProjection, registered by engine, not branched on "
            "here."
        )


def test_per_domain_delegates_filesystem_delivery_without_layout_knowledge():
    """PerDomain owns half scheduling, not filesystem compatibility."""
    from pathlib import Path

    from agentclaw.community.core.skill_center.services.runtime_projections import (
        per_domain,
    )

    source = Path(per_domain.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "SkillsPoolLayoutRepositoryProtocol",
        "SkillsPoolRuntimeProtocol",
        "runtime_uses_pool_paths",
        "mapping_contract_for",
        "_apply_pool_mappings",
        "_mapping_message",
    ):
        assert forbidden not in source


@pytest.mark.asyncio
async def test_per_domain_engine_keeps_the_scope_split():
    """A per-domain engine still writes only the half the mutation declared.

    The regression guard for the whole change: whatever a whole-artifact
    engine does, an engine whose Skill and MCP halves have separate runtime
    endpoints must keep saving the round trip. An MCP-only scope touches the
    MCP half and nothing else, and the claimed set is still guarded down to
    what the projection actually resolved.
    """
    pool = _RuntimePool()
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.a"})),
    )

    assert factory.service.runtime_syncs == []
    assert len(factory.service.mcp_projections) == 1
    claimed, released, declared = factory.service.mcp_projections[0]
    # Guarded against the projected set: a code the projection never resolved
    # cannot be claimed just because the mutation named it.
    assert claimed <= set(declared)
    assert released == frozenset()
    assert pool.publish_calls == []


@pytest.mark.asyncio
async def test_failed_skill_delivery_does_not_skip_the_mcp_half():
    pool = _FailingPublishRuntimePool()
    factory = _RuntimeFactory()
    repo_skill = RegisteredSkillAsset(
        skill_id=8,
        name="repo-skill",
        git_path="git://team/repo-skill",
    )
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills([repo_skill])),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    result = await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert result.status.value == "PENDING"
    assert result.issues[0].code == "SKILL_RUNTIME_UNAVAILABLE"
    assert len(factory.service.mcp_projections) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bot_status", "log_method"),
    [("OFFLINE", "info"), ("ACTIVE", "warning")],
)
async def test_explicit_desktop_offline_is_pending_without_error_traceback(
    bot_status: str, log_method: str
) -> None:
    from agentclaw.community.core.devices.services.device_context import (
        DeviceOfflineError,
    )
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        ResolvedSkillPlan,
        RuntimeProjectionStatus,
    )
    from agentclaw.community.core.skill_center.runtime_resolver import (
        RuntimeSkillProjection,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
        PerDomainRuntimeProjection,
    )

    delivery = MagicMock()
    delivery.deliver = AsyncMock(side_effect=DeviceOfflineError("offline"))
    projection = PerDomainRuntimeProjection(skill_delivery=delivery)
    plan = ResolvedSkillPlan(
        bot_id="desktop-a",
        owner_id="owner-a",
        bot={"status": bot_status, "bot_type": "desktop"},
        engine="hermes",
        projection=RuntimeSkillProjection(skill_mappings=(), skill_assets=()),
    )

    with patch(
        "agentclaw.community.core.skill_center.services.runtime_projections."
        "per_domain.logger"
    ) as log:
        result = await projection.apply(
            plan=plan,
            scope=ProjectionScope(skills=True),
            service_factory=MagicMock(),
        )

    assert result.status is RuntimeProjectionStatus.PENDING
    assert result.issues[0].code == "DESKTOP_DEVICE_OFFLINE"
    assert result.issues[0].retryable is True
    assert "上线后自动同步" in result.issues[0].reason
    calls = getattr(log, log_method).call_args_list
    assert any("Desktop device offline" in str(call.args[0]) for call in calls)
    log.exception.assert_not_called()


@pytest.mark.asyncio
async def test_non_desktop_no_active_device_keeps_generic_runtime_contract() -> None:
    from agentclaw.community.core.devices.services.device_context import (
        DeviceOfflineError,
    )
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        ResolvedSkillPlan,
    )
    from agentclaw.community.core.skill_center.runtime_resolver import (
        RuntimeSkillProjection,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
        PerDomainRuntimeProjection,
    )

    delivery = MagicMock()
    delivery.deliver = AsyncMock(side_effect=DeviceOfflineError("offline"))
    projection = PerDomainRuntimeProjection(skill_delivery=delivery)
    plan = ResolvedSkillPlan(
        bot_id="service-a",
        owner_id="owner-a",
        bot={"status": "ACTIVE", "bot_type": "service"},
        engine="openclaw",
        projection=RuntimeSkillProjection(skill_mappings=(), skill_assets=()),
    )

    result = await projection.apply(
        plan=plan,
        scope=ProjectionScope(skills=True),
        service_factory=MagicMock(),
    )

    assert result.issues[0].code == "SKILL_RUNTIME_UNAVAILABLE"


@pytest.mark.asyncio
async def test_transient_desktop_connection_failure_is_retryable_warning() -> None:
    from agentclaw.community.core.devices.services.device_context import (
        DeviceConnectionUnavailableError,
    )
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        ResolvedSkillPlan,
        RuntimeProjectionStatus,
    )
    from agentclaw.community.core.skill_center.runtime_resolver import (
        RuntimeSkillProjection,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
        PerDomainRuntimeProjection,
    )

    delivery = MagicMock()
    delivery.deliver = AsyncMock(
        side_effect=DeviceConnectionUnavailableError("timeout")
    )
    projection = PerDomainRuntimeProjection(skill_delivery=delivery)
    plan = ResolvedSkillPlan(
        bot_id="desktop-a",
        owner_id="owner-a",
        bot={"status": "ACTIVE"},
        engine="openclaw",
        projection=RuntimeSkillProjection(skill_mappings=(), skill_assets=()),
    )

    with patch(
        "agentclaw.community.core.skill_center.services.runtime_projections."
        "per_domain.logger"
    ) as log:
        result = await projection.apply(
            plan=plan,
            scope=ProjectionScope(skills=True),
            service_factory=MagicMock(),
        )

    assert result.status is RuntimeProjectionStatus.PENDING
    assert result.issues[0].code == "SKILL_RUNTIME_UNAVAILABLE"
    assert result.issues[0].retryable is True
    assert any(
        "transient" in str(call.args[0]).lower()
        for call in log.warning.call_args_list
    )
    log.exception.assert_not_called()

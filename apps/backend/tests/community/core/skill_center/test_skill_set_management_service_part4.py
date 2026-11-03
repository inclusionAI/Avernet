from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredState,
    DesiredStateMutation,
)
from agentclaw.community.core.caller_identity.models import McpCallType
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.services.skill_set_management_service import (
    SkillSetManagementService,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
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
async def test_projection_scope_is_the_projected_codes_not_the_config_rows():
    """The projection decides the scope; the call-config table only colours it.

    The table is per-Bot, not per-projection, so it can hold rows for MCPs
    this Bot no longer has. Those must not reappear in the manifest —
    ``updatePassport`` replaces the list wholesale, so an extra item would
    re-grant a capability the Bot has lost.
    """
    passport = _RuntimePassport()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(
            {
                "mcp.weather": McpCallType.CALLER,
                "mcp.retired": McpCallType.CALLER,
            }
        ),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    scope = passport.calls[0]["resource_scope"]
    assert [item["mcp_code"] for item in scope["mcp_items"]] == scope["mcp_codes"]
    assert "mcp.retired" not in scope["mcp_codes"]


@pytest.mark.asyncio
async def test_projection_fails_closed_without_a_bot_primary_key():
    """No primary key fails the projection instead of guessing Owner.

    Defaulting here would be the same silent demotion the missing mcp_items
    caused, just from a different direction, so it fails closed like every
    other unreadable input on this path.
    """
    passport = _RuntimePassport()
    identity = _RuntimeCallerIdentity({"mcp.weather": McpCallType.CALLER})
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_PkLessRuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=identity,
    )

    with pytest.raises(SkillSetRuntimeReconcileError):
        await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )

    # Closed means nothing was written, not merely that it stopped: the
    # device allow-list and the symlink sync both precede the Passport call,
    # and a mid-write abort would leave the device holding forward state that
    # the compensating projection could not undo.
    assert factory.service.mcp_codes is None
    assert factory.service.desired_skills is None
    assert passport.calls == []
    assert identity.calls == []


@pytest.mark.asyncio
async def test_failed_mcp_projection_keeps_the_declared_mcp_delta():
    repository = _Repository()
    runtime = _Runtime()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    result = await service.add_mcp(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        server_code="mcp.new",
    )

    assert result["runtime_projection"]["status"] == "PENDING"
    assert len(runtime.reconcile_calls) == 1
    assert runtime.reconcile_calls[0]["scope"].claimed_mcp == frozenset({"mcp.new"})
    assert runtime.reconcile_calls[0]["scope"].released_mcp == frozenset()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["add_mcp", "remove_mcp"])
async def test_an_unchanged_mcp_membership_declares_no_runtime_delta(
    method_name: str,
) -> None:
    class _UnchangedMcpRepository(_Repository):
        @staticmethod
        def _unchanged(server_code: str) -> DesiredStateMutation:
            return DesiredStateMutation(
                item={"server_code": server_code},
                changed=False,
                previous_state=CapabilityDesiredState(set(), {}, {}),
            )

        def add_mcp(self, **kwargs) -> DesiredStateMutation:
            return self._unchanged(kwargs["server_code"])

        def remove_mcp(self, **kwargs) -> DesiredStateMutation:
            return self._unchanged(kwargs["server_code"])

    runtime = _Runtime(fail_first=False)
    service = _skill_service(_UnchangedMcpRepository(), runtime)

    result = await getattr(service, method_name)(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        server_code="mcp.weather",
    )

    assert result["changed"] is False
    (call,) = runtime.reconcile_calls
    assert call["scope"] == ProjectionScope()


@pytest.mark.asyncio
async def test_reconcile_scope_claims_every_projected_code():
    """A restart or upload has no mutation to ask, so nothing is assumed.

    The device may hold no configuration at all on that path, so every
    projected code is treated as newly claimed — the behaviour that existed
    before delivery was scoped, preserved exactly.
    """
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    ((claimed, released),) = factory.service.deliveries
    assert claimed == frozenset(factory.service.mcp_codes)
    assert released == frozenset()


@pytest.mark.asyncio
async def test_a_declared_claim_delivers_only_that_code():
    """Problem 3 at the projector: the guard cannot enlarge a declared scope.

    The Bot's projected set has three codes; declaring one claim must deliver
    exactly that one while the allow-list still declares all three.
    """
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.weather"})),
    )

    assert factory.service.deliveries == [(frozenset({"mcp.weather"}), frozenset())]


@pytest.mark.asyncio
async def test_removing_direct_override_refreshes_when_skill_still_supplies_mcp():
    factory = _RuntimeFactory()
    runtime = _scoped_projector(factory=factory)

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(
            mcp=True,
            released_mcp=frozenset({"mcp.weather"}),
            updated_mcp=frozenset({"mcp.weather"}),
        ),
    )

    assert factory.service.deliveries == [(frozenset({"mcp.weather"}), frozenset())]
    # Declaration stays total even though delivery did not.
    assert factory.service.mcp_codes == {
        "mcp.weather",
        "mcp.template-preset",
        "hitl",
    }


@pytest.mark.asyncio
async def test_a_release_still_supplied_by_policy_is_not_deleted():
    """REL's removal guard, recovered structurally.

    ``mcp.template-preset`` reaches the projection through the engine/template
    default policy rather than Set membership, so a Set dropping its claim
    must not delete it from the device. Nothing else stops that — the
    ``- codes`` guard is the whole protection.
    """
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(
            mcp=True, released_mcp=frozenset({"mcp.template-preset"})
        ),
    )

    assert factory.service.deliveries == [(frozenset(), frozenset())]


@pytest.mark.asyncio
async def test_a_release_no_longer_supplied_is_deleted():
    """The other side of the guard: a genuinely gone code is withdrawn.

    Without this, removing an MCP left its endpoint, api_key and headers on
    the container forever — the regression with no production caller at all.
    """
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True, released_mcp=frozenset({"mcp.gone"})),
    )

    assert factory.service.deliveries == [(frozenset(), frozenset({"mcp.gone"}))]


@pytest.mark.asyncio
async def test_runtime_reconcile_fails_closed_when_effective_cli_scope_cannot_be_read():
    factory = _RuntimeFactory()
    passport = _FailingRuntimePassport()
    pool = _RuntimePool()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    with pytest.raises(SkillSetRuntimeReconcileError):
        await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )

    assert passport.calls == []
    assert pool.apply_calls == []
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_runtime_reconcile_requires_and_uses_mapping_v3_for_center():
    factory = _RuntimeFactory()
    pool = _CenterRuntimePool()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_CenterRuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.service.mcp_codes is not None
    assert len(pool.publish_calls) == len(pool.verify_calls) == 1
    # A product capability mutation uses the tolerant contract.  Pool
    # cutover/recovery remains STRICT at its own call sites; this assertion
    # protects the public Runtime Projector seam from accidentally turning a
    # normal SkillSet operation back into an all-or-nothing filesystem gate.
    assert pool.publish_calls[0]["apply_mode"].value == "BEST_EFFORT"
    assert pool.verify_calls[0]["apply_mode"].value == "BEST_EFFORT"
    assert pool.publish_calls[0]["mapping_contract_version"] == (
        "skills-pool-mapping-v3"
    )
    assert pool.publish_calls[0]["mappings"][0].to_dict() == {
        "corpus": "center",
        "link_name": "center-skill",
            "skill_uuid": "00000000-0000-4000-8000-000000000007",
        "sc_version_number": "3.0.0",
    }


@pytest.mark.asyncio
async def test_center_projection_requests_bot_restart_when_mount_is_missing():
    pool = _CenterMountMissingRuntimePool()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_CenterRuntimeSkills()),
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
    assert result.issues[0].code == "CENTER_RUNTIME_RESTART_REQUIRED"
    assert result.issues[0].reason == (
        "Bot 尚未加载 Skill Center 目录，请重启 Bot 后重试"
    )
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_transient_mapping_publish_failure_stays_pending_after_verify():
    """A failed link write must remain retryable, not become a false conflict."""
    from agentclaw.community.core.skills_pool.models import (
        MappingItemResult,
        MappingProjectionStatus,
        MappingPublishResult,
        MappingVerificationResult,
    )

    target = "/home/admin/.claude/skills/center-skill"
    source = "/home/admin/.claude_code/workspace/skills-pool/skill-center/00000000-0000-4000-8000-000000000007/3.0.0"
    pool = _CenterRuntimePool()
    pool._published = MappingPublishResult(
        published=False,
        status=MappingProjectionStatus.PENDING,
        items=(
            MappingItemResult(
                target=target,
                source=source,
                status=MappingProjectionStatus.PENDING,
                code="MAPPING_PUBLISH_IO_ERROR",
                retryable=True,
            ),
        ),
    )
    pool._verified = MappingVerificationResult(
        valid=False,
        status=MappingProjectionStatus.DEGRADED,
        items=(
            MappingItemResult(
                target=target,
                source=source,
                status=MappingProjectionStatus.DEGRADED,
                code="TARGET_NOT_SYMLINK",
            ),
        ),
    )
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_CenterRuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    result = await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=ProjectionScope(skills=True)
    )

    assert result.status.value == "PENDING"
    assert result.issues[0].code == "MAPPING_PUBLISH_IO_ERROR"
    assert result.issues[0].retryable is True


@pytest.mark.asyncio
async def test_coding_template_uses_aicoding_for_center_probe_but_keeps_logical_engine():
    factory = _RuntimeFactory()
    pool = _CenterRuntimePool()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_AicodingImageRuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_CenterRuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.kwargs["engine_type"] == "claude_code"
    assert pool.probe_calls == [
        {"bot_id": "bot-1", "user_id": "true-owner", "engine": "aicoding"}
    ]
    assert len(pool.publish_calls) == len(pool.verify_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bots", "skills", "expected_engine"),
    [
        (_PlainClaudeCodeRuntimeBots(), _RuntimeSkills(), "claude_code"),
        (_HistoricalAicodingRuntimeBots(), _RuntimeSkills(), "aicoding"),
    ],
)
async def test_existing_coding_runtime_uses_its_resolved_layout(
    bots, skills, expected_engine: str
) -> None:
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=bots,
        repository=_McpInstallations(),
        reader=_reader(skills),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.kwargs["engine_type"] == expected_engine
    assert factory.service.desired_skills == []


@pytest.mark.asyncio
async def test_teclaw_v4_consumes_exact_center_through_whole_artifact():
    pool = _CenterRuntimePool()
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_TeclawRuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_CenterRuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert pool.probe_calls == []
    assert pool.publish_calls == []
    assert factory.service.desired_skills == [
        {
            "id": "7",
            "name": "center-skill",
            "git_path": "center://public-skill",
            "skill_uuid": "00000000-0000-4000-8000-000000000007",
            "sc_version_number": "3.0.0",
        }
    ]


@pytest.mark.asyncio
async def test_teclaw_v4_repo_projection_uses_artifact_runtime_not_pool_mapping():
    pool = _RuntimePool()
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_TeclawRuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_TeclawRuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.service.desired_skills == [
        {
            "id": "8",
            "name": "repo-skill",
            "git_path": "git://team/repo-skill",
            "skill_uuid": None,
            "sc_version_number": None,
        }
    ]
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_non_skill_projection_never_writes_skill_mappings():
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

    await runtime.project_mcp_and_cli(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claim_all_mcp=True),
    )

    assert factory.service.desired_skills is None
    assert factory.service.mcp_codes is not None
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_non_skill_entry_rejects_a_scope_without_mcp():
    runtime = _scoped_projector()

    with pytest.raises(ValueError, match="requires scope.mcp=True"):
        await runtime.project_mcp_and_cli(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope(skills=True),
        )


@pytest.mark.parametrize(
    "scope",
    [
        ProjectionScope(skills=True),
        ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.a"})),
        ProjectionScope(mcp=True, released_mcp=frozenset({"mcp.a"})),
        ProjectionScope(
            skills=True, mcp=True, claimed_mcp=frozenset({"mcp.a"})
        ),
        ProjectionScope.everything(),
    ],
    ids=["skills", "mcp-claim", "mcp-release", "both", "everything"],
)
@pytest.mark.asyncio
async def test_teclaw_projects_the_whole_artifact_once_per_scope_shape(scope):
    """One projection, one delivery — whatever the mutation declared.

    Every shape a production caller constructs. The runtime recomposes the
    whole artifact from the database and discards its arguments, so a second
    call could only restate the first; which half a mutation touched cannot
    change that.
    """
    factory = _RuntimeFactory()
    runtime = _teclaw_runtime(factory)

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=scope,
    )

    assert len(factory.service.runtime_syncs) == 1
    assert factory.service.mcp_projections == []

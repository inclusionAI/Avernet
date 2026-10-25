from __future__ import annotations
from dataclasses import replace
from unittest.mock import MagicMock
import pytest
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
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
from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
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
async def test_skill_set_activate_claims_skill_dependency_mcp_configuration():
    class _DependentSet(_Repository):
        def set_skill_set_active(self, **kwargs) -> DesiredStateMutation:
            return replace(
                super().set_skill_set_active(**kwargs),
                mcp_codes=frozenset({"mcp.weather"}),
            )

    runtime = _Runtime(fail_first=False)
    await _skill_service(_DependentSet(), runtime).activate(
        bot_id="bot-1", owner_id="true-owner", user_id="true-owner",
        set_id="set-1",
    )

    assert runtime.reconcile_calls[0]["scope"] == ProjectionScope(
        skills=True, mcp=True, claimed_mcp=frozenset({"mcp.weather"}),
    )


@pytest.mark.asyncio
async def test_skill_set_noop_keeps_full_projection_for_runtime_retry():
    class _UnchangedSet(_Repository):
        def set_skill_set_active(self, **kwargs) -> DesiredStateMutation:
            return replace(super().set_skill_set_active(**kwargs), changed=False)

    runtime = _Runtime(fail_first=False)
    await _skill_service(_UnchangedSet(), runtime).deactivate(
        bot_id="bot-1", owner_id="true-owner", user_id="true-owner",
        set_id="set-1",
    )

    assert runtime.reconcile_calls[0]["scope"] == ProjectionScope(
        skills=True, mcp=True,
    )


@pytest.mark.asyncio
async def test_skill_set_deactivation_projects_mcp_when_dependencies_are_unknown():
    class _UnknownSet(_Repository):
        def set_skill_set_active(self, **kwargs) -> DesiredStateMutation:
            return replace(
                super().set_skill_set_active(**kwargs), mcp_scope_unknown=True,
            )

    runtime = _Runtime(fail_first=False)
    await _skill_service(_UnknownSet(), runtime).deactivate(
        bot_id="bot-1", owner_id="true-owner", user_id="true-owner",
        set_id="set-1",
    )

    assert runtime.reconcile_calls[0]["scope"] == ProjectionScope(
        skills=True, mcp=True,
    )


@pytest.mark.parametrize("bots", [_PlainClaudeCodeBots(), _LiteralAicodingBots()])
def test_existing_coding_bot_metadata_mutations_ignore_product_creation_matrix(
    bots,
) -> None:
    repository = _CreateRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=bots,
        runtime=_SuccessfulRuntime(),
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

    created = service.create_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        name="set",
        description=None,
    )
    updated = service.update_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        name="new-name",
        description=None,
    )

    assert created["id"] == "set-1"
    assert updated["id"] == "set-1"


def test_historical_bot_may_delete_an_inactive_skill_set_without_new_runtime_write():
    service = SkillSetManagementService(
        repository=_Repository(),
        bot_repo=_UnsupportedBots(),
        runtime=_SuccessfulRuntime(),
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

    service.delete_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
    )


def test_legacy_name_or_git_path_materializes_market_repo_skill_before_membership():
    """The legacy batch adapter keeps its historical implicit Repo creation."""
    repository = _LegacyResolutionRepository()
    factory = _LegacyFactory()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=factory,
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    stable_id = service.resolve_legacy_skill_id(
        bot_id="bot-1",
        owner_id="true-owner",
        actor_id="true-owner",
        identifier="market/example",
    )

    assert stable_id == "stable-skill-id"
    assert repository.identifiers == ["market/example"]
    assert factory.calls == [
        {
            "entity_id": "true-owner",
            "bot_id": "bot-1",
            "engine_type": "openclaw",
            "entity_type": "staff",
        }
    ]
    assert factory.service.calls == [("market/example", "true-owner", "bot-1")]


@pytest.mark.asyncio
async def test_runtime_mapping_snapshot_has_no_runtime_side_effects():
    repository = _McpInstallations()
    pool = _RuntimePool()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=repository,
        reader=_reader(
            _RuntimeSkills(
                [
                    RegisteredSkillAsset(
                        skill_id=1,
                        name="qa",
                        git_path="local://qa",
                    ),
                    RegisteredSkillAsset(
                        skill_id=2,
                        name="eva",
                        git_path="git://business/eva",
                    ),
                ]
            ),
            repository=repository,
        ),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    snapshot = await runtime.snapshot_skill_mappings(
        bot_id="bot-1", owner_id="true-owner"
    )

    assert snapshot == (
        PoolSkillMapping(corpus="local", relative_path="qa", link_name="qa"),
        PoolSkillMapping(corpus="repo", relative_path="business/eva", link_name="eva"),
    )
    # The reader's DB-side flush runs on every read; what a snapshot must
    # never do is touch the engine.
    assert len(repository.flush_calls) == 1
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_runtime_projection_flushes_installations_first():
    repository = _McpInstallations()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=repository,
        reader=_reader(_RuntimeSkills(), repository=repository),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert repository.flush_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "entity-1",
            "env": "pre",
            "engine_type": "openclaw",
            "default_engine_types": ("openclaw",),
        }
    ]


@pytest.mark.asyncio
async def test_projection_flush_prefers_the_layout_engine_for_default_sets():
    """A coding template runs in an AICoding image while staying claude_code
    logically, so the Default-Set scope tries the filesystem identity first."""
    repository = _McpInstallations()
    bots = _AicodingImageRuntimeBots()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=bots,
        repository=repository,
        reader=_reader(_RuntimeSkills(), repository=repository, bots=bots),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert repository.flush_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "entity-1",
            "env": "pre",
            "engine_type": "claude_code",
            "default_engine_types": ("aicoding", "claude_code"),
        }
    ]


@pytest.mark.asyncio
async def test_runtime_projection_fails_before_engine_writes_when_flush_fails():
    repository = _FailingFlushRepository()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=repository,
        reader=_reader(_RuntimeSkills(), repository=repository),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    with pytest.raises(RuntimeError, match="installation persistence unavailable"):
        await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )


@pytest.mark.asyncio
async def test_runtime_projection_fails_closed_when_default_mcp_policy_is_unavailable():
    factory = _FailingPolicyCollectFactory()
    passport = _RuntimePassport()
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

    assert factory.service.mcp_codes is None
    assert passport.calls == []
    assert pool.apply_calls == []
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_runtime_projection_mcp_inputs_agree_when_the_union_overlaps():
    """installed ∪ effective_default: a code arriving through both inputs —
    the repo's installed listing and the collect union that now contains
    installed codes too — is delivered exactly once and drops nothing."""
    factory = _OverlappingCollectFactory()
    passport = _RuntimePassport()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.service.mcp_codes == {"mcp.weather", "mcp.template-preset"}
    assert passport.calls[0]["resource_scope"]["mcp_codes"] == [
        "mcp.template-preset",
        "mcp.weather",
    ]


@pytest.mark.asyncio
async def test_runtime_reconcile_projects_full_mcp_desired_state():
    from agentclaw.community.core.skill_center.capability_state_contract import BotCapabilitySnapshot
    factory = _RuntimeFactory()
    passport = _RuntimePassport()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.kwargs == {
        "user_id": "true-owner",
        "entity_id": "entity-1",
        "bot_id": "bot-1",
        "engine_type": "openclaw",
        "entity_type": "staff",
    }
    assert factory.service.mcp_codes == {
        "mcp.weather",
        "mcp.template-preset",
        "hitl",
    }
    assert factory.service.collect_calls == [
        {
            "entity_id": "entity-1",
            "bot_id": "bot-1",
            "user_id": "true-owner",
            "entity_type": "staff",
            "engine_type": "openclaw",
                "strict_policy_context": True,
                "capability_snapshot": BotCapabilitySnapshot(
                    "bot-1", "entity-1", (), frozenset({"mcp.weather"})
                ),
        }
    ]
    assert passport.calls == [
        {
            "bot_id": "bot-1",
            "user_id": "true-owner",
            "engine_type": "openclaw",
            "resource_scope": {
                "mcp_codes": ["mcp.template-preset", "mcp.weather"],
                # Identity travels with every projection. Omitting mcp_items
                # does not preserve existing identity: the Passport port
                # substitutes bare items and writes identity_mode="owner"
                # explicitly, so a Caller MCP would be demoted by any
                # unrelated SkillSet mutation.
                "mcp_items": [
                    {"mcp_code": "mcp.template-preset", "identity_mode": "owner"},
                    {"mcp_code": "mcp.weather", "identity_mode": "owner"},
                ],
                "cli_items": [{"cli_code": "kept-cli", "cli_name": "Kept", "cli_desc": None, "identity_mode": "owner"}],
            },
        }
    ]


@pytest.mark.asyncio
async def test_projection_preserves_caller_identity_for_configured_mcp():
    """A Caller MCP keeps Caller across a projection that never mentions it.

    This is the regression: updatePassport replaces the MCP list wholesale
    and the Passport port writes identity_mode="owner" for any item that
    arrives without one, so a projection that omitted identity moved every
    Caller MCP onto the bot owner's credential.
    """
    passport = _RuntimePassport()
    identity = _RuntimeCallerIdentity({"mcp.weather": McpCallType.CALLER})
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=identity,
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert _passport_mcp_items(passport) == [
        {"mcp_code": "mcp.template-preset", "identity_mode": "owner"},
        {"mcp_code": "mcp.weather", "identity_mode": "caller"},
    ]
    # Looked up by the Bot's primary key and the engine it actually runs.
    assert identity.calls == [(42, "openclaw")]


@pytest.mark.asyncio
async def test_projection_preserves_agentpass_only_caller_identity_without_sparse_row():
    """A full AgentPass snapshot colours desired MCP membership before overwrite."""

    class _HistoricalCallerPassport(_RuntimePassport):
        def query_agent_passport(self, bot_id: str, owner_id: str) -> dict:
            assert (bot_id, owner_id) == ("bot-1", "true-owner")
            return {
                "mcps": [
                    {"mcp_code": "mcp.weather", "identity_mode": "caller"},
                ],
                "clis": [{"cli_code": "kept-cli", "cli_name": "Kept", "identity_mode": "owner"}],
            }

    passport = _HistoricalCallerPassport()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert _passport_mcp_items(passport) == [
        {"mcp_code": "mcp.template-preset", "identity_mode": "owner"},
        {"mcp_code": "mcp.weather", "identity_mode": "caller"},
    ]


@pytest.mark.asyncio
async def test_projection_logs_agentpass_scope_success_without_snapshot_secret(caplog):
    """AgentPass projection observability exposes scope counts, not its raw snapshot."""

    class _TokenBearingPassport(_RuntimePassport):
        def query_agent_passport(self, bot_id: str, owner_id: str) -> dict:
            snapshot = super().query_agent_passport(bot_id, owner_id)
            return {**snapshot, "token": "passport-token-secret"}

    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_TokenBearingPassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    with caplog.at_level("INFO", logger="start"):
        await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )

    logged = caplog.text
    assert "agentpass_runtime_scope_update_requested" in logged
    assert "agentpass_runtime_scope_update_succeeded" in logged
    assert "status=succeeded" in logged
    assert "duration_ms" in logged
    assert "passport-token-secret" not in logged


@pytest.mark.asyncio
async def test_projection_logs_agentpass_snapshot_failure_without_secret(caplog):
    """The snapshot failure event records exception type only before wrapping."""

    class _FailingPassport(_RuntimePassport):
        def query_agent_passport(self, bot_id: str, owner_id: str) -> dict:
            raise RuntimeError("passport-token-secret")

    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_FailingPassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    with caplog.at_level("INFO", logger="start"):
        result = await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )

    assert result.status.value == "PENDING"
    assert result.issues[0].code == "PASSPORT_RUNTIME_UNAVAILABLE"
    logged = caplog.text
    assert "agentpass_runtime_scope_update_requested" in logged
    assert "agentpass_runtime_scope_update_failed" in logged
    assert "stage=snapshot" in logged
    assert "error_type=RuntimeError" in logged
    assert "duration_ms" in logged
    assert "passport-token-secret" not in logged


@pytest.mark.asyncio
async def test_projection_logs_agentpass_update_failure_without_secret(caplog):
    """The overwrite failure has the same low-sensitive event contract."""

    class _FailingPassport(_RuntimePassport):
        def update_passport(self, **kwargs) -> None:
            raise RuntimeError("passport-token-secret")

    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_FailingPassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    with caplog.at_level("INFO", logger="start"):
        result = await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )

    assert result.status.value == "PENDING"
    assert result.issues[0].code == "PASSPORT_RUNTIME_UNAVAILABLE"
    assert "agentpass_runtime_scope_update_requested" in caplog.text
    assert "agentpass_runtime_scope_update_failed" in caplog.text
    assert "stage=update" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "passport-token-secret" not in caplog.text


@pytest.mark.asyncio
async def test_projection_defaults_to_owner_without_a_call_config_row():
    """The call-config table is sparse: a missing row means Owner."""
    passport = _RuntimePassport()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert _passport_mcp_items(passport) == [
        {"mcp_code": "mcp.template-preset", "identity_mode": "owner"},
        {"mcp_code": "mcp.weather", "identity_mode": "owner"},
    ]

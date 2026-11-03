from __future__ import annotations
from dataclasses import replace
from unittest.mock import MagicMock
import pytest
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.errors import (
    McpEndpointUnavailableError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.services.skill_set_management_service import (
    SkillSetManagementService,
)
from agentclaw.community.core.skill_center.skill_set_batch import (
    SkillSetSkillOutcome,
)
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    LegacySkillSetScope,
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
async def test_add_mcp_rejects_an_intranet_only_remote_server_before_write() -> None:
    class _IntranetOnlyCenter(_McpCenter):
        def get_mcp_detail(self, _server_code: str) -> dict:
            return {
                "accessLevel": "PUBLIC",
                "runMode": "REMOTE",
                "endpoints": [
                    {
                        "networkType": "INTRANET",
                        "env": "PROD",
                        "transportProtocol": "STREAMABLE_HTTP",
                        "url": "mcp.internal.only",
                    }
                ],
            }

    repository = _Repository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_IntranetOnlyCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    with pytest.raises(McpEndpointUnavailableError):
        await service.add_mcp(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="set-1",
            server_code="mcp.intranet.only",
        )

    assert repository.add_mcp_calls == []


@pytest.mark.asyncio
async def test_add_mcp_validates_with_bot_owner_config_not_collaborator_config() -> None:
    class _PreIntranetCenter(_McpCenter):
        def get_mcp_detail(self, _server_code: str) -> dict:
            return {
                "accessLevel": "PUBLIC",
                "runMode": "REMOTE",
                "endpoints": [
                    {
                        "networkType": "OFFICE",
                        "env": "PROD",
                        "transportProtocol": "STREAMABLE_HTTP",
                        "url": "https://mcp.example.test/prod",
                    },
                    {
                        "networkType": "INTRANET",
                        "env": "PRE",
                        "transportProtocol": "STREAMABLE_HTTP",
                        "url": "https://mcp.example.test/pre",
                    },
                ],
            }

    repository = _Repository()
    config = _OwnerSensitiveMcpConfig()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_PreIntranetCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=config,
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    with pytest.raises(McpEndpointUnavailableError) as exc_info:
        await service.add_mcp(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="collaborator",
            set_id="set-1",
            server_code="mcp.owner.pre.intranet",
        )

    assert "PROD" not in str(exc_info.value)
    assert config.user_ids == ["true-owner"]
    assert repository.add_mcp_calls == []


@pytest.mark.asyncio
async def test_collaborator_command_keeps_desired_state_and_uses_true_owner():
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

    result = await service.activate(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="collaborator",
        set_id="set-1",
    )

    # The hook that recorded (bot, true-owner, actor) is gone: the seam
    # adjudicates before the handler runs, against the same OwnerIdDep the
    # handler acts on. What is still this service's to prove is that the *true*
    # owner — not the caller — reaches the runtime, which the next lines do.
    assert result["runtime_projection"]["status"] == "PENDING"
    assert runtime.owners == ["true-owner"]
    assert runtime.snapshot_calls == [
        {"bot_id": "bot-1", "owner_id": "true-owner"},
        {"bot_id": "bot-1", "owner_id": "true-owner"},
    ]
    _activate_scope = ProjectionScope(skills=True)
    assert runtime.reconcile_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "retired_mappings": (),
            "scope": _activate_scope,
        },
    ]
    assert repository.restore_calls == []


@pytest.mark.asyncio
async def test_deactivate_retires_mappings_removed_from_the_runtime_projection():
    class _McpSetRepository(_Repository):
        def set_skill_set_active(self, **kwargs) -> DesiredStateMutation:
            mutation = super().set_skill_set_active(**kwargs)
            return replace(
                mutation,
                mcp_codes=frozenset({"mcp.weather"}),
            )

    repository = _McpSetRepository()
    runtime = _Runtime(snapshots=[_Runtime._skill_mappings(), ()], fail_first=False)
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

    await service.deactivate(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="collaborator",
        set_id="set-1",
    )

    assert runtime.reconcile_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "retired_mappings": _Runtime._skill_mappings(),
            # Deactivation withdraws callable state but retains the Set's MCP
            # configuration. The mutation result deliberately carries the
            # Set's code to prove it is not treated as a physical release.
            "scope": ProjectionScope(skills=True, mcp=True),
        }
    ]


def test_create_rejects_missing_bot_instead_of_creating_orphan_set():
    service = SkillSetManagementService(
        repository=_Repository(),
        bot_repo=_MissingBots(),
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

    # The control plane speaks one error vocabulary: an invisible Bot scope is
    # a SkillSet not-found, so the HTTP adapter maps a single family rather
    # than also having to know about the Local Skill errors.
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        service.create_set(
            bot_id="missing",
            owner_id="actor",
            user_id="actor",
            name="set",
            description=None,
        )


def test_default_create_rejects_missing_bot_instead_of_creating_orphan_set():
    repository = _CreateRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_MissingBots(),
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

    with pytest.raises(SkillSetControlPlaneNotFoundError):
        service.create_set(
            bot_id="default",
            owner_id="actor",
            user_id="actor",
            name="set",
            description=None,
        )

    assert repository.create_calls == []


def test_default_create_uses_owner_qualified_bot_lookup():
    owner_id = "owner-a"
    bots = _SharedDefaultBots(owner_id)
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

    service.create_set(
        bot_id="default",
        owner_id=owner_id,
        user_id=owner_id,
        name="owner set",
        description=None,
    )

    assert bots.lookups == [
        ("default", owner_id),
    ]
    assert repository.create_calls[0]["owner_id"] == owner_id


def test_legacy_set_scope_recovers_persisted_bot_then_applies_actor_acl() -> None:
    repository = _LegacyScopeRepository(
        LegacySkillSetScope(owner_id="true-owner", bot_id="bot-1")
    )
    authorization = _Authorization()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=authorization,
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    scope = service.resolve_legacy_set_scope(
        set_id="set-1",
        actor_id="collaborator",
        owner_id_hint=None,
    )

    assert scope == LegacySkillSetScope(owner_id="true-owner", bot_id="bot-1")
    assert repository.scope_calls == ["set-1"]
    assert authorization.calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "actor_id": "collaborator",
        }
    ]


def test_legacy_set_scope_rejects_conflicting_owner_hint() -> None:
    repository = _LegacyScopeRepository(
        LegacySkillSetScope(owner_id="true-owner", bot_id="bot-1")
    )
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
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

    with pytest.raises(SkillSetControlPlaneNotFoundError):
        service.resolve_legacy_set_scope(
            set_id="set-1",
            actor_id="collaborator",
            owner_id_hint="wrong-owner",
        )


def test_addressed_create_persists_metadata_without_runtime_reconcile() -> None:
    repository = _CreateRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
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

    result = service.create_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        name="metadata-only",
        description=None,
    )

    assert result["id"] == "set-1"
    assert repository.create_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "name": "metadata-only",
            "description": None,
            "engine_type": "openclaw",
        }
    ]


def test_create_active_empty_set_does_not_require_runtime_readiness() -> None:
    repository = _CreateRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_NotReadyApplicationCodingBots(),
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

    result = service.create_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        name="draft",
        description=None,
    )

    assert result["is_active"] is True
    assert repository.create_calls[0]["engine_type"] == "claude_code"


def test_inactive_set_metadata_updates_do_not_require_runtime_readiness() -> None:
    repository = _Repository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_NotReadyApplicationCodingBots(),
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

    updated = service.update_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        name="renamed draft",
        description=None,
    )
    service.delete_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
    )

    assert updated["id"] == "set-1"
    assert repository.update_calls[0]["engine_type"] == "claude_code"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "resource_kwargs"),
    [
        ("add_skills", {"skill_ids": ["skill-1"]}),
        ("remove_skills", {"skill_ids": ["skill-1"]}),
        ("add_mcp", {"server_code": "mcp.weather"}),
        ("remove_mcp", {"server_code": "mcp.weather"}),
    ],
)
async def test_inactive_set_membership_does_not_require_runtime_readiness(
    method_name: str, resource_kwargs: dict[str, str]
) -> None:
    repository = _InactiveMembershipRepository()
    runtime = _Runtime(fail_first=False)
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_NotReadyApplicationCodingBots(),
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

    result = await getattr(service, method_name)(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        **resource_kwargs,
    )

    if method_name in {"add_skills", "remove_skills"}:
        assert result == [SkillSetSkillOutcome(skill_id="skill-1", changed=True)]
    else:
        assert result["is_active"] is False
    assert repository.membership_calls[0][0] == (
        (
            "add_skill"
            if method_name == "add_skills"
            else "remove_skill"
            if method_name == "remove_skills"
            else method_name
        )
    )
    assert runtime.snapshot_calls == []
    assert runtime.reconcile_calls == []


@pytest.mark.asyncio
async def test_active_set_membership_commits_when_runtime_is_not_ready() -> None:
    repository = _ActiveMembershipRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_NotReadyApplicationCodingBots(),
        runtime=_Runtime(fail_first=False),
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

    outcomes = await service.add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["skill-1"],
    )

    assert outcomes[0].changed is True
    assert len(repository.membership_calls) == 1


def test_default_read_rejects_missing_bot():
    service = SkillSetManagementService(
        repository=_Repository(),
        bot_repo=_MissingBots(),
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

    with pytest.raises(SkillSetControlPlaneNotFoundError):
        service.get_set(
            bot_id="default", owner_id="owner", user_id="owner", set_id="set-1"
        )


@pytest.mark.asyncio
async def test_legacy_sync_activates_additively_without_replacing_other_sets():
    repository = _Repository()
    runtime = _Runtime(fail_first=False)
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

    await service.legacy_activate(
        bot_id="bot-1",
        owner_id="true-owner",
        actor_id="true-owner",
        set_id="set-1",
    )

    assert repository.set_active_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "set_id": "set-1",
            "active": True,
            "engine_type": "openclaw",
            "default_engine_types": ("openclaw",),
        }
    ]
    assert runtime.reconcile_calls[0]["scope"] == ProjectionScope(skills=True)


@pytest.mark.asyncio
async def test_legacy_default_sync_uses_owner_qualified_bot_lookup():
    owner_id = "owner-a"
    bots = _SharedDefaultBots(owner_id)
    repository = _Repository()
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

    await service.legacy_activate(
        bot_id="default",
        owner_id=owner_id,
        actor_id=owner_id,
        set_id="set-1",
    )

    assert bots.lookups == [("default", owner_id)]
    assert repository.set_active_calls[0]["owner_id"] == owner_id

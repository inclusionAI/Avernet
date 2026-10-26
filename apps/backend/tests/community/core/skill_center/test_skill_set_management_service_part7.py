from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.errors import (
    McpEndpointUnavailableError,
    SkillSetControlPlaneConflictError,
    McpPermissionDeniedError,
)
from agentclaw.community.core.skill_center.services.skill_set_management_service import (
    SkillSetManagementService,
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
async def test_default_mcp_exclusion_wire_mirrors_the_skill_wire():
    repository = _DefaultTargetRepository(excluded_codes={"mcp.back"})
    runtime = _ProjectionCountingRuntime()
    service = _default_wire_service(repository, runtime)

    removed = await service.remove_mcp(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="9",
        server_code="mcp.gone",
    )
    added = await service.add_mcp(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="9",
        server_code="mcp.back",
    )

    assert removed["changed"] is True and added["changed"] is True
    assert [name for name, _ in repository.exclusion_calls] == [
        "exclude_default_mcp",
        "unexclude_default_mcp",
    ]
    assert runtime.projections == 2
    assert runtime.scopes == [
        ProjectionScope(mcp=True, released_mcp=frozenset({"mcp.gone"})),
        ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.back"})),
    ]

    with pytest.raises(
        SkillSetControlPlaneConflictError, match="SYSTEM_DEFAULT_IMMUTABLE"
    ):
        await service.add_mcp(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="9",
            server_code="mcp.never-member",
        )


@pytest.mark.asyncio
async def test_unexcluding_default_mcp_rejects_an_incompatible_endpoint_before_write():
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

    repository = _DefaultTargetRepository(excluded_codes={"mcp.back"})
    service = _default_wire_service(
        repository, mcp_center=_IntranetOnlyCenter(allowed=True)
    )

    with pytest.raises(McpEndpointUnavailableError):
        await service.add_mcp(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="9",
            server_code="mcp.back",
        )

    assert repository.exclusion_calls == []


@pytest.mark.asyncio
async def test_unexcluding_a_default_mcp_still_requires_marketplace_permission():
    repository = _DefaultTargetRepository(excluded_codes={"mcp.back"})
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=False),
        mcp_auth=_McpAuth(allowed=False),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    with pytest.raises(McpPermissionDeniedError):
        await service.add_mcp(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="9",
            server_code="mcp.back",
        )
    assert repository.exclusion_calls == []

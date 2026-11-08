from __future__ import annotations
import importlib
import inspect
import json
import pathlib
from unittest.mock import MagicMock
import pytest
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
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

def test_skill_set_acl_denial_is_adjudicated_at_both_gates():
    """Two gates at one bar, and which one answers depends on the surface.

    On ``/openapi/v1`` all nineteen operations carry ``Check(MEMBER)``, so
    ``bot_access`` refuses before the handler runs and answers
    ``BotAccessRefusedError`` — **404 Not found, byte-identical to a bot that
    does not exist.** That is caller-visible and intended: a 403 tells a caller
    the bot exists and they may not reach it, and the seam's masked-404 contract
    is that authorization and existence must be indistinguishable.

    The service's own ``SkillSetAccessDeniedError`` (403, code 403201) is
    **not** gone, because ``/api/skillsets`` reaches this service on four routes
    that carry no gate of their own. There it is still the only refusal, and it
    is still a 403 — that surface was never behind the masked-404 contract and
    this feature does not change it. See ``_bot`` for the route list, and
    ``test_the_control_plane_check_the_legacy_surface_relies_on_still_exists``.

    An earlier revision of this test said the denial had *moved*. It had not; it
    had been deleted, which is what left ``/api/skillsets`` open.
    """
    from agentclaw.community.adapters.http.openapi_v1.authorization import (
        AUTHORIZATION,
        Check,
    )
    from agentclaw.community.core.bot_collaborator.models import PermissionLevel

    adjudicated = {
        key: rule
        for key, rule in AUTHORIZATION.items()
        if isinstance(rule, Check)
        and ("/skill-sets" in key[1] or key[1].endswith("/mcps"))
    }
    assert adjudicated, "the skill-set operations are no longer adjudicated"
    assert all(rule.level is PermissionLevel.MEMBER for rule in adjudicated.values()), (
        "the bar moved off MEMBER, which can_manage_bot enforced before the seam"
    )


def test_the_control_plane_check_the_legacy_surface_relies_on_still_exists():
    """``can_manage_bot`` must stay while a surface with no gate reaches here.

    The nineteen ``/openapi/v1`` rows are ``Check(MEMBER)`` and ``bot_access``
    adjudicates them first, so on that surface this call is a redundant second
    gate. It is not redundant on the other one:
    ``adapters/http/skill_center/skillsets.py`` is mounted at ``/api/skillsets``,
    governed by no row in ``AUTHORIZATION``, and four of its routes carry no
    ``CollaboratorPermissionInterceptor``. All four take ``entity_id`` and
    ``bot_id`` as caller-supplied query parameters, so deleting this call lets an
    authenticated stranger read — and on the ``PUT``, write — another owner's
    SkillSet.

    That deletion was made and shipped to the PR before a review caught it. This
    test is what makes the same mistake fail here instead of there: it asserts
    the call exists, that the bar is MEMBER, and that the four legacy routes are
    still ungated (if someone gives them an interceptor, this test should be
    revisited — not deleted quietly).
    """
    source = inspect.getsource(SkillSetManagementService._bot)
    assert "can_manage_bot" in source, (
        "_bot no longer adjudicates the caller. The /openapi/v1 rows are fine — "
        "the seam adjudicates them — but /api/skillsets reaches this service "
        "with no route-level gate at all, and is now open to any authenticated "
        "caller who can name an entity_id and a bot_id"
    )

    hook = importlib.import_module(
        "agentclaw.community.core.skill_center.authorization_hook"
    )
    hook_source = inspect.getsource(
        hook.CollaboratorBotCapabilityAuthorizationHook.can_manage_bot
    )
    assert "PermissionLevel.MEMBER" in hook_source, (
        "the hook no longer checks at MEMBER, which is the bar the nineteen "
        "migrated rows were derived from; re-derive before changing it"
    )

    legacy = pathlib.Path(
        "src/agentclaw/community/adapters/http/skill_center/skillsets.py"
    )
    if not legacy.exists():  # pragma: no cover - depends on the runner's cwd
        pytest.skip("legacy skillsets adapter not reachable from this working dir")
    text = legacy.read_text()
    text += "\n" + "\n".join(p.read_text() for p in legacy.parent.glob("skillsets_*_routes.py"))
    from agentclaw.community.adapters.http.skill_center.skillsets import router
    assert router.routes and all(route.path.startswith("/api/skillsets") for route in router.routes), (
        "the legacy skillsets router moved; if it now lives under /openapi/v1 "
        "it is governed by AUTHORIZATION and this reasoning has to be redone"
    )

    routes = _ungated_control_plane_routes(text)
    assert routes, (
        "no ungated /api/skillsets route reaches the control plane any more. "
        "If every one of them gained a CollaboratorPermissionInterceptor, this "
        "check may finally be deletable — verify that deliberately rather than "
        "assuming it, and update _bot's comment with what you found"
    )


def test_the_service_keeps_writing_its_own_audit_row_after_the_seam_took_over():
    """``_audit`` survives the migration, and deliberately so.

    The plan expected this write to be deleted once ``bot_access`` began
    auditing these operations, so that one mutation left exactly one row.
    Reading both writers side by side shows that would drop rows rather than
    de-duplicate them:

    * The seam audits **only non-owner** mutations — ``bot_access`` guards its
      write with ``level < PermissionLevel.OWNER``. A bot owner editing their
      own skill sets is audited here today and would be audited nowhere at all
      afterwards.
    * The two rows do not carry the same thing. The seam's ``detail`` is
      ``{"route": ..., "method": ...}``, which is what the *transport* knows;
      this one is ``{"action": "skill_set_create"}``, a domain name no route
      template encodes.

    So the overlap is a second row for a non-owner on four operations, and the
    alternative is a coverage hole. This write was never the authorization
    check — it runs *after* the mutation and was never consulted to permit one
    — so consolidating the check did not make it redundant.
    """
    rows: list[dict] = []

    class _RecordingAudit:
        def insert(self, data) -> None:
            rows.append(data)

    service = SkillSetManagementService(
        repository=_CreateRepository(),
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_RecordingAudit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    # The owner acting on their own bot: the case the seam does not audit.
    service.create_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        name="set",
        description=None,
    )

    assert len(rows) == 1, "the owner's own mutation stopped being audited"
    assert rows[0]["bot_id"] == "bot-1"
    assert rows[0]["owner_id"] == "true-owner"
    assert rows[0]["operator_id"] == "true-owner"
    assert json.loads(rows[0]["detail"]) == {"action": "skill_set_create"}, (
        "the semantic action name is what this row adds over the seam's "
        "route-and-method one; losing it makes the two rows redundant"
    )


def test_resources_forwards_resolved_bot_owner_to_owner_scoped_set_listing():
    repository = _ResourceRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=_ResourceLegacyFactory(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    assert (
        service.list_resources(
            bot_id="bot-1", owner_id="true-owner", user_id="true-owner"
        )
        == []
    )
    assert repository.list_set_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "engine_type": "openclaw",
            "default_engine_types": ("openclaw",),
        }
    ]


def test_list_sets_uses_aicoding_default_then_claude_code_fallback_for_coding_image():
    repository = _ResourceRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_AicodingImageBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=_ResourceLegacyFactory(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    assert (
        service.list_sets(bot_id="bot-1", owner_id="true-owner", user_id="true-owner")
        == []
    )
    assert repository.list_set_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "engine_type": "claude_code",
            "default_engine_types": ("aicoding", "claude_code"),
        }
    ]


def test_update_set_uses_runtime_default_candidates_for_coding_image():
    repository = _Repository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_AicodingImageBots(),
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

    service.update_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="default-id",
        name=None,
        description="ignored by the repository fake",
    )

    assert repository.update_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "set_id": "default-id",
            "name": None,
            "description": "ignored by the repository fake",
            "engine_type": "claude_code",
            "default_engine_types": ("aicoding", "claude_code"),
        }
    ]


def test_resources_reads_global_default_mcp_projection_for_collaborator_owner_scope():
    repository = _DefaultResourceRepository()
    legacy = _ResourceLegacyFactory()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=legacy,
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    result = service.list_resources(
        bot_id="bot-1", owner_id="true-owner", user_id="collaborator"
    )

    assert result[0]["mcps"] == [{"server_code": "legacy-default-mcp"}]
    # See above: the collaborator adjudication moved to the seam, so there
    # is no hook call left to observe here.
    assert repository.list_mcp_calls == []
    assert legacy.service.default_mcp_calls == [
        {
            "skill_set_id": "global-default",
            "user_id": "true-owner",
            "bot_id": "bot-1",
            "engine_type": "openclaw",
        }
    ]


def test_resources_enriches_only_default_mcp_display_from_agentpass():
    class _CustomDisplayResourceRepository(_MixedResourceRepository):
        def list_mcps(self, **kwargs):
            self.list_mcp_calls.append(kwargs)
            return [{
                "server_code": "ordinary-set-mcp",
                "name": "自定义快照名称",
                "description": "自定义快照描述",
            }]

    repository = _CustomDisplayResourceRepository()
    legacy = _ResourceLegacyFactory()
    passport = _ResourcePassport({
        "mcps": [
            {
                "mcp_code": "legacy-default-mcp",
                "mcp_name": "默认 MCP 中文名称",
                "mcp_desc": "默认 MCP 中文描述",
            },
            {
                "mcp_code": "ordinary-set-mcp",
                "mcp_name": "不应覆盖自定义项",
                "mcp_desc": "不应覆盖自定义描述",
            },
        ],
        "clis": [],
    })
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=legacy,
        passport=passport,
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    result = service.list_resources(
        bot_id="bot-1", owner_id="true-owner", user_id="true-owner"
    )

    assert result[0]["mcps"] == [{
        "server_code": "legacy-default-mcp",
        "name": "默认 MCP 中文名称",
        "description": "默认 MCP 中文描述",
    }]
    assert result[1]["mcps"] == [{
        "server_code": "ordinary-set-mcp",
        "name": "自定义快照名称",
        "description": "自定义快照描述",
    }]
    assert passport.calls == [("bot-1", "true-owner")]


def test_list_mcps_reads_global_default_projection_for_collaborator_owner_scope():
    repository = _DefaultResourceRepository()
    legacy = _ResourceLegacyFactory()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=legacy,
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    result = service.list_mcps(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="collaborator",
        set_id="global-default",
    )

    assert result == [{"server_code": "legacy-default-mcp"}]
    assert repository.list_mcp_calls == []
    assert legacy.service.default_mcp_calls == [
        {
            "skill_set_id": "global-default",
            "user_id": "true-owner",
            "bot_id": "bot-1",
            "engine_type": "openclaw",
        }
    ]


def test_list_mcps_keeps_ordinary_membership_on_canonical_repository_path():
    repository = _MixedResourceRepository()
    legacy = _ResourceLegacyFactory()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=legacy,
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    result = service.list_mcps(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="ordinary-set",
    )

    assert result == [{"server_code": "ordinary-set-mcp"}]
    assert legacy.calls == []
    assert repository.list_mcp_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "set_id": "ordinary-set",
            "engine_type": "openclaw",
            "default_engine_types": ("openclaw",),
        }
    ]


def test_default_mcp_permissions_remain_scoped_to_persisted_membership():
    repository = _DefaultResourceRepository()
    legacy = _ResourceLegacyFactory()
    mcp_center = _McpCenter(allowed=True)
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=legacy,
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=mcp_center,
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    result = service.list_mcp_permissions(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="collaborator",
        set_id="global-default",
    )

    assert result == [
        {
            "server_code": "visible-default-mcp",
            "has_permission": True,
            "access_level": "LOCAL",
        }
    ]
    assert mcp_center.calls == [("collaborator", "visible-default-mcp")]
    assert legacy.calls == []


def test_default_mcp_permission_request_does_not_expand_platform_defaults():
    repository = _DefaultResourceRepository()
    legacy = _ResourceLegacyFactory()
    mcp_auth = _McpAuth(allowed=True)
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=legacy,
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=mcp_auth,
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    result = service.request_mcp_permissions(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="collaborator",
        set_id="global-default",
        reason="needed",
    )

    assert result == [
        {
            "server_code": "visible-default-mcp",
            "success": True,
            "process_url": None,
            "error": None,
        }
    ]
    assert mcp_auth.apply_calls == [
        {
            "staff_no": "collaborator",
            "service_code": "visible-default-mcp",
            "tool_list": [],
            "is_public": True,
            "reason": "needed",
        }
    ]
    assert legacy.calls == []


def test_resources_keeps_ordinary_mcp_membership_on_canonical_repository_path():
    repository = _MixedResourceRepository()
    legacy = _ResourceLegacyFactory()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=legacy,
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

    result = service.list_resources(
        bot_id="bot-1", owner_id="true-owner", user_id="true-owner"
    )

    assert result[0]["mcps"] == [{"server_code": "legacy-default-mcp"}]
    assert result[1]["mcps"] == [{"server_code": "ordinary-set-mcp"}]
    assert repository.list_mcp_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "set_id": "ordinary-set",
            "engine_type": "openclaw",
            "default_engine_types": ("openclaw",),
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("bots", [_PlainClaudeCodeBots(), _LiteralAicodingBots()])
async def test_existing_coding_bot_can_activate_skill_set(bots) -> None:
    repository = _Repository()
    runtime = _SuccessfulRuntime()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=bots,
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
        user_id="true-owner",
        set_id="set-1",
    )

    assert result["is_active"] is True
    assert repository.set_active_calls[0]["active"] is True


@pytest.mark.asyncio
async def test_existing_claude_code_skill_set_deactivate_without_mcp_uses_skill_scope():
    repository = _Repository()
    runtime = _Runtime(
        snapshots=[_Runtime._skill_mappings(), ()],
        fail_first=False,
    )
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_PlainClaudeCodeBots(),
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
        user_id="true-owner",
        set_id="set-1",
    )

    assert repository.set_active_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "set_id": "set-1",
            "active": False,
            "engine_type": "claude_code",
            "default_engine_types": ("claude_code",),
        }
    ]
    assert runtime.reconcile_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "retired_mappings": _Runtime._skill_mappings(),
            "scope": ProjectionScope(skills=True),
        }
    ]

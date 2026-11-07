"""Bulk Apply through the existing authorized management service seam."""

from dataclasses import replace

import pytest

from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from tests.community.repository.skill_center.test_capability_desired_state_uow import (
    _Database,
)
from tests.community.core.skill_center._test_skill_set_management_service_support import (
    _skill_service,
    _Runtime,
)


@pytest.mark.asyncio
async def test_bulk_apply_service_reads_and_replaces_without_runtime_for_platform():
    service = _skill_service(CapabilityDesiredStateRepository(_Database()), _Runtime())
    before = service.read_apply_snapshot(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )
    result = await service.replace_apply_skill_sets(
        scope=before.scope,
        actor_id="true-owner",
        sets=(ResolvedSkillSetWrite("expert"),),
        remove_names=(),
        default_change=DefaultExclusionChange(),
        platform_default_codes=before.platform_default_codes,
        project=False,
    )
    assert result.created == ("expert",) and result.changed
    assert result.runtime_projection["status"] == "SKIPPED"
    after = service.read_apply_snapshot(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )
    assert tuple(item.name for item in after.sets) == ("expert",)
    assert after.sets[0].is_active


@pytest.mark.asyncio
async def test_bulk_apply_service_refuses_stale_trusted_scope_before_writes():
    service = _skill_service(CapabilityDesiredStateRepository(_Database()), _Runtime())
    before = service.read_apply_snapshot(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="CAPABILITY_SCOPE_CHANGED"
    ):
        await service.replace_apply_skill_sets(
            scope=replace(before.scope, engine_type="hermes"),
            actor_id="true-owner",
            sets=(ResolvedSkillSetWrite("expert"),),
            remove_names=(),
            default_change=DefaultExclusionChange(),
            platform_default_codes=frozenset(),
            project=False,
        )
    assert (
        service.list_sets(bot_id="bot-1", owner_id="true-owner", user_id="true-owner")
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner_endpoint_env", "entity_endpoint_env", "admitted"),
    [("PROD", "PRE", True), ("PRE", "PROD", False)],
)
async def test_bulk_apply_mcp_admission_uses_current_owner_defaults_and_keeps_entity_scope(
    owner_endpoint_env: str, entity_endpoint_env: str, admitted: bool
):
    from unittest.mock import MagicMock

    from agentclaw.community.core.mcp.services.config_service import MCPConfigService
    from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
    from agentclaw.community.core.repository.implementations.bot.bot_mcp_config import (
        BotMCPConfigRepository,
    )
    from agentclaw.community.core.repository.implementations.bot.user_mcp_config import (
        UserMCPConfigRepository,
    )
    from agentclaw.community.core.skill_center.errors import McpEndpointUnavailableError
    from agentclaw.community.core.skill_center.services.skill_set_management_service import (
        SkillSetManagementService,
    )
    from agentclaw.community.di.config import McpRuntimeCredentialsConfig
    from agentclaw.community.utils.env_utils import get_current_env
    from tests.community.core.skill_center._test_skill_set_management_service_support import (
        _Audit,
        _Authorization,
        _McpAuth,
    )

    db = _Database()
    bots = BotRepository(db)
    bots.insert(
        {
            "bot_id": "bot-1", "entity_id": "entity-1", "owner_id": "true-owner",
            "creator_id": "true-owner", "entity_type": "staff",
            "active_engine": "openclaw", "status": "ACTIVE",
        }
    )
    user_configs = UserMCPConfigRepository(db)
    for user_id, endpoint_env in (
        ("true-owner", owner_endpoint_env), ("entity-1", entity_endpoint_env),
    ):
        user_configs.create(
            {
                "user_id": user_id, "server_code": "mcp.owner-default",
                "env": get_current_env(),
                "extra_config": {
                    "endpoint_env": endpoint_env,
                    "transport_protocol": "STREAMABLE_HTTP",
                },
            }
        )
    center = MagicMock()
    center.get_mcp_detail.return_value = {
        "serverCode": "mcp.owner-default", "runMode": "REMOTE",
        "endpoints": [
            {
                "networkType": "OFFICE", "env": "PROD",
                "transportProtocol": "STREAMABLE_HTTP",
                "url": "https://mcp.example.test/prod",
            },
            {
                "networkType": "INTRANET", "env": "PRE",
                "transportProtocol": "STREAMABLE_HTTP",
                "url": "https://mcp.example.test/pre",
            },
        ],
    }
    config = MCPConfigService(
        user_mcp_config_repo=user_configs,
        bot_mcp_config_repo=BotMCPConfigRepository(db), mcp_center=center,
        bot_repo=bots, capability_reader=MagicMock(),
        mcp_runtime_credentials=McpRuntimeCredentialsConfig(), secret_resolver=MagicMock(),
    )
    service = SkillSetManagementService(
        repository=CapabilityDesiredStateRepository(db), bot_repo=bots, runtime=_Runtime(),
        legacy_factory=object(), passport=object(), authorization=_Authorization(),
        audit_log_repo=_Audit(), mcp_center=center, mcp_auth=_McpAuth(allowed=True),
        mcp_config=config, ext_info_provider=lambda _bot_id: None, recovery=MagicMock(),
    )
    before = service.read_apply_snapshot(
        bot_id="bot-1", owner_id="true-owner", actor_id="collaborator"
    )
    assert before.scope.owner_id == "entity-1"
    replacement = dict(
        scope=before.scope, actor_id="collaborator",
        sets=(ResolvedSkillSetWrite("expert", mcp_codes=("mcp.owner-default",)),),
        remove_names=(), default_change=DefaultExclusionChange(),
        platform_default_codes=before.platform_default_codes, project=False,
    )
    if admitted:
        result = await service.replace_apply_skill_sets(**replacement)
        assert result.created == ("expert",)
        after = service.read_apply_snapshot(
            bot_id="bot-1", owner_id="entity-1", actor_id="collaborator"
        )
        assert after.scope == before.scope
        assert after.sets[0].mcp_codes == ("mcp.owner-default",)
        assert after.installed_mcp_codes == frozenset({"mcp.owner-default"})
    else:
        with pytest.raises(McpEndpointUnavailableError):
            await service.replace_apply_skill_sets(**replacement)
        assert service.read_apply_snapshot(
            bot_id="bot-1", owner_id="entity-1", actor_id="collaborator"
        ) == before

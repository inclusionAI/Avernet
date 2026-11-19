"""Default restoration flows through the real OpenClaw device projector."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.apply_engine.capabilities import resolve_capabilities
from agentclaw.community.core.apply_engine.desired_state import (
    CapabilityDeclaration,
    Declaration,
    DefaultSkillSetRef,
    DesiredCapabilities,
    DesiredState,
)
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.devices.services.mcp_device_payload import (
    convert_to_device_format,
)
from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)
from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
    PerDomainRuntimeProjection,
)
from agentclaw.community.plugin_api.models import BotModel
from tests.community.core.skill_center.services.test_collect_bot_active_mcps_union import (
    _service,
)
from ._mcp_config_support import McpWorld


@pytest.mark.asyncio
async def test_denied_default_reaches_device_configuration_and_allowlist(
    tmp_path, monkeypatch
):
    # The shared in-memory SQLite fixture has one connection, so serialize fan-out.
    monkeypatch.setattr(
        "agentclaw.community.core.mcp.services.sync_service._DESIRED_STATE_DETAIL_CONCURRENCY",
        1,
    )
    world = McpWorld()
    with world.db.transactional_orm_session() as session:
        bot = session.get(BotModel, world.bot["id"])
        bot.active_engine = "openclaw"
        group = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="openclaw",
            is_default=True,
            is_active=True,
            env="dev",
        )
        session.add(group)
        session.flush()
        session.add(
            SkillSetMCPServer(
                skill_set_id=group.id,
                server_code="mcp.denied",
                name="Denied",
                env="dev",
            )
        )
        session.add(
            DefaultSkillsetMcpExclusion(
                user_id="entity-1",
                bot_id="bot-1",
                skill_set_id=group.id,
                server_code="mcp.denied",
            )
        )
    world.bot = world.bots.get_by_id_and_owner("bot-1", "owner")
    world.context = replace(
        world.context,
        engine_type="openclaw",
        bot=world.bot,
        capabilities=resolve_capabilities(
            active_engine="openclaw", bot_type="personal", is_teclaw=lambda _: False
        ),
    )
    world.center.check_mcp_permission_detail = lambda *_: {
        "has_permission": False,
        "access_level": "PRIVATE",
    }
    service = _service(world.db, tmp_path, ext_info_provider=lambda _: {})
    service.bot_id, service.entity_id = "bot-1", "entity-1"
    service.user_id, service._device_owner_id = "owner", "owner"
    service.engine_type = service.runtime_engine_type = "openclaw"
    service._reader, service._bot_repo, service.mcp_center = (
        world.capabilities,
        world.bots,
        world.center,
    )
    service._resolver = MagicMock()
    service._resolver.resolve_for_bot.return_value = SimpleNamespace(bot_id="bot-1")
    device = MagicMock()
    device.sync_all_mcp_servers.return_value = True
    device.sync_single_mcp.return_value = True
    service._device_sync_dispatcher = MagicMock()
    service._device_sync_dispatcher.dispatch.return_value = device
    service._mcp_sync_service = MCPSyncService(
        mcp_provider_factory=MagicMock(),
        mcp_center=world.center,
        user_mcp_config_repo=MagicMock(),
        passport_update=MagicMock(),
        mcp_config_service=world.config,
        bot_repository=world.bots,
        caller_identity_repository=MagicMock(),
        resolver_provider=MagicMock(),
        device_sync_dispatcher_provider=MagicMock(),
    )
    factory = MagicMock()
    factory.create.return_value = service
    passport = MagicMock()
    passport.query_passport_clis.return_value = []
    passport.query_agent_passport.return_value = {"mcps": [], "clis": []}
    identities = MagicMock()
    identities.list_draft_call_types.return_value = {}
    skill_delivery = MagicMock()
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        RuntimeProjectionResult,
    )

    skill_delivery.deliver = AsyncMock(return_value=RuntimeProjectionResult.converged())
    projector = BotRuntimeProjector(
        factory=factory,
        bot_repo=world.bots,
        repository=MagicMock(),
        reader=world.capabilities,
        registry=SimpleNamespace(
            for_engine=lambda _: PerDomainRuntimeProjection(
                skill_delivery=skill_delivery
            )
        ),
        passport=passport,
        caller_identity_repo=identities,
    )
    world.sets._inner._flow._runtime = projector
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    before = world.sets.read_apply_snapshot(
        bot_id="bot-1", owner_id="entity-1", actor_id="owner"
    )
    assert "mcp.denied" not in before.installed_mcp_codes
    result = await world.apply_state(
        DesiredState(capabilities=CapabilityDeclaration.from_target(target))
    )
    assert result.status is ApplyStatus.SUCCEEDED
    assert all(not e.note for c in result.categories for e in c.entries), [
        e.note for c in result.categories for e in c.entries
    ]
    delivered = next(
        call
        for call in device.sync_single_mcp.call_args_list
        if call.args[0].get("serverCode") == "mcp.denied"
    )
    payload = convert_to_device_format(delivered.args[0], **delivered.kwargs)
    assert payload.url == "https://center.example/prod/mcp.denied"
    assert payload.transport == "http"
    assert "mcp.denied" in {
        entry["server_code"] for entry in device.sync_all_mcp_servers.call_args.args[0]
    }
    first_count = device.sync_single_mcp.call_count
    repeated = await world.apply_state(
        DesiredState(capabilities=CapabilityDeclaration.from_target(target))
    )
    assert repeated.status is ApplyStatus.SUCCEEDED
    assert any(
        call.args[0].get("serverCode") == "mcp.denied"
        for call in device.sync_single_mcp.call_args_list[first_count:]
    )

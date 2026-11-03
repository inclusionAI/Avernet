"""Keep DB/Passport entity addresses separate from the device's current Owner."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import BotRuntimeProjector
from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
    PerDomainRuntimeProjection,
)
from tests.community.core.skill_center.services.test_collect_bot_active_mcps_union import (
    _Database,
    _ENGINE,
    _service,
)


def _seed(db):
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="tools", user_id="owner", bolt_id="bot",
                             engine_type=_ENGINE, is_active=True, env="dev")
        session.add(skill_set)
        session.flush()
        session.add(SkillSetMCPServer(skill_set_id=skill_set.id, server_code="mcp.weather",
                                      name="Weather", env="dev"))


@pytest.mark.asyncio
async def test_real_installations_and_collector_reach_device_after_handoff(tmp_path):
    db = _Database()
    _seed(db)
    service = _service(db, tmp_path, ext_info_provider=lambda _bot: None)
    bot = {"id": 1, "bot_id": "bot", "entity_id": "owner", "owner_id": "successor",
           "active_engine": _ENGINE, "entity_type": "staff", "bot_type": "service",
           "status": "ACTIVE", "env": "dev"}
    service._device_owner_id = "successor"
    service._resolver = Mock()
    # Fail if resource identity is accidentally used as a personnel identity.
    def resolve(bot_id, actor):
        assert (bot_id, actor) == ("bot", "successor")
        return SimpleNamespace(bot_id=bot_id)
    service._resolver.resolve_for_bot.side_effect = resolve
    device_sync = Mock()
    device_sync.sync_all_mcp_servers.return_value = True
    service._device_sync_dispatcher = Mock()
    service._device_sync_dispatcher.dispatch.return_value = device_sync
    service.mcp_center.get_mcp_detail.return_value = {"server_code": "mcp.weather"}
    service._mcp_sync_service = Mock()
    service._mcp_sync_service.sync_mcp_details_for_bot = AsyncMock(return_value={"success": True})
    factory = Mock()
    factory.create.return_value = service
    bots = Mock()
    bots.get_by_id_and_owner.return_value = bot
    bots.get_by_id_and_entity.return_value = bot
    service._bot_repo = bots
    projection = PerDomainRuntimeProjection(skill_delivery=Mock())
    passport = Mock()
    passport.query_passport_clis.return_value = []
    passport.query_agent_passport.return_value = {"mcps": [], "clis": []}
    identities = Mock()
    identities.list_draft_call_types.return_value = {}
    projector = BotRuntimeProjector(
        factory=factory, bot_repo=bots, repository=Mock(), reader=service._reader,
        registry=SimpleNamespace(for_engine=lambda _engine: projection),
        passport=passport, caller_identity_repo=identities,
    )
    result = await projector.project(
        bot_id="bot", owner_id="successor",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.weather"})),
    )
    assert result.status is RuntimeProjectionStatus.CONVERGED
    service._resolver.resolve_for_bot.assert_called_once_with("bot", "successor")
    delivered = service._mcp_sync_service.sync_mcp_details_for_bot.await_args.kwargs
    assert delivered["entity_id"] == "owner"
    assert delivered["device_sync"] is device_sync
    assert delivered["mcp_entries"] == [{"server_code": "mcp.weather"}]
    device_sync.sync_all_mcp_servers.assert_called_once_with([{"server_code": "mcp.weather"}])
    passport.query_passport_clis.assert_called_once_with("bot", "owner")
    passport.query_agent_passport.assert_called_once_with("bot", "owner")
    assert passport.update_passport.call_args.kwargs["user_id"] == "owner"


def test_current_owner_snapshot_keeps_original_default_exclusions(tmp_path):
    db = _Database()
    with db.transactional_orm_session() as session:
        default = SkillSet(name="defaults", user_id="owner", bolt_id="bot",
                           engine_type=_ENGINE, is_default=True, is_active=True, env="dev")
        session.add(default)
        session.flush()
        session.add_all([
            SkillSetMCPServer(skill_set_id=default.id, server_code="mcp.excluded",
                             name="Excluded", env="dev"),
            DefaultSkillsetMcpExclusion(user_id="owner", bot_id="bot",
                                       skill_set_id=default.id, server_code="mcp.excluded"),
        ])
    service = _service(db, tmp_path)
    snapshot = service._reader.active_capabilities(
        bot_id="bot", owner_id="successor",
        bot={"id": 1, "bot_id": "bot", "owner_id": "successor", "entity_id": "owner",
             "active_engine": _ENGINE, "env": "dev"},
    )
    assert snapshot.owner_id == "owner"
    assert service.collect_bot_active_mcps(
        entity_id="owner", bot_id="bot", user_id="successor", capability_snapshot=snapshot
    ) == []

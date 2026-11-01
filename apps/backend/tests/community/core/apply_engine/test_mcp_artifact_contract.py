"""Persisted MCP configuration in the first artifact and whole-artifact updates."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.apply_engine.delivery import TeclawPlatformDelivery
from agentclaw.community.core.apply_engine.redeliver import TeclawRedeliver
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_management.services.teclaw_provision_service import TeclawProvisionService
from agentclaw.community.core.models.mcp import BotMCPConfig, UserMCPConfig
from agentclaw.community.core.repository.implementations.bot.scoped_mcp_config import ScopedMCPConfigRepository
from agentclaw.community.core.service_bot.services.deploy.teclaw_compose_producer import TeclawComposeProducer
from tests.community.core.config_compose.test_collector import _collector
from tests.community.core.config_compose.test_config_composer import _composer, _FakeCollector
from tests.community.core.devices.test_teclaw_device_sync import _make_service

from ._mcp_config_support import McpWorld


def _artifact_composer(world):
    service = SimpleNamespace(
        mcp_center=world.center,
        collect_bot_active_mcps=lambda **kw: [
            {"server_code": code}
            for code in world.direct.list_installed_mcps(bot_id="bot-1", owner_id="owner", actor_id="owner")
        ],
    )
    mcp_collector = _collector(skill_set_service=service, mcp_config_service=world.config)

    class Inputs(_FakeCollector):
        def mcps(self, req):
            return mcp_collector.mcps(req)

    return _composer(Inputs())


@pytest.mark.asyncio
async def test_first_artifact_and_redeliver_use_preserved_cleared_and_replaced_configuration():
    world = McpWorld(platform=True)
    with world.db.transactional_orm_session() as session:
        session.add(UserMCPConfig(
            user_id="owner", server_code="mcp.test", env="dev",
            extra_config='{"headers":{"X-Inherited":"user"},"endpoint_env":"PROD"}',
        ))
    ScopedMCPConfigRepository(world.db).replace(
        user_id="entity-1", server_code="mcp.test", headers={},
        bot_headers={"bot-1": {"X-Bot": "saved"}}, owned_bot_ids={"bot-1"},
        endpoint_env="PROD", transport_protocol=None,
        bot_urls={"bot-1": "https://saved.example/mcp"},
    )
    assert (await world.apply_manifest([{"server_code": "mcp.test"}])).status is ApplyStatus.SUCCEEDED
    with world.db.transactional_orm_session() as session:
        session.add(BotMCPConfig(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.dormant", env="dev",
            config='{"url":"https://dormant.example/mcp"}',
        ))
    composer = _artifact_composer(world)
    ext = MagicMock()
    ext.fetch.return_value = {}
    producer = TeclawComposeProducer(composer, ext)
    producer_router = MagicMock()
    producer_router.resolve.return_value = producer
    baas = MagicMock()
    baas.create_teclaw_bot.return_value = {"bot_uuid": "teclaw-bot", "publish_id": 9}
    binding = MagicMock()
    binding.insert_binding.return_value = 77
    provisioner = TeclawProvisionService(
        baas_service=baas, deploy_artifact_producer_router=producer_router,
        device_binding_repo=binding, task_queue_service=MagicMock(), bot_repository=MagicMock(),
        teclaw_template_uuid="test-template",
    )
    provisioner.provision(bot=world.bot, owner_id="owner")
    artifact = baas.create_teclaw_bot.call_args.kwargs["config_artifact"]
    servers = artifact["mcp"]["servers"]
    assert [entry["server_code"] for entry in servers] == ["mcp.test"]
    assert servers[0]["endpoint"] == "https://saved.example/mcp"
    assert servers[0]["headers"]["X-Bot"] == "saved"
    assert servers[0]["headers"]["X-Inherited"] == "user"

    sync, io = _make_service(
        composer_provider=lambda: composer, bot_id="bot-1", owner_id="owner", user_id="owner", entity_id="entity-1",
    )
    closing = TeclawPlatformDelivery(
        ports=lambda: None,
        redeliver=TeclawRedeliver(resolve=lambda *args: object(), dispatch=lambda device: sync, not_bound=LookupError),
    )
    for config in ({}, {"headers": {"X-Bot": "new"}}):
        assert (await world.apply_manifest([{"server_code": "mcp.test", "config": config}])).status is ApplyStatus.SUCCEEDED
        assert await closing.finish(world.context, None) is None
        delivered = io["http_client"].post.call_args.kwargs["json"]["bot_config"]["mcp"]["servers"]
        assert [entry["server_code"] for entry in delivered] == ["mcp.test"]
        assert delivered[0]["endpoint"] == "https://center.example/prod/mcp.test"
        assert delivered[0]["headers"]["X-Inherited"] == "user"
        assert delivered[0]["headers"].get("X-Bot") == config.get("headers", {}).get("X-Bot")

"""Persisted MCP configuration in the first artifact and whole-artifact updates."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.apply_engine.delivery import TeclawPlatformDelivery
from agentclaw.community.core.apply_engine.redeliver import TeclawRedeliver
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_management.services.teclaw_provision_service import TeclawProvisionService
from agentclaw.community.core.models.mcp import BotMCPConfig, UserMCPConfig
from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.repository.implementations.bot.scoped_mcp_config import ScopedMCPConfigRepository
from agentclaw.community.core.repository.implementations.skill_center.category import SkillCategoryRepository
from agentclaw.community.core.repository.implementations.skill_center.skill import SkillSetRepository
from agentclaw.community.core.skill_center.services.skill_cache import MarketCache
from agentclaw.community.core.skill_center.services.skill_service import SkillService
from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.core.service_bot.services.deploy.teclaw_compose_producer import TeclawComposeProducer
from tests.community.core.config_compose.test_collector import _collector
from tests.community.core.config_compose.test_config_composer import _composer, _FakeCollector
from tests.community.core.devices.test_teclaw_device_sync import _make_service

from ._mcp_config_support import McpWorld


def _artifact_composer(world, tmp_path):
    def unrelated_file_io(*_args, **_kwargs):
        pytest.fail("Collecting MCP capabilities must not fetch or write Skill files")

    # All domain aggregation is real. Only unrelated external cache, content
    # sync and device I/O boundaries are stood in for at assembly time.
    repo_sync = MagicMock()
    skill_service = SkillService(
        world.skills, repo_sync, SkillCategoryRepository(world.db), MarketCache(MagicMock()),
        device_fs_factory=unrelated_file_io, git_sync_service_factory=unrelated_file_io,
        active_dir=tmp_path / "active", repo_dir=tmp_path / "repo",
        local_dir=tmp_path / "local", global_repo_dir=tmp_path / "shared",
    )
    service = SkillSetService(
        world.skills, SkillSetRepository(world.db), world.center, world.config,
        skill_service, world.bots, bot_id="bot-1", engine_type="teclaw",
        skills_dir=tmp_path / "active", repo_dir=tmp_path / "repo", local_dir=tmp_path / "local",
        path_factory=WorkspacePathFactory(repo_sync), reader=world.capabilities,
        ext_info_provider=lambda _bot_id: {},
    )
    mcp_collector = _collector(skill_set_service=service, mcp_config_service=world.config)

    class Inputs(_FakeCollector):
        def mcps(self, req):
            return mcp_collector.mcps(req)

    return _composer(Inputs())


def _seed_other_supplies(world):
    ordinary = world.desired.create_set(
        bot_id="bot-1", owner_id="entity-1", name="ordinary", description=None, engine_type="teclaw",
    )
    world.desired.add_mcp(
        bot_id="bot-1", owner_id="entity-1", set_id=ordinary["id"], server_code="mcp.set",
        platform_default_codes=world.policy.server_codes_for(world.bot),
        name="Set member", description=None, icon=None, engine_type="teclaw",
    )
    with world.db.transactional_orm_session() as session:
        default = SkillSet(
            name="Default", user_id=None, bolt_id="", engine_type="teclaw", is_default=True, is_active=True, env="dev",
        )
        session.add(default)
        session.flush()
        session.add(SkillSetMCPServer(skill_set_id=default.id, server_code="mcp.default", name="Default member", env="dev"))
        for code in ("mcp.set", "mcp.default", "mcp.dep"):
            session.add(BotMCPConfig(
                bot_id="bot-1", owner_id="entity-1", server_code=code, env="dev", config='{"endpoint_env":"PRE"}',
            ))
    skill = world.skills.create({
        "name": "dependent", "description": "Derived MCP", "git_path": "local://dependent", "is_public": False,
        "user_id": "entity-1", "bolt_id": "bot-1", "status": "PUBLISHED", "mcp_dependencies": [{"code": "mcp.dep"}],
    })
    world.desired.install_skill(bot_id="bot-1", owner_id="entity-1", skill_id=skill["id"], engine_type="teclaw")
    assert world.direct.set_supplied_capabilities(bot_id="bot-1", owner_id="owner", actor_id="owner")[1] == (
        world.policy.server_codes_for(world.bot) | {"mcp.set", "mcp.default"}
    )


def _save_target(world, value):
    current_user = world.config.get_user_unified_config("owner", "mcp.test") or {}
    ScopedMCPConfigRepository(world.db).replace(
        user_id="owner", server_code="mcp.test", headers=current_user.get("headers") or {},
        bot_headers={"bot-1": {"X-Bot": value}}, owned_bot_ids={"bot-1"},
        endpoint_env="PROD", transport_protocol=None,
        user_url=current_user.get("url"), bot_urls={"bot-1": f"https://{value}.example/mcp"},
    )


def _save_dormant(world):
    with world.db.transactional_orm_session() as session:
        session.add(BotMCPConfig(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.dormant", env="dev",
            config='{"url":"https://dormant.example/mcp"}',
        ))


def _assert_servers(world, artifact, *, case):
    servers = {entry["server_code"]: entry for entry in artifact["mcp"]["servers"]}
    # These are the input policy and seeded domain facts, not the collector's
    # output: dropping any supplier in composition must fail this assertion.
    assert set(servers) == world.policy.server_codes_for(world.bot) | {"mcp.test", "mcp.set", "mcp.default", "mcp.dep"}
    target = servers["mcp.test"]
    assert target["endpoint"] == (
        "https://center.example/prod/mcp.test"
    )
    assert target["headers"].get("X-Bot") == ("new" if case == "replace" else None)
    assert target["headers"]["X-Inherited"] == "user"
    assert servers["mcp.set"]["endpoint"] == "https://center.example/pre/mcp.set"
    assert servers["mcp.default"]["endpoint"] == "https://center.example/pre/mcp.default"
    assert servers["mcp.dep"]["endpoint"] == "https://center.example/prod/mcp.dep"
    assert "mcp.dormant" in world.saved(), "Composition must exclude a dormant configuration without deleting it"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["omitted", "clear", "replace"])
async def test_first_artifact_and_redeliver_use_inherited_and_replaced_configuration(case, tmp_path):
    world = McpWorld(platform=True)
    with world.db.transactional_orm_session() as session:
        session.add(UserMCPConfig(
            user_id="owner", server_code="mcp.test", env="dev",
            extra_config='{"headers":{"X-Inherited":"user"},"endpoint_env":"PROD"}',
        ))
    _seed_other_supplies(world)
    _save_target(world, "saved")
    entry = {"server_code": "mcp.test"}
    if case != "omitted":
        entry["config"] = {} if case == "clear" else {"headers": {"X-Bot": "new"}}
    assert (await world.apply_manifest([entry])).status is ApplyStatus.SUCCEEDED
    _save_dormant(world)
    composer = _artifact_composer(world, tmp_path)
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
    _assert_servers(world, artifact, case=case)

    sync, io = _make_service(
        composer_provider=lambda: composer, bot_id="bot-1", owner_id="owner", user_id="owner", entity_id="entity-1",
    )
    closing = TeclawPlatformDelivery(
        ports=lambda: None,
        redeliver=TeclawRedeliver(resolve=lambda *args: object(), dispatch=lambda device: sync, not_bound=LookupError),
    )
    _save_target(world, "edited")
    assert (await world.apply_manifest([entry])).status is ApplyStatus.SUCCEEDED
    _save_dormant(world)
    assert await closing.finish(world.context, None) is None
    delivered = io["http_client"].post.call_args.kwargs["json"]["bot_config"]
    _assert_servers(world, delivered, case=case)

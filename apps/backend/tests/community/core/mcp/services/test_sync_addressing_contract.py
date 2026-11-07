"""Incremental MCP projection preserves the Bot/user identity distinction."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.repository.implementations.identity.caller_identity import CallerIdentityRepository
from tests.community.core.apply_engine._mcp_config_support import McpWorld


class RecordingDevice:
    def __init__(self):
        self.deliveries = []

    def has_mcp(self, code):
        return True

    def sync_single_mcp(self, data, **kwargs):
        self.deliveries.append(kwargs)
        return True


def unused_provider():
    raise AssertionError("A pre-resolved delivery must not request another provider")


def sync_service(world):
    return MCPSyncService(
        mcp_provider_factory=unused_provider,
        mcp_center=world.center,
        user_mcp_config_repo=world.config.user_mcp_config_repo,
        passport_update=MagicMock(),
        mcp_config_service=world.config,
        bot_repository=world.bots,
        caller_identity_repository=CallerIdentityRepository(world.db),
        resolver_provider=unused_provider,
        device_sync_dispatcher_provider=unused_provider,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_id", ["entity-1", None])
@pytest.mark.parametrize("bot_type", ["personal", "service"])
async def test_pre_resolved_delivery_uses_entity_override_and_human_user_defaults(entity_id, bot_type):
    world = McpWorld(bot_type=bot_type)
    world.config.user_mcp_config_repo.create({
        "user_id": "owner", "server_code": "mcp.test", "env": get_current_env(), "extra_config": {"headers": {"X-User": "inherited"}},
    })
    await world.apply_manifest([{"server_code": "mcp.test", "config": {"headers": {"X-Bot": "saved"}}}])
    device = RecordingDevice()
    result = await sync_service(world).sync_mcp_details_for_bot(
        user_id="owner", entity_id=entity_id, bot_id="bot-1", engine_type="moltis",
        mcp_entries=[world.center.get_mcp_detail("mcp.test")], device_sync=device,
    )
    assert result["success"] is True
    assert device.deliveries[0]["custom_headers"] == {
        "X-Bot": "saved", "X-User": "inherited", "X-TC-Bot-Id": "bot-1",
    }


class LocalConnectionEndpoint:
    def get_device_connection_v2(self, **kwargs):
        return {"engine_type": "moltis"}


def connected_sync_service(world, device, *, provider=unused_provider, passport=None):
    from types import SimpleNamespace

    from agentclaw.community.core.devices.services.conn_info_builders.arca_builder import ArcaConnInfoBuilder
    from agentclaw.community.core.devices.services.conn_info_builders.baas_builder import BaasConnInfoBuilder
    from agentclaw.community.core.devices.services.conn_info_builders.local_builder import LocalConnInfoBuilder
    from agentclaw.community.core.devices.services.conn_info_builders.teclaw_builder import TeclawConnInfoBuilder
    from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
    from agentclaw.community.core.repository.implementations.devices.device import DeviceRepository

    bindings = DeviceRepository(world.db)
    binding = bindings.insert_binding(
        entity_id="entity-1", entity_type=world.bots.get_by_id_and_owner("bot-1", "owner")["entity_type"], device_id="review-device", device_provider="local",
        env=get_current_env(), device_props={}, status="ACTIVE", apply_reason=None, applied_by="owner",
    )
    world.bots.update_by_owner("bot-1", "owner", {"binding_id": binding})
    endpoint = LocalConnectionEndpoint()
    resolver = DeviceContextResolver(
        bindings, world.bots, ArcaConnInfoBuilder(endpoint),
        BaasConnInfoBuilder(endpoint, world.bots, bindings), TeclawConnInfoBuilder(endpoint),
        LocalConnInfoBuilder(endpoint),
    )
    return MCPSyncService(
        mcp_provider_factory=provider, mcp_center=world.center,
        user_mcp_config_repo=world.config.user_mcp_config_repo, passport_update=passport or MagicMock(),
        mcp_config_service=world.config, bot_repository=world.bots,
        caller_identity_repository=CallerIdentityRepository(world.db), resolver_provider=lambda: resolver,
        device_sync_dispatcher_provider=lambda: SimpleNamespace(dispatch=lambda ctx: device),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_id", ["entity-1", None])
@pytest.mark.parametrize("bot_type", ["personal", "service"])
async def test_single_delivery_resolves_the_same_bot_override(entity_id, bot_type):
    world = McpWorld(bot_type=bot_type)
    await world.apply_manifest([{"server_code": "mcp.test", "config": {"headers": {"X-Bot": "saved"}}}])
    device = RecordingDevice()
    result = await connected_sync_service(world, device).sync_mcp_detail(
        user_id="owner", entity_id=entity_id, bot_id="bot-1", mcp_data=world.center.get_mcp_detail("mcp.test"),
    )
    assert result["success"] is True
    assert device.deliveries[0]["custom_headers"]["X-Bot"] == "saved"


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [True, False])
async def test_account_and_entity_fanout_keep_resource_and_user_scopes_separate(selected):
    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test", "config": {"headers": {"X-Bot": "saved"}}}])
    device = RecordingDevice()
    result = await connected_sync_service(world, device).sync_mcp_detail_to_all_bots(
        user_id="owner" if selected else "viewer", entity_id="owner" if selected else "entity-1",
        entity_type="staff", server_code="mcp.test",
        mcp_data=world.center.get_mcp_detail("mcp.test"), target_bot_ids=["bot-1"] if selected else None,
        target_bot_owners={"bot-1": "owner"} if selected else None,
    )
    assert result["sync_results"][0]["synced"] is True
    assert device.deliveries[0]["custom_headers"]["X-Bot"] == "saved"


@pytest.mark.asyncio
async def test_explicit_entity_is_exact_while_an_ambiguous_legacy_locator_is_refused():
    from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError

    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test", "config": {"headers": {"X-Bot": "saved"}}}])
    world.bots.insert({
        "bot_id": "bot-1", "owner_id": "entity-1", "entity_id": "another-entity", "entity_type": "staff",
        "creator_id": "entity-1", "status": "ACTIVE", "active_engine": "moltis", "bot_type": "personal",
    })
    device = RecordingDevice()
    service = sync_service(world)
    result = await service.sync_mcp_details_for_bot(
        user_id="owner", entity_id="entity-1", bot_id="bot-1", engine_type="moltis",
        mcp_entries=[world.center.get_mcp_detail("mcp.test")], device_sync=device,
    )
    assert result["success"] is True
    assert device.deliveries[0]["custom_headers"]["X-Bot"] == "saved"
    rejected = RecordingDevice()
    with pytest.raises(BotLookupAmbiguousError):
        await service.sync_mcp_details_for_bot(
            user_id="entity-1", bot_id="bot-1", engine_type="moltis",
            mcp_entries=[world.center.get_mcp_detail("mcp.test")], device_sync=rejected,
        )
    assert rejected.deliveries == []


@pytest.mark.asyncio
async def test_ambiguous_account_target_is_a_failed_fanout_without_delivery():
    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test", "config": {"headers": {"X-Bot": "private"}}}])
    world.bots.insert({
        "bot_id": "bot-1", "owner_id": "owner", "entity_id": "another-entity", "entity_type": "staff",
        "creator_id": "entity-1", "status": "ACTIVE", "active_engine": "moltis", "bot_type": "personal",
    })
    device = RecordingDevice()
    result = await connected_sync_service(world, device).sync_mcp_detail_to_all_bots(
        user_id="owner", entity_id="owner", entity_type="staff", server_code="mcp.test",
        mcp_data=world.center.get_mcp_detail("mcp.test"), target_bot_ids=["bot-1"],
        target_bot_owners={"bot-1": "owner"},
    )
    assert result["sync_summary"]["failed_count"] == 1
    assert result["sync_results"][0]["synced"] is False
    assert "ambiguous" in result["sync_results"][0]["error"]
    assert device.deliveries == []


@pytest.mark.asyncio
async def test_account_drift_recovers_the_real_team_resource_with_user_inheritance(tmp_path):
    from agentclaw.community.core.repository.implementations.skill_center.category import SkillCategoryRepository
    from agentclaw.community.core.repository.implementations.skill_center.skill import SkillSetRepository
    from agentclaw.community.core.skill_center.services.skill_cache import MarketCache
    from agentclaw.community.core.skill_center.services.skill_service import SkillService
    from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory

    world = McpWorld()
    with world.db.transactional_orm_session() as session:
        session.query(world.bots.Model).filter_by(bot_id="bot-1", owner_id="owner").one().entity_type = "team"
    world.config.user_mcp_config_repo.create({
        "user_id": "owner", "server_code": "mcp.test", "env": get_current_env(),
        "extra_config": {"headers": {"X-User": "inherited"}},
    })
    await world.apply_manifest([{"server_code": "mcp.test", "config": {"headers": {"X-Bot": "saved"}}}])
    repo_sync = MagicMock()
    skills = SkillService(
        world.skills, repo_sync, SkillCategoryRepository(world.db), MarketCache(MagicMock()),
        device_fs_factory=unused_provider, git_sync_service_factory=unused_provider,
        active_dir=tmp_path / "active", repo_dir=tmp_path / "repo",
        local_dir=tmp_path / "local", global_repo_dir=tmp_path / "shared",
    )
    real_provider = SkillSetService(
        world.skills, SkillSetRepository(world.db), world.center, world.config, skills, world.bots,
        bot_id="bot-1", engine_type="moltis", skills_dir=tmp_path / "active",
        repo_dir=tmp_path / "repo", local_dir=tmp_path / "local",
        path_factory=WorkspacePathFactory(repo_sync), reader=world.capabilities,
        ext_info_provider=lambda _bot_id: {},
    )
    collections = []

    class RecordingCollection:
        def collect_bot_active_mcps(self, **kwargs):
            collections.append(kwargs)
            return real_provider.collect_bot_active_mcps(**kwargs)

    class DriftedDevice(RecordingDevice):
        def __init__(self):
            super().__init__()
            self.scopes = []

        def has_mcp(self, code):
            return False

        def sync_all_mcp_servers(self, entries):
            self.scopes.append(entries)
            return True

    class Passport:
        def __init__(self):
            self.updates = []

        def query_agent_passport(self, bot_id, user_id):
            return {"mcps": [], "clis": []}

        def update_passport(self, **kwargs):
            self.updates.append(kwargs)

    world.center.get_mcp_list = lambda **kw: {
        "success": True, "data": [world.center.get_mcp_detail(code) for code in kw["server_codes"]],
    }
    device, passport = DriftedDevice(), Passport()
    service = connected_sync_service(world, device, provider=lambda: RecordingCollection(), passport=passport)
    result = await service.sync_mcp_detail_to_all_bots(
        user_id="owner", entity_id="owner", entity_type="staff", server_code="mcp.test",
        mcp_data=world.center.get_mcp_detail("mcp.test"), target_bot_ids=["bot-1"],
        target_bot_owners={"bot-1": "owner"},
    )
    assert result["sync_results"][0]["synced"] is True
    assert collections and all(
        entry["entity_id"] == "entity-1" and entry["entity_type"] == "team" and entry["user_id"] == "owner"
        for entry in collections
    )
    assert {entry["server_code"] for entry in device.scopes[0]} == {"mcp.test"}
    assert passport.updates[0]["user_id"] == "owner"
    assert len(device.deliveries) == 2
    assert all(delivery["custom_headers"]["X-Bot"] == "saved" for delivery in device.deliveries)
    assert all(delivery["custom_headers"]["X-User"] == "inherited" for delivery in device.deliveries)


@pytest.mark.asyncio
async def test_invalid_single_payload_fails_before_resolving_a_missing_bot():
    world = McpWorld()
    result = await sync_service(world).sync_mcp_detail(user_id="missing", bot_id="missing", mcp_data={})
    assert result == {"success": False, "error": "向 bot=missing 推送 MCP 配置失败"}


@pytest.mark.asyncio
async def test_account_fanout_treats_owner_as_a_human_even_when_another_bot_uses_it_as_entity():
    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test", "config": {"headers": {"X-Bot": "saved"}}}])
    world.config.user_mcp_config_repo.create({
        "user_id": "owner", "server_code": "mcp.test", "env": get_current_env(),
        "extra_config": {"headers": {"X-User": "inherited"}},
    })
    world.bots.insert({
        "bot_id": "bot-1", "owner_id": "someone-else", "entity_id": "owner", "entity_type": "staff",
        "creator_id": "someone-else", "status": "ACTIVE", "active_engine": "moltis", "bot_type": "service",
    })
    device = RecordingDevice()
    result = await connected_sync_service(world, device).sync_mcp_detail_to_all_bots(
        user_id="owner", entity_id="owner", entity_type="staff", server_code="mcp.test",
        mcp_data=world.center.get_mcp_detail("mcp.test"), target_bot_ids=["bot-1"],
        target_bot_owners={"bot-1": "owner"},
    )
    assert result["sync_results"][0]["synced"] is True
    assert device.deliveries[0]["custom_headers"]["X-Bot"] == "saved"
    assert device.deliveries[0]["custom_headers"]["X-User"] == "inherited"


@pytest.mark.asyncio
async def test_explicit_entity_uses_resolved_binding_when_another_bot_has_that_owner():
    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test", "config": {"headers": {"X-Bot": "saved"}}}])
    world.bots.insert({
        "bot_id": "bot-1", "owner_id": "entity-1", "entity_id": "other-resource", "entity_type": "staff",
        "creator_id": "entity-1", "status": "ACTIVE", "active_engine": "moltis", "bot_type": "service",
    })
    device = RecordingDevice()
    result = await connected_sync_service(world, device).sync_mcp_detail(
        user_id="owner", entity_id="entity-1", bot_id="bot-1", mcp_data=world.center.get_mcp_detail("mcp.test"),
    )
    assert result["success"] is True
    assert device.deliveries[0]["custom_headers"]["X-Bot"] == "saved"

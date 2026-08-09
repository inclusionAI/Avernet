"""Service-backed API contract tests for Gateway-facing backend routes.

These tests intentionally keep the HTTP handler and the real service in the
path. Only the test database is synthetic: data is seeded through the real
repository bound in the per-test SQLite injector.
"""
from __future__ import annotations

from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.device_service import DeviceServiceProtocol
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.api.skill_set_service_factory import SkillSetServiceFactoryProtocol
from agentclaw.community.core.access.services.policy_service import PolicyService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot import TemplateRepository
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.repository.protocols.bot import BotFriendRepositoryProtocol
from agentclaw.community.core.bot_public.repository.models import BotFriendStatus
from agentclaw.community.core.bot_public.services.bot_public_service import BotPublicService
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.devices.services.device_service import (
    DeviceService,
    LOCAL_DEVICE_PROVIDER,
)
from agentclaw.community.core.skill_center.factories import (
    SkillServiceFactory,
    SkillSetServiceFactory,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillSetRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.repository.implementations.bot.friend import BotFriendRepository
from agentclaw.community.utils.env_utils import get_current_env

from tests.community.contracts.gateway.conftest import (
    assert_api_response_contract,
    assert_has_fields,
    assert_success,
)

OWNER_ID = "448524"
BOT_ID = "svc_contract_bot"
BOT_NAME = "service-backed-contract-bot"
ENGINE_TYPE = "openclaw"
PUBLIC_OWNER_ID = "100001"
PUBLIC_BOT_ID = "svc_contract_public_bot"
PUBLIC_BOT_NAME = "service-backed-public-contract-bot"


def _bot_seed(
    *,
    bot_id: str = BOT_ID,
    bot_name: str = BOT_NAME,
    binding_id: int | None = 100,
    owner_id: str = OWNER_ID,
    entity_id: str | None = None,
    owner_name: str = "TestUser",
    public: str = "private",
    friend_approval: str = "auto",
    bot_type: str = "personal",
    status: str = "ACTIVE",
    ext_extra: dict | None = None,
) -> dict:
    resolved_entity_id = entity_id or owner_id
    ext = {
        "passport": {"status": "AUTHORIZED", "is_first_bot": False},
        "avatar_url": "https://example.com/avatar.png",
        "start_status": "running",
        "start_message": "",
        "friend_approval": friend_approval,
        "public_approval": {
            "puid": "",
            "public": public,
            "status": "",
            "applicant": "",
            "approval_url": "",
            "processed_at": "",
            "friend_approval": friend_approval,
            "permission_owner": "caller",
        },
        "permission_owner": "caller",
        "env": "dev",
        "bot_type": bot_type,
        "can_edit_bot": True,
        "engine_paths": {},
        "bot_work_dir": "",
    }
    if ext_extra:
        ext.update(ext_extra)
    return {
        "bot_id": bot_id,
        "bot_name": bot_name,
        "bot_desc": "Seeded through the real BotRepository",
        "entity_id": resolved_entity_id,
        "entity_type": "staff",
        "creator_id": owner_id,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "engine_types": [ENGINE_TYPE, "moltis"],
        "active_engine": ENGINE_TYPE,
        "status": status,
        "binding_id": binding_id,
        "device_id": f"device_{bot_id}",
        "modifier_id": owner_id,
        "share_policy": {"visibility": "team", "allowed_staff_ids": [owner_id]},
        "is_delete": 0,
        "public": public,
        "bot_type": bot_type,
        "template_type": "applicationCoding",
        "ext": ext,
    }


def _seed_bot(world, **overrides) -> dict:
    repo = world.get(BotRepository)
    return repo.insert(_bot_seed(**overrides))


def _seed_device_binding(
    world,
    *,
    entity_id: str = OWNER_ID,
    device_id: str = "svc_contract_device",
    status: str = DeviceBindingStatus.ACTIVE.value,
) -> int:
    repo = world.get(DeviceBindingRepository)
    return repo.insert_binding(
        entity_id=entity_id,
        entity_type="staff",
        device_id=device_id,
        device_provider=LOCAL_DEVICE_PROVIDER,
        env=get_current_env(),
        # NOTE: 故意不传 ``adapter_port``——plan-02 后 dispatcher 的
        # ``_build_binding_ctx`` 看到该字段存在才视作 "BaaS-managed binding"
        # 并构造 BaaS 模式 LocalDeviceFileSystem。Contract gateway 测试需要
        # pathlib fallback（用 tmp_path 真读写），否则 fs.read_file 会发起
        # 真 HTTP 调用到 mock BaaS 给的 http_url 触发 ConnectError。
        device_props={
            "callback_token": "svc-contract-token",
            "sandbox_id": "svc-contract-sandbox",
        },
        status=status,
        apply_reason="service-backed API contract",
        applied_by=entity_id,
    )


def _seed_skill(
    world,
    *,
    name: str = "ContractSkill",
    git_path: str = "git://contract/dev/contract-skill",
    user_id: str = OWNER_ID,
    bolt_id: str = BOT_ID,
    skill_uuid: str = "contract-skill-uuid",
) -> dict:
    repo = world.get(SkillRepository)
    return repo.create(
        {
            "name": name,
            "description": "Seeded through the real SkillRepository",
            "git_path": git_path,
            "category": "contract",
            "tags": ["contract", "api"],
            "input_schema": "{}",
            "output_schema": "{}",
            "is_public": True,
            "is_builtin": False,
            "user_id": user_id,
            "bolt_id": bolt_id,
            "status": "PUBLISHED",
            "version": 1,
            "skill_uuid": skill_uuid,
            "source_type": "git",
            "category_path": "/contract",
            "package_url": "",
            "zip_url": "",
        }
    )


def _seed_skill_set(
    world,
    *,
    name: str = "Contract Skill Set",
    user_id: str = OWNER_ID,
    bolt_id: str = BOT_ID,
    is_default: bool = False,
    is_active: bool = True,
    engine_type: str = ENGINE_TYPE,
) -> dict:
    repo = world.get(SkillSetRepository)
    return repo.create(
        {
            "name": name,
            "description": "Seeded through the real SkillSetRepository",
            "user_id": user_id,
            "bolt_id": bolt_id,
            "is_default": is_default,
            "is_builtin": False,
            "is_active": 1 if is_active else 0,
            "engine_type": engine_type,
        }
    )


def _seed_skill_set_with_skill_and_mcp(world) -> tuple[dict, dict]:
    skill = _seed_skill(world)
    skill_set = _seed_skill_set(world)
    repo = world.get(SkillSetRepository)
    repo.add_skill_to_set(skill_set["id"], skill["id"], user_id=OWNER_ID)
    repo.add_mcp_to_set(
        skill_set["id"],
        "contract.mcp.echo",
        "Contract Echo MCP",
        description="Seeded MCP association",
        icon="https://example.com/mcp.png",
        user_id=OWNER_ID,
        env=get_current_env(),
    )
    return skill_set, skill


def _seed_bot_friend(
    world,
    *,
    bot_id: str = PUBLIC_BOT_ID,
    requester_id: str = OWNER_ID,
    target_owner_id: str = PUBLIC_OWNER_ID,
    status: str = BotFriendStatus.ACCEPTED.value,
) -> dict:
    repo = world.get(BotFriendRepositoryProtocol)
    return repo.insert(
        {
            "requester_entity_id": requester_id,
            "requester_name": "TestUser",
            "target_entity_id": target_owner_id,
            "target_name": PUBLIC_BOT_NAME,
            "target_bot_id": bot_id,
            "target_owner_name": "PublicOwner",
            "status": status,
            "ext": {"source": "service-backed-contract"},
        }
    )


def _seed_template(
    world,
    *,
    bot_id: str,
    architect_bot_id: str,
) -> dict:
    repo = world.get(TemplateRepository)
    return repo.insert(
        {
            "bot_id": bot_id,
            "ext": {
                "architect_bot_id": architect_bot_id,
                "template": "service-backed-contract",
            },
        }
    )


def _assert_real_bot_service_bound(world) -> None:
    service = world.get(BotService)
    protocol_service = world.get(BotServiceProtocol)
    assert isinstance(service, BotService)
    assert protocol_service is service


def _assert_real_device_service_bound(world) -> None:
    service = world.get(DeviceService)
    protocol_service = world.get(DeviceServiceProtocol)
    assert isinstance(service, DeviceService)
    assert protocol_service is service


def _assert_real_skill_factories_bound(world) -> None:
    skill_factory = world.get(SkillServiceFactory)
    protocol_skill_factory = world.get(SkillServiceFactoryProtocol)
    skill_set_factory = world.get(SkillSetServiceFactory)
    protocol_skill_set_factory = world.get(SkillSetServiceFactoryProtocol)
    assert isinstance(skill_factory, SkillServiceFactory)
    assert protocol_skill_factory is skill_factory
    assert isinstance(skill_set_factory, SkillSetServiceFactory)
    assert protocol_skill_set_factory is skill_set_factory


def _assert_real_bot_public_service_bound(world) -> None:
    service = world.get(BotPublicService)
    protocol_service = world.get(BotPublicServiceProtocol)
    friend_repo = world.get(BotFriendRepositoryProtocol)
    assert isinstance(service, BotPublicService)
    assert protocol_service is service
    assert isinstance(friend_repo, BotFriendRepository)


class TestServiceBackedBotManagementApiContracts:
    """Rule #1 contracts that exercise the real BotService layer."""

    def test_get_bot_uses_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(world)
        _assert_real_bot_service_bound(world)

        resp = gw_client.get(f"/api/bots/{BOT_ID}")
        body = resp.json()

        assert_success(body, "service-backed GET /api/bots/{id}")
        assert_api_response_contract(
            body,
            "service_backed_rule01_GET_api_bots_id",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            body["data"],
            {
                "bot_id": str,
                "bot_name": str,
                "owner_id": str,
                "engine_types": list,
                "active_engine": str,
                "ext": dict,
            },
            label="service-backed GET /api/bots/{id} data",
        )

    def test_list_bots_by_owner_uses_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(world)
        _seed_bot(
            world,
            bot_id="svc_contract_bot_secondary",
            bot_name="service-backed-contract-bot-secondary",
            binding_id=None,
        )
        _assert_real_bot_service_bound(world)

        resp = gw_client.get("/api/bots/by-owner")
        body = resp.json()

        assert_success(body, "service-backed GET /api/bots/by-owner")
        assert_api_response_contract(
            body,
            "service_backed_rule01_GET_api_bots_by_owner",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            body["data"],
            {"total": int, "items": list, "default_bot": dict, "engine_types": list},
            label="service-backed GET /api/bots/by-owner data",
        )
        assert body["data"]["total"] == 2
        assert_has_fields(
            body["data"]["items"][0],
            {
                "bot_id": str,
                "bot_name": str,
                "engine_paths": dict,
                "bot_work_dir": str,
                "can_edit_bot": bool,
            },
            label="service-backed GET /api/bots/by-owner items[0]",
        )

    def test_check_bot_name_uses_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(world)
        _assert_real_bot_service_bound(world)

        resp = gw_client.get("/api/bots/check/name", params={"bot_name": BOT_NAME})
        body = resp.json()

        assert_success(body, "service-backed GET /api/bots/check/name")
        assert_api_response_contract(
            body,
            "service_backed_rule01_GET_api_bots_check_name",
            update=contract_snapshot_update,
        )
        assert body["data"] == {"exists": True, "bot_name": BOT_NAME}

    def test_update_bot_ext_uses_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(world)
        _assert_real_bot_service_bound(world)

        resp = gw_client.put(
            f"/api/bots/{BOT_ID}",
            json={"avatar_url": "https://example.com/updated-avatar.png"},
        )
        body = resp.json()

        assert_success(body, "service-backed PUT /api/bots/{id}")
        assert_api_response_contract(
            body,
            "service_backed_rule01_PUT_api_bots_id",
            update=contract_snapshot_update,
        )
        assert body["data"]["ext"]["avatar_url"] == "https://example.com/updated-avatar.png"


class TestServiceBackedBotReadApiContracts:
    """Additional BotService read contracts not covered by the legacy mock tests."""

    def test_bot_list_and_search_use_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(world)
        _seed_bot(
            world,
            bot_id="svc_contract_search_secondary",
            bot_name="service-backed-contract-search-secondary",
            binding_id=None,
        )
        _assert_real_bot_service_bound(world)

        list_resp = gw_client.get(
            "/api/bots",
            params={
                "entity_id": OWNER_ID,
                "entity_type": "staff",
                "page": 1,
                "page_size": 20,
            },
        )
        list_body = list_resp.json()
        assert_success(list_body, "service-backed GET /api/bots")
        assert_api_response_contract(
            list_body,
            "service_backed_rule01_GET_api_bots",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            list_body["data"],
            {"total": int, "items": list},
            label="service-backed GET /api/bots data",
        )

        search_resp = gw_client.post(
            "/api/bots/search",
            json={
                "key": "service-backed-contract",
                "owner_id": OWNER_ID,
                "bot_status": "ACTIVE",
                "bot_type": "personal",
                "page": 1,
                "page_size": 20,
            },
        )
        search_body = search_resp.json()
        assert_success(search_body, "service-backed POST /api/bots/search")
        assert_api_response_contract(
            search_body,
            "service_backed_rule01_POST_api_bots_search",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            search_body["data"],
            {"total": int, "items": list},
            label="service-backed POST /api/bots/search data",
        )
        assert_has_fields(
            search_body["data"]["items"][0],
            {"bot_id": str, "bot_name": str, "owner_id": str, "publish": (dict, type(None))},
            label="service-backed POST /api/bots/search items[0]",
        )

    def test_bot_detail_status_and_paths_use_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        binding_id = _seed_device_binding(world, device_id="svc_contract_bot_read_device")
        _seed_bot(world, binding_id=binding_id)
        world.get(PolicyService).allow(entity_id=OWNER_ID, entity_type="staff")
        _assert_real_bot_service_bound(world)

        detail_resp = gw_client.get(
            f"/api/bots/{BOT_ID}/detail-by-owner",
            params={"owner_id": OWNER_ID},
        )
        detail_body = detail_resp.json()
        assert_success(detail_body, "service-backed GET /api/bots/{id}/detail-by-owner")
        assert_api_response_contract(
            detail_body,
            "service_backed_rule01_GET_api_bots_id_detail_by_owner",
            update=contract_snapshot_update,
        )

        status_resp = gw_client.get(
            f"/api/bots/{BOT_ID}/status",
            params={"owner_id": OWNER_ID},
        )
        status_body = status_resp.json()
        assert_success(status_body, "service-backed GET /api/bots/{id}/status")
        assert_api_response_contract(
            status_body,
            "service_backed_rule01_GET_api_bots_id_status",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            status_body["data"],
            {
                "bot_id": str,
                "bot_status": str,
                "binding_status": str,
                "device_id": str,
                "device_provider": str,
                "is_ready": bool,
                "ext": dict,
            },
            label="service-backed GET /api/bots/{id}/status data",
        )

        work_dir_resp = gw_client.get(
            f"/api/bots/{BOT_ID}/work-dir",
            params={"owner_id": OWNER_ID, "engine_type": ENGINE_TYPE},
        )
        work_dir_body = work_dir_resp.json()
        assert_success(work_dir_body, "service-backed GET /api/bots/{id}/work-dir")
        assert_api_response_contract(
            work_dir_body,
            "service_backed_rule01_GET_api_bots_id_work_dir",
            update=contract_snapshot_update,
        )
        assert isinstance(work_dir_body["data"], str)

        config_dir_resp = gw_client.get(
            f"/api/bots/{BOT_ID}/config-dir",
            params={"owner_id": OWNER_ID, "engine_type": ENGINE_TYPE},
        )
        config_dir_body = config_dir_resp.json()
        assert_success(config_dir_body, "service-backed GET /api/bots/{id}/config-dir")
        assert_api_response_contract(
            config_dir_body,
            "service_backed_rule01_GET_api_bots_id_config_dir",
            update=contract_snapshot_update,
        )
        assert isinstance(config_dir_body["data"], str)

        engine_config_resp = gw_client.get(
            f"/api/bots/{BOT_ID}/engine-config",
            params={"owner_id": OWNER_ID, "engine_type": ENGINE_TYPE},
        )
        engine_config_body = engine_config_resp.json()
        assert_success(engine_config_body, "service-backed GET /api/bots/{id}/engine-config")
        assert_api_response_contract(
            engine_config_body,
            "service_backed_rule01_GET_api_bots_id_engine_config",
            update=contract_snapshot_update,
        )
        assert isinstance(engine_config_body["data"], dict)

    def test_domain_and_appcoding_bot_reads_use_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        architect_bot_id = "svc_contract_architect_bot"
        coding_bot_id = "svc_contract_appcoding_bot"
        _seed_bot(
            world,
            bot_id=architect_bot_id,
            bot_name="service-backed-contract-architect",
            binding_id=None,
            ext_extra={"is_domain_bot": True},
            bot_type="service",
        )
        _seed_bot(
            world,
            bot_id=coding_bot_id,
            bot_name="service-backed-contract-appcoding",
            binding_id=None,
        )
        _seed_template(world, bot_id=coding_bot_id, architect_bot_id=architect_bot_id)
        _assert_real_bot_service_bound(world)

        domain_resp = gw_client.get(
            "/api/bots/search/domain-bots",
            params={"page": 1, "page_size": 20},
        )
        domain_body = domain_resp.json()
        assert_success(domain_body, "service-backed GET /api/bots/search/domain-bots")
        assert_api_response_contract(
            domain_body,
            "service_backed_rule01_GET_api_bots_search_domain_bots",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            domain_body["data"],
            {"total": int, "items": list},
            label="service-backed GET /api/bots/search/domain-bots data",
        )

        appcoding_resp = gw_client.get(f"/api/bots/{architect_bot_id}/appcoding-bots")
        appcoding_body = appcoding_resp.json()
        assert_success(appcoding_body, "service-backed GET /api/bots/{id}/appcoding-bots")
        assert_api_response_contract(
            appcoding_body,
            "service_backed_rule01_GET_api_bots_id_appcoding_bots",
            update=contract_snapshot_update,
        )
        assert isinstance(appcoding_body["data"], list)
        assert_has_fields(
            appcoding_body["data"][0],
            {"bot_id": str, "bot_name": str, "template_config": dict},
            label="service-backed GET /api/bots/{id}/appcoding-bots data[0]",
        )


class TestServiceBackedDeviceApiContracts:
    """Rule #5 contracts that exercise the real DeviceService layer."""

    def test_device_lookup_list_and_connection_use_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        binding_id = _seed_device_binding(world)
        _assert_real_device_service_bound(world)

        get_resp = gw_client.get(f"/api/v1/devices/{binding_id}")
        get_body = get_resp.json()
        assert_success(get_body, "service-backed GET /api/v1/devices/{id}")
        assert_api_response_contract(
            get_body,
            "service_backed_rule05_GET_api_v1_devices_id",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            get_body["data"],
            {
                "id": int,
                "entity_id": str,
                "entity_type": str,
                "device_id": str,
                "device_provider": str,
                "status": str,
            },
            label="service-backed GET /api/v1/devices/{id} data",
        )

        by_id_resp = gw_client.get("/api/v1/devices/by-id/svc_contract_device")
        by_id_body = by_id_resp.json()
        assert_success(by_id_body, "service-backed GET /api/v1/devices/by-id/{device_id}")
        assert_api_response_contract(
            by_id_body,
            "service_backed_rule05_GET_api_v1_devices_by_id_device_id",
            update=contract_snapshot_update,
        )

        list_resp = gw_client.get(
            "/api/v1/devices",
            params={"entity_type": "staff", "status": DeviceBindingStatus.ACTIVE.value},
        )
        list_body = list_resp.json()
        assert_success(list_body, "service-backed GET /api/v1/devices")
        assert_api_response_contract(
            list_body,
            "service_backed_rule05_GET_api_v1_devices",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            list_body["data"],
            {"total": int, "items": list},
            label="service-backed GET /api/v1/devices data",
        )

        connection_resp = gw_client.get(
            f"/api/v1/devices/{binding_id}/connection",
            params={"port": 20003},
        )
        connection_body = connection_resp.json()
        assert_success(connection_body, "service-backed GET /api/v1/devices/{id}/connection")
        assert_api_response_contract(
            connection_body,
            "service_backed_rule05_GET_api_v1_devices_id_connection",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            connection_body["data"],
            {
                "type": str,
                "target": str,
                "token": str,
                "engine_type": str,
                "available": bool,
                "message": str,
            },
            label="service-backed GET /api/v1/devices/{id}/connection data",
        )

    def test_connectable_devices_use_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        binding_id = _seed_device_binding(world, device_id="svc_contract_connectable_device")
        _seed_bot(world, binding_id=binding_id)
        _assert_real_device_service_bound(world)

        resp = gw_client.get(
            "/api/v1/devices/connectable",
            params={
                "entity_id": OWNER_ID,
                "entity_type": "staff",
                "with_connection": True,
                "port": 20003,
            },
        )
        body = resp.json()

        assert_success(body, "service-backed GET /api/v1/devices/connectable")
        assert_api_response_contract(
            body,
            "service_backed_rule05_GET_api_v1_devices_connectable",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            body["data"],
            {"total": int, "items": list},
            label="service-backed GET /api/v1/devices/connectable data",
        )
        assert body["data"]["total"] == 1
        assert_has_fields(
            body["data"]["items"][0],
            {
                "id": int,
                "entity_id": str,
                "entity_type": str,
                "device_id": str,
                "device_provider": str,
                "connection": dict,
            },
            label="service-backed GET /api/v1/devices/connectable items[0]",
        )


class TestServiceBackedSkillCenterApiContracts:
    """Rule #10/#15 contracts that exercise real skill-center services."""

    def test_skillset_read_endpoints_use_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(world, binding_id=None)
        skill_set, _skill = _seed_skill_set_with_skill_and_mcp(world)
        _assert_real_skill_factories_bound(world)

        common_params = {
            "user_id": OWNER_ID,
            "bot_id": BOT_ID,
            "engine_type": ENGINE_TYPE,
        }
        list_resp = gw_client.get("/api/skillsets", params=common_params)
        list_body = list_resp.json()
        assert_success(list_body, "service-backed GET /api/skillsets")
        assert_api_response_contract(
            list_body,
            "service_backed_rule15_GET_api_skillsets",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            list_body["data"][0],
            {
                "id": str,
                "name": str,
                "is_default": bool,
                "is_builtin": bool,
                "user_id": str,
                "bot_id": str,
                "is_active": bool,
                "skills": list,
            },
            label="service-backed GET /api/skillsets data[0]",
        )

        detail_resp = gw_client.get(
            f"/api/skillsets/{skill_set['id']}",
            params=common_params,
        )
        detail_body = detail_resp.json()
        assert_success(detail_body, "service-backed GET /api/skillsets/{id}")
        assert_api_response_contract(
            detail_body,
            "service_backed_rule15_GET_api_skillsets_id",
            update=contract_snapshot_update,
        )

        skills_resp = gw_client.get(
            f"/api/skillsets/{skill_set['id']}/skills",
            params=common_params,
        )
        skills_body = skills_resp.json()
        assert_success(skills_body, "service-backed GET /api/skillsets/{id}/skills")
        assert_api_response_contract(
            skills_body,
            "service_backed_rule15_GET_api_skillsets_id_skills",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            skills_body["data"][0],
            {"id": str, "name": str, "description": str, "category": str, "git_path": str},
            label="service-backed GET /api/skillsets/{id}/skills data[0]",
        )

        with_mcps_resp = gw_client.get("/api/skillsets/with-mcps", params=common_params)
        with_mcps_body = with_mcps_resp.json()
        assert_success(with_mcps_body, "service-backed GET /api/skillsets/with-mcps")
        assert_api_response_contract(
            with_mcps_body,
            "service_backed_rule15_GET_api_skillsets_with_mcps",
            update=contract_snapshot_update,
        )

        mcps_resp = gw_client.get(
            f"/api/skillsets/{skill_set['id']}/mcps",
            params=common_params,
        )
        mcps_body = mcps_resp.json()
        assert_success(mcps_body, "service-backed GET /api/skillsets/{id}/mcps")
        assert_api_response_contract(
            mcps_body,
            "service_backed_rule15_GET_api_skillsets_id_mcps",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            mcps_body["data"][0],
            {"id": str, "server_code": str, "name": str, "status": str},
            label="service-backed GET /api/skillsets/{id}/mcps data[0]",
        )

    def test_skillset_write_endpoints_use_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(world, binding_id=None)
        skill = _seed_skill(
            world,
            name="ContractWriteSkill",
            git_path="git://contract/dev/contract-write-skill",
            skill_uuid="contract-write-skill-uuid",
        )
        skill_set = _seed_skill_set(
            world,
            name="Contract Write Skill Set",
            is_active=False,
        )
        _assert_real_skill_factories_bound(world)

        common_params = {
            "user_id": OWNER_ID,
            "bot_id": BOT_ID,
            "engine_type": ENGINE_TYPE,
        }
        create_resp = gw_client.post(
            "/api/skillsets",
            params={"bot_id": BOT_ID, "engine_type": ENGINE_TYPE},
            json={
                "name": "Created Contract Skill Set",
                "description": "Created through the HTTP contract test",
                "user_id": OWNER_ID,
                "bot_id": BOT_ID,
            },
        )
        create_body = create_resp.json()
        assert_success(create_body, "service-backed POST /api/skillsets")
        assert_api_response_contract(
            create_body,
            "service_backed_rule15_POST_api_skillsets",
            update=contract_snapshot_update,
        )

        update_resp = gw_client.put(
            f"/api/skillsets/{skill_set['id']}",
            params=common_params,
            json={
                "description": "Updated through the HTTP contract test",
                "user_id": OWNER_ID,
                "bot_id": BOT_ID,
            },
        )
        update_body = update_resp.json()
        assert_success(update_body, "service-backed PUT /api/skillsets/{id}")
        assert_api_response_contract(
            update_body,
            "service_backed_rule15_PUT_api_skillsets_id",
            update=contract_snapshot_update,
        )

        add_resp = gw_client.post(
            f"/api/skillsets/{skill_set['id']}/skills",
            params={"bot_id": BOT_ID, "engine_type": ENGINE_TYPE},
            json={"skill_ids": [skill["id"]], "user_id": OWNER_ID, "bot_id": BOT_ID},
        )
        add_body = add_resp.json()
        assert_success(add_body, "service-backed POST /api/skillsets/{id}/skills")
        assert_api_response_contract(
            add_body,
            "service_backed_rule15_POST_api_skillsets_id_skills",
            update=contract_snapshot_update,
        )

        remove_resp = gw_client.delete(
            f"/api/skillsets/{skill_set['id']}/skills/{skill['id']}",
            params=common_params,
        )
        remove_body = remove_resp.json()
        assert_success(remove_body, "service-backed DELETE /api/skillsets/{id}/skills/{skill_id}")
        assert_api_response_contract(
            remove_body,
            "service_backed_rule15_DELETE_api_skillsets_id_skills_skill_id",
            update=contract_snapshot_update,
        )

        delete_resp = gw_client.delete(
            f"/api/skillsets/{skill_set['id']}",
            params=common_params,
        )
        delete_body = delete_resp.json()
        assert_success(delete_body, "service-backed DELETE /api/skillsets/{id}")
        assert_api_response_contract(
            delete_body,
            "service_backed_rule15_DELETE_api_skillsets_id",
            update=contract_snapshot_update,
        )

    def test_skill_market_and_active_skillsets_use_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(world, binding_id=None)
        _seed_skill(world, name="ContractMarketSkill")
        _seed_skill_set(world, name="Contract Active Skill Set", is_active=True)
        _assert_real_skill_factories_bound(world)

        market_params = {
            "entity_id": OWNER_ID,
            "bot_id": BOT_ID,
            "engine_type": ENGINE_TYPE,
        }
        list_resp = gw_client.get("/api/skills/market/list", params=market_params)
        list_body = list_resp.json()
        assert_success(list_body, "service-backed GET /api/skills/market/list")
        assert_api_response_contract(
            list_body,
            "service_backed_rule10_GET_api_skills_market_list",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            list_body["data"][0],
            {
                "id": str,
                "name": str,
                "description": str,
                "git_path": str,
                "is_public": bool,
                "is_builtin": bool,
                "status": str,
                "version": int,
            },
            label="service-backed GET /api/skills/market/list data[0]",
        )

        search_resp = gw_client.post(
            "/api/skills/market/search",
            params=market_params,
            json={"query": "ContractMarket", "page": 1, "page_size": 20},
        )
        search_body = search_resp.json()
        assert_success(search_body, "service-backed POST /api/skills/market/search")
        assert_api_response_contract(
            search_body,
            "service_backed_rule10_POST_api_skills_market_search",
            update=contract_snapshot_update,
        )

        active_resp = gw_client.get(
            "/api/skills/skillset/active",
            params={"entity_id": OWNER_ID, "bot_id": BOT_ID, "engine_type": ENGINE_TYPE},
        )
        active_body = active_resp.json()
        assert_success(active_body, "service-backed GET /api/skills/skillset/active")
        assert_api_response_contract(
            active_body,
            "service_backed_rule10_GET_api_skills_skillset_active",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            active_body["data"][0],
            {
                "id": str,
                "name": str,
                "is_default": bool,
                "is_builtin": bool,
                "is_active": bool,
                "engine_type": str,
                "bot_id": str,
            },
            label="service-backed GET /api/skills/skillset/active data[0]",
        )


class TestServiceBackedSkillCrudApiContracts:
    """SkillService CRUD contracts that exercise the real service factory."""

    def test_skill_crud_endpoints_use_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(world, binding_id=None)
        _assert_real_skill_factories_bound(world)

        create_resp = gw_client.post(
            "/api/skills",
            json={
                "name": "ContractCrudSkill",
                "description": "Created through the real SkillService",
                "git_path": "local:///tmp/service-backed-contract-crud-skill",
                "category": "contract",
                "tags": ["contract", "crud"],
                "input_schema": "{}",
                "output_schema": "{}",
                "is_public": True,
                "user_id": OWNER_ID,
                "bot_id": BOT_ID,
                "source_type": "local",
            },
        )
        create_body = create_resp.json()
        assert_success(create_body, "service-backed POST /api/skills")
        assert_api_response_contract(
            create_body,
            "service_backed_rule10_POST_api_skills",
            update=contract_snapshot_update,
        )
        skill_id = create_body["data"]["id"]

        list_resp = gw_client.get(
            "/api/skills",
            params={"user_id": OWNER_ID, "bot_id": BOT_ID, "limit": 20, "offset": 0},
        )
        list_body = list_resp.json()
        assert_success(list_body, "service-backed GET /api/skills")
        assert_api_response_contract(
            list_body,
            "service_backed_rule10_GET_api_skills",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            list_body,
            {"success": bool, "data": list, "total": int, "limit": int, "offset": int},
            label="service-backed GET /api/skills top-level",
        )

        get_resp = gw_client.get(
            f"/api/skills/{skill_id}",
            params={"user_id": OWNER_ID},
        )
        get_body = get_resp.json()
        assert_success(get_body, "service-backed GET /api/skills/{id}")
        assert_api_response_contract(
            get_body,
            "service_backed_rule10_GET_api_skills_id",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            get_body["data"],
            {
                "id": str,
                "name": str,
                "description": str,
                "git_path": str,
                "category": str,
                "tags": list,
                "is_public": bool,
                "is_builtin": bool,
                "user_id": str,
            },
            label="service-backed GET /api/skills/{id} data",
        )

        update_resp = gw_client.put(
            f"/api/skills/{skill_id}",
            json={
                "description": "Updated through the real SkillService",
                "tags": ["contract", "updated"],
                "user_id": OWNER_ID,
            },
        )
        update_body = update_resp.json()
        assert_success(update_body, "service-backed PUT /api/skills/{id}")
        assert_api_response_contract(
            update_body,
            "service_backed_rule10_PUT_api_skills_id",
            update=contract_snapshot_update,
        )
        assert update_body["data"]["description"] == "Updated through the real SkillService"

        delete_resp = gw_client.delete(
            f"/api/skills/{skill_id}",
            params={"entity_id": OWNER_ID, "bot_id": BOT_ID, "engine_type": ENGINE_TYPE},
        )
        delete_body = delete_resp.json()
        assert_success(delete_body, "service-backed DELETE /api/skills/{id}")
        assert_api_response_contract(
            delete_body,
            "service_backed_rule10_DELETE_api_skills_id",
            update=contract_snapshot_update,
        )
        assert delete_body["message"] == "Skill deleted successfully"


class TestServiceBackedBotPublicApiContracts:
    """Rule #2 contracts that exercise the real BotPublicService layer."""

    def test_public_bot_search_uses_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(
            world,
            bot_id=PUBLIC_BOT_ID,
            bot_name=PUBLIC_BOT_NAME,
            owner_id=PUBLIC_OWNER_ID,
            owner_name="PublicOwner",
            binding_id=300,
            public="1",
            friend_approval="0",
        )
        _seed_bot_friend(world)
        _assert_real_bot_public_service_bound(world)

        resp = gw_client.get(
            "/api/v1/bot-public/search",
            params={"search": "public-contract", "page": 1, "page_size": 20},
        )
        body = resp.json()

        assert_success(body, "service-backed GET /api/v1/bot-public/search")
        assert_api_response_contract(
            body,
            "service_backed_rule02_GET_api_v1_bot_public_search",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            body["data"],
            {"total": int, "items": list},
            label="service-backed GET /api/v1/bot-public/search data",
        )
        assert_has_fields(
            body["data"]["items"][0],
            {
                "bot_id": str,
                "bot_name": str,
                "owner_id": str,
                "public": str,
                "friend_record_approval": dict,
            },
            label="service-backed GET /api/v1/bot-public/search items[0]",
        )

    def test_friend_record_and_my_friend_bots_use_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(
            world,
            bot_id=PUBLIC_BOT_ID,
            bot_name=PUBLIC_BOT_NAME,
            owner_id=PUBLIC_OWNER_ID,
            owner_name="PublicOwner",
            binding_id=301,
            public="1",
            friend_approval="0",
        )
        _seed_bot_friend(world)
        _assert_real_bot_public_service_bound(world)

        record_resp = gw_client.get(
            "/api/v1/bot-public/friend-record",
            params={"target_entity_id": PUBLIC_OWNER_ID, "target_bot_id": PUBLIC_BOT_ID},
        )
        record_body = record_resp.json()
        assert_success(record_body, "service-backed GET /api/v1/bot-public/friend-record")
        assert_api_response_contract(
            record_body,
            "service_backed_rule02_GET_api_v1_bot_public_friend_record",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            record_body["data"],
            {"id": int, "status": str, "bot_access_info": dict},
            label="service-backed GET /api/v1/bot-public/friend-record data",
        )

        friends_resp = gw_client.get(
            "/api/v1/bot-public/my-friend-bots",
            params={"search": "public-contract", "page": 1, "page_size": 20},
        )
        friends_body = friends_resp.json()
        assert_success(friends_body, "service-backed GET /api/v1/bot-public/my-friend-bots")
        assert_api_response_contract(
            friends_body,
            "service_backed_rule02_GET_api_v1_bot_public_my_friend_bots",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            friends_body["data"],
            {"total": int, "items": list},
            label="service-backed GET /api/v1/bot-public/my-friend-bots data",
        )
        assert_has_fields(
            friends_body["data"]["items"][0],
            {"id": int, "target_bot_id": str, "status": str, "bot": dict},
            label="service-backed GET /api/v1/bot-public/my-friend-bots items[0]",
        )

    def test_friend_request_approval_auto_path_uses_real_service_contract(
        self,
        gw_client,
        world,
        contract_snapshot_update,
    ):
        _seed_bot(
            world,
            bot_id=PUBLIC_BOT_ID,
            bot_name=PUBLIC_BOT_NAME,
            owner_id=PUBLIC_OWNER_ID,
            owner_name="PublicOwner",
            binding_id=302,
            public="1",
            friend_approval="0",
        )
        _assert_real_bot_public_service_bound(world)

        resp = gw_client.post(
            "/api/v1/bot-public/friend-request-approval",
            params={"bot_id": PUBLIC_BOT_ID, "owner_id": PUBLIC_OWNER_ID},
        )
        body = resp.json()

        assert_success(body, "service-backed POST /api/v1/bot-public/friend-request-approval")
        assert_api_response_contract(
            body,
            "service_backed_rule02_POST_api_v1_bot_public_friend_request_approval",
            update=contract_snapshot_update,
        )
        assert_has_fields(
            body["data"],
            {"success": bool, "auto_accepted": bool, "bot_friend_id": int, "message": str},
            label="service-backed POST /api/v1/bot-public/friend-request-approval data",
        )

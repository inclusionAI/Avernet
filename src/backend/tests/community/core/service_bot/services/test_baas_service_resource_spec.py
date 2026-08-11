"""Tests for service-bot sandbox resource_spec passthrough via ext.service_bot_config."""

from unittest.mock import MagicMock

from agentclaw.community.kernel.device_dto import ResourceSpecification

from agentclaw.community.core.service_bot.services.baas_service import BotDeployConfig


def test_deploy_config_to_dict_includes_resource_spec_with_disk():
    cfg = BotDeployConfig(resource_spec=ResourceSpecification(cpu=2, memory=4096, disk=20))
    d = cfg.to_dict()
    assert d["resource_spec"] == {"cpu": 2, "memory": 4096, "disk": 20}


def test_deploy_config_to_dict_resource_spec_without_disk():
    cfg = BotDeployConfig(resource_spec=ResourceSpecification(cpu=2, memory=4096))
    d = cfg.to_dict()
    assert d["resource_spec"] == {"cpu": 2, "memory": 4096}
    assert "disk" not in d["resource_spec"]


def test_deploy_config_to_dict_omits_resource_spec_when_none():
    cfg = BotDeployConfig()
    d = cfg.to_dict()
    assert "resource_spec" not in d


def test_deploy_config_to_dict_includes_docker_image():
    cfg = BotDeployConfig(docker_image="registry.example.com/aicoding:latest")
    d = cfg.to_dict()
    assert d["docker_image"] == "registry.example.com/aicoding:latest"


def _make_service():
    from agentclaw.community.core.service_bot.services.baas_service import BaasService
    return BaasService(
        baas_api_base="http://test",
        tenant="test",
        template_uuid="legacy-uuid",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=MagicMock(),
        general_http_client=MagicMock(),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
        personal_bot_template_uuid="TEMPLATE-poolab",
    )


def test_resolve_resource_spec_full():
    svc = _make_service()
    ext = {"service_bot_config": {"cpu": 2, "memory": 4096, "disk": 20}}
    spec = svc._resolve_service_bot_resource_spec(ext)
    assert spec is not None
    assert (spec.cpu, spec.memory, spec.disk) == (2, 4096, 20)


def test_resolve_resource_spec_without_disk():
    svc = _make_service()
    ext = {"service_bot_config": {"cpu": 2, "memory": 4096}}
    spec = svc._resolve_service_bot_resource_spec(ext)
    assert spec is not None
    assert (spec.cpu, spec.memory) == (2, 4096)
    assert spec.disk is None


def test_resolve_resource_spec_missing_cpu_returns_none():
    svc = _make_service()
    assert svc._resolve_service_bot_resource_spec({"service_bot_config": {"memory": 4096}}) is None


def test_resolve_resource_spec_missing_memory_returns_none():
    svc = _make_service()
    assert svc._resolve_service_bot_resource_spec({"service_bot_config": {"cpu": 2}}) is None


def test_resolve_resource_spec_invalid_cpu_returns_none():
    svc = _make_service()
    ext = {"service_bot_config": {"cpu": "abc", "memory": 4096}}
    assert svc._resolve_service_bot_resource_spec(ext) is None


def test_resolve_resource_spec_invalid_disk_keeps_cpu_memory():
    svc = _make_service()
    ext = {"service_bot_config": {"cpu": 2, "memory": 4096, "disk": "bad"}}
    spec = svc._resolve_service_bot_resource_spec(ext)
    assert spec is not None
    assert (spec.cpu, spec.memory) == (2, 4096)
    assert spec.disk is None


def test_resolve_resource_spec_no_ext_returns_none():
    svc = _make_service()
    assert svc._resolve_service_bot_resource_spec(None) is None
    assert svc._resolve_service_bot_resource_spec({}) is None
    assert svc._resolve_service_bot_resource_spec({"other": 1}) is None


def test_build_create_bot_payload_supports_auto_approve_and_extra_envs():
    svc = _make_service()
    svc._get_start_cmd = MagicMock(return_value="echo start")
    svc._get_destroy_cmd = MagicMock(return_value="echo destroy")
    svc._setup_directory = MagicMock(return_value=[])
    svc._setup_bot_storage = MagicMock(return_value=None)
    svc._build_outbound_operation_rule = MagicMock(return_value=None)
    svc._should_mount_home_dir_storage = MagicMock(return_value=False)

    payload = svc._build_create_bot_payload(
        bot={
            "bot_id": "B1",
            "bot_name": "personal-bot",
            "entity_id": "E1",
            "entity_type": "staff",
            "active_engine": "claude_code",
            "bot_type": "personal",
        },
        owner_id="U1",
        request_id="req-1234567890-abcdefghijklmno",
        device_count=1,
        migration_path="",
        auto_approve_publish=True,
        extra_envs={"BOT_TYPE": "personalCoding"},
    )

    assert payload["config"]["auto_approve_publish"] is True
    assert payload["config"]["deploy_config"]["envs"] == {
        "AGENTCLAW_ENGINE": "claude_code",
        "BOT_TYPE": "personalCoding",
    }


def test_build_create_bot_payload_auto_approves_by_default():
    svc = _make_service()
    svc._get_start_cmd = MagicMock(return_value="echo start")
    svc._get_destroy_cmd = MagicMock(return_value="echo destroy")
    svc._setup_directory = MagicMock(return_value=[])
    svc._setup_bot_storage = MagicMock(return_value=None)
    svc._build_outbound_operation_rule = MagicMock(return_value=None)
    svc._should_mount_home_dir_storage = MagicMock(return_value=False)

    payload = svc._build_create_bot_payload(
        bot={
            "bot_id": "B1",
            "bot_name": "service-bot",
            "entity_id": "E1",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "service",
        },
        owner_id="U1",
        request_id="req-1234567890-abcdefghijklmno",
        device_count=1,
        migration_path="",
    )

    assert payload["config"]["auto_approve_publish"] is True


def test_payload_includes_resource_spec_from_ext():
    svc = _make_service()
    svc._get_start_cmd = MagicMock(return_value="echo start")
    svc._get_destroy_cmd = MagicMock(return_value="echo destroy")
    svc._setup_directory = MagicMock(return_value=[])
    svc._setup_bot_storage = MagicMock(return_value=None)
    svc._build_outbound_operation_rule = MagicMock(return_value=None)
    svc._should_mount_home_dir_storage = MagicMock(return_value=False)

    bot = {
        "bot_id": "B1",
        "bot_name": "svc-bot",
        "entity_id": "E1",
        "entity_type": "staff",
        "active_engine": "openclaw",
        "bot_type": "service",
        "ext": {"service_bot_config": {"cpu": 2, "memory": 4096, "disk": 20}},
    }
    payload = svc._build_create_bot_payload(
        bot=bot,
        owner_id="U1",
        request_id="req-1234567890-abcdefghijklmno",
        device_count=1,
        migration_path="",
    )
    assert payload["config"]["deploy_config"]["resource_spec"] == {
        "cpu": 2,
        "memory": 4096,
        "disk": 20,
    }


def test_payload_omits_resource_spec_when_ext_absent():
    svc = _make_service()
    svc._get_start_cmd = MagicMock(return_value="echo start")
    svc._get_destroy_cmd = MagicMock(return_value="echo destroy")
    svc._setup_directory = MagicMock(return_value=[])
    svc._setup_bot_storage = MagicMock(return_value=None)
    svc._build_outbound_operation_rule = MagicMock(return_value=None)
    svc._should_mount_home_dir_storage = MagicMock(return_value=False)

    bot = {
        "bot_id": "B1",
        "bot_name": "svc-bot",
        "entity_id": "E1",
        "entity_type": "staff",
        "active_engine": "openclaw",
        "bot_type": "service",
    }
    payload = svc._build_create_bot_payload(
        bot=bot,
        owner_id="U1",
        request_id="req-1234567890-abcdefghijklmno",
        device_count=1,
        migration_path="",
    )
    assert "resource_spec" not in payload["config"]["deploy_config"]


def test_payload_maps_template_config_overrides_to_deploy_config():
    svc = _make_service()
    svc._get_start_cmd = MagicMock(return_value="echo start")
    svc._get_destroy_cmd = MagicMock(return_value="echo destroy")
    svc._setup_directory = MagicMock(return_value=[])
    svc._setup_bot_storage = MagicMock(return_value=None)
    svc._build_outbound_operation_rule = MagicMock(return_value=None)
    svc._should_mount_home_dir_storage = MagicMock(return_value=False)

    payload = svc._build_create_bot_payload(
        bot={
            "bot_id": "B1",
            "bot_name": "personal-bot",
            "entity_id": "E1",
            "entity_type": "staff",
            "active_engine": "aicoding",
            "bot_type": "personal",
            "ext": {"service_bot_config": {"cpu": 1, "memory": 1024}},
        },
        owner_id="U1",
        request_id="req-1234567890-abcdefghijklmno",
        device_count=1,
        migration_path="",
        extra_envs={"BOT_TYPE": "personalCoding"},
        template_config={
            "image": "registry.example.com/aicoding:latest",
            "resource_spec": {"cpu": 4, "memory": 8192, "disk": 100},
            "envs": {"AGENTCLAW_ENGINE": "custom", "USER_ENV": "yes"},
        },
    )

    svc._build_outbound_operation_rule.assert_called_once_with(
        "B1",
        "U1",
        "",
        extra_properties=None,
    )

    deploy_config = payload["config"]["deploy_config"]
    assert deploy_config["docker_image"] == "registry.example.com/aicoding:latest"
    assert deploy_config["resource_spec"] == {"cpu": 4, "memory": 8192, "disk": 100}
    assert deploy_config["envs"] == {
        "AGENTCLAW_ENGINE": "custom",
        "BOT_TYPE": "personalCoding",
        "USER_ENV": "yes",
    }


def test_payload_ignores_template_config_command_until_baas_has_field():
    svc = _make_service()
    svc._get_start_cmd = MagicMock(return_value="echo start")
    svc._get_destroy_cmd = MagicMock(return_value="echo destroy")
    svc._setup_directory = MagicMock(return_value=[])
    svc._setup_bot_storage = MagicMock(return_value=None)
    svc._build_outbound_operation_rule = MagicMock(return_value=None)
    svc._should_mount_home_dir_storage = MagicMock(return_value=False)

    payload = svc._build_create_bot_payload(
        bot={
            "bot_id": "B1",
            "bot_name": "personal-bot",
            "entity_id": "E1",
            "entity_type": "staff",
            "active_engine": "aicoding",
            "bot_type": "personal",
        },
        owner_id="U1",
        request_id="req-1234567890-abcdefghijklmno",
        device_count=1,
        migration_path="",
        template_config={"command": "python main.py"},
    )

    assert "command" not in payload["config"]["deploy_config"]


def test_read_only_skipped_for_personal_and_draft():
    svc = _make_service()
    svc._resolve_sandbox_provider = MagicMock()
    # personal online / service draft → editable → 不拼 set_read_only
    assert svc._get_set_read_only_rule(bot_id="b", owner_id="o", bot_type="personal", stage="online") == ""
    assert svc._get_set_read_only_rule(bot_id="b", owner_id="o", bot_type="service", stage="draft") == ""


def test_read_only_applied_for_service_online():
    from agentclaw.community.core.workspace.engine_sandbox import ReadOnlyRule
    svc = _make_service()
    prov = MagicMock()
    prov.get_base_path.return_value = "/home/admin"
    prov.get_default_read_only_rules.return_value = [ReadOnlyRule(path="x.json", rule_type="file")]
    svc._resolve_sandbox_provider = MagicMock(return_value=prov)
    svc._bot_repo = MagicMock()
    svc._bot_repo.get_by_id_and_owner.return_value = None
    out = svc._get_set_read_only_rule(bot_id="b", owner_id="o", bot_type="service", stage="online")
    assert "--set_read_only" in out  # online 才锁

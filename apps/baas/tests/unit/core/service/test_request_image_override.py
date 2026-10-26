"""Request-scoped image overrides at the helper and bot-operation boundaries.

Publish execution/consumer coverage lives in separate integration tests. These
unit tests exercise real bot-management methods with isolated dependencies.
"""

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.bot_manage import BotConfig, BotResponse, BotStatus
from secbaas.community.api.device_manage import DeployConfig, DeviceStatus
from secbaas.community.api.publish_manage import PublishType
from secbaas.community.core.service.bot_manage import (
    DefaultBotManagementService,
    merge_deploy_config,
)

OLD_IMAGE = "example/bot:previous"
NEW_IMAGE = "example/bot:requested"
REQUEST_ID = "request-image-test-0000000000000001"
OMITTED = object()


def _stored_deploy_config() -> DeployConfig:
    """Keep unrelated deployment fields observable instead of mocking them."""
    return DeployConfig(
        docker_image=OLD_IMAGE,
        docker_container_port=8080,
        envs={"KEEP": "stored"},
        ttl_in_minutes=1440,
        mount_points=[
            {
                "id": "mount-1",
                "remote_dir": "/shared",
                "local_dir": "/workspace/shared",
                "permission": "ro",
            }
        ],
        resource_spec={"cpu": 2, "memory": 4096, "disk": 20},
        storage={"type": "nas", "path": "/workspace/data"},
        arca_metadata={"purpose": "unit-test"},
        before_destroy_cmd_hook="echo before-destroy",
        after_create_cmd_hook="echo after-create",
        machine_id="machine-1",
        user_id="user-1",
        engine_type="openclaw",
    )


HELPER_OVERRIDES = [
    pytest.param(None, None, id="no-override"),
    pytest.param({}, None, id="empty-dict"),
    pytest.param(DeployConfig(), None, id="empty-model"),
    pytest.param({"docker_image": None}, None, id="dict-null-image"),
    pytest.param(DeployConfig(docker_image=None), None, id="model-null-image"),
    pytest.param({"envs": {"NEW": "value"}}, None, id="dict-env-only"),
    pytest.param(DeployConfig(envs={"NEW": "value"}), None, id="model-env-only"),
    pytest.param({"docker_image": NEW_IMAGE}, NEW_IMAGE, id="dict-explicit-image"),
    pytest.param(
        DeployConfig(docker_image=NEW_IMAGE), NEW_IMAGE, id="model-explicit-image"
    ),
]


@pytest.mark.parametrize("with_current", [False, True], ids=["no-current", "stored"])
@pytest.mark.parametrize(("override", "expected_image"), HELPER_OVERRIDES)
def test_merge_image_is_request_scoped_without_mutating_inputs(
    with_current, override, expected_image
):
    current = _stored_deploy_config() if with_current else None
    override = deepcopy(override)
    before_current = current.model_dump() if current is not None else None
    before_override = deepcopy(override)

    result = merge_deploy_config(current, override)

    assert (current.model_dump() if current is not None else None) == before_current
    assert override == before_override
    if current is None and override is None:
        # Compatibility: do not create a deployment config where none existed.
        assert result is None
        return

    assert isinstance(result, DeployConfig)
    expected = deepcopy(before_current) if before_current is not None else {}
    expected["docker_image"] = expected_image
    if isinstance(override, dict) and "envs" in override:
        expected["envs"] = override["envs"]
    elif isinstance(override, DeployConfig) and override.envs is not None:
        expected["envs"] = override.envs
    assert result.model_dump() == DeployConfig.model_validate(expected).model_dump()
    assert result is not current
    assert result is not override


@pytest.mark.parametrize("override", [None, {}, {"docker_image": None}])
def test_merge_clear_preserves_custom_hook_timeouts(override):
    current = _stored_deploy_config()
    current.before_destroy_hook_wait_seconds = 17
    current.after_create_hook_wait_seconds = 29
    before = current.model_dump()

    result = merge_deploy_config(current, override)

    assert result is not None
    assert result.model_dump() == {**before, "docker_image": None}
    assert current.model_dump() == before


CONFIG_CASES = [
    pytest.param(OMITTED, None, id="config-omitted"),
    pytest.param(None, None, id="config-null"),
    pytest.param({}, None, id="config-empty"),
    pytest.param({"share_policy": {"mode": "private"}}, None, id="no-deploy-config"),
    pytest.param({"deploy_config": None}, None, id="deploy-config-null"),
    pytest.param({"deploy_config": {}}, None, id="deploy-config-empty"),
    pytest.param(
        {"deploy_config": {"docker_image": None}}, None, id="docker-image-null"
    ),
    pytest.param({"deploy_config": {"envs": {"NEW": "value"}}}, None, id="env-only"),
    pytest.param(
        {"deploy_config": {"docker_image": NEW_IMAGE}},
        NEW_IMAGE,
        id="explicit-image",
    ),
]


@pytest.fixture
def bot_context(monkeypatch):
    """Construct the real service without importing the oversized legacy tests."""
    deploy = _stored_deploy_config()
    stored_config = BotConfig(
        deploy_config=deploy,
        entity_id="entity-1",
        entity_type="staff",
        share_policy={"mode": "shared"},
        publish_max_retry_times=2,
    )
    bot = BotResponse(
        id=41,
        bot_uuid="BOT-request-image-test",
        tenant="tenant-test",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="tester",
        modifier="tester",
        status=BotStatus.ACTIVE.value,
        name="Image override test",
        description=None,
        template_uuid="template-current",
        replica_desired=1,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=datetime(2026, 1, 1),
        gmt_modified=datetime(2026, 1, 1),
        config=stored_config,
    )
    record = SimpleNamespace(
        id=bot.id,
        name=bot.name,
        status=bot.status,
        template_uuid=bot.template_uuid,
        extra_config=stored_config.model_dump(),
    )
    device = SimpleNamespace(
        id=51, device_uuid="device-1", status=DeviceStatus.ACTIVE.value
    )
    bot_repo = MagicMock()
    bot_repo.update_bot.return_value = 1
    device_repo = MagicMock()
    device_repo.list_by_bot_id.return_value = [device]
    publish_service = MagicMock()
    publish_service.create_publish = AsyncMock(return_value=SimpleNamespace(id=61))
    bot_service = MagicMock()
    bot_service.get_bot = AsyncMock(return_value=bot)
    system_config_repo = MagicMock()
    system_config_repo.get_by_env_and_key.return_value = None
    service = DefaultBotManagementService(
        bot_repo=bot_repo,
        device_repo=device_repo,
        system_config_repo=system_config_repo,
        publish_service=publish_service,
        bot_service=bot_service,
        health_checker=MagicMock(),
    )
    service.get_bot = AsyncMock(return_value=bot)
    service._get_bot_record_by_uuid = MagicMock(return_value=record)
    service._get_operational_bot_record_by_uuid_for_update = MagicMock(
        return_value=record
    )
    monkeypatch.setattr(
        "secbaas.community.core.service.bot_manage._bot_management_service.get_current_env",
        lambda: "dev",
    )
    return SimpleNamespace(
        service=service,
        bot=bot,
        record=record,
        bot_repo=bot_repo,
        publish_service=publish_service,
    )


async def _invoke_operation(context, operation, config_payload):
    kwargs = {
        "tenant": context.bot.tenant,
        "bot_uuid": context.bot.bot_uuid,
        "operator": "tester",
        "request_id": REQUEST_ID,
    }
    config = (
        BotConfig.model_validate(deepcopy(config_payload))
        if isinstance(config_payload, dict)
        else config_payload
    )
    if config is not OMITTED:
        kwargs["config" if operation == "update_devices" else "bot_config"] = config
    before_config = config.model_dump() if isinstance(config, BotConfig) else None
    if operation == "scale_bot":
        kwargs["target_count"] = 2
    elif operation == "update_bot":
        # Omitted config is a real deployment only when a template is selected.
        kwargs["template_uuid"] = "template-requested"
    else:
        kwargs["device_uuids"] = ["device-1"]

    response = await getattr(context.service, operation)(**kwargs)

    assert response.publish_id == 61
    if isinstance(config, BotConfig):
        assert config.model_dump() == before_config
    return context.publish_service.create_publish.await_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["scale_bot", "update_bot", "update_devices"])
@pytest.mark.parametrize(("config_payload", "expected_image"), CONFIG_CASES)
async def test_operations_resolve_image_from_this_request_only(
    bot_context, operation, config_payload, expected_image
):
    before_record = deepcopy(bot_context.record.extra_config)
    before_bot = bot_context.bot.model_dump()
    before_payload = (
        deepcopy(config_payload) if isinstance(config_payload, dict) else None
    )

    call = await _invoke_operation(bot_context, operation, config_payload)

    bot_context.publish_service.create_publish.assert_awaited_once()
    assert call["tenant"] == bot_context.bot.tenant
    assert call["bot_id"] == bot_context.bot.id
    assert call["request_id"] == REQUEST_ID
    assert (
        call["publish_type"]
        == {
            "scale_bot": PublishType.SCALE_UP,
            "update_bot": PublishType.UPDATE,
            "update_devices": PublishType.UPDATE_DEVICE,
        }[operation]
    )
    deploy = call["config"].deploy_config
    assert isinstance(deploy, DeployConfig)
    expected = deepcopy(before_record["deploy_config"])
    expected["docker_image"] = expected_image
    if isinstance(config_payload, dict):
        patch = config_payload.get("deploy_config")
        if isinstance(patch, dict) and "envs" in patch:
            expected["envs"] = patch["envs"]
        assert config_payload == before_payload
    assert deploy.model_dump() == expected
    assert bot_context.record.extra_config == before_record
    assert bot_context.bot.model_dump() == before_bot

    if operation == "update_devices" and isinstance(config_payload, dict):
        # The existing producer persists config-bearing updates before publish.
        bot_context.bot_repo.update_bot.assert_called_once()
        persisted = bot_context.bot_repo.update_bot.call_args.kwargs
        assert persisted["tenant"] == bot_context.bot.tenant
        assert persisted["env"] == "dev"
        assert persisted["extra_config"]["deploy_config"] == expected
    elif operation in ("scale_bot", "update_bot"):
        # Do not rewrite the source Bot or historical configuration snapshots.
        bot_context.bot_repo.update_bot.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["scale_bot", "update_bot", "update_devices"])
@pytest.mark.parametrize(
    "config_payload",
    [pytest.param(OMITTED, id="omitted"), pytest.param(None, id="null")],
)
@pytest.mark.parametrize("stored_extra", [{}, {"deploy_config": None}])
async def test_operations_do_not_invent_an_absent_deployment_config(
    bot_context, operation, config_payload, stored_extra
):
    bot_context.record.extra_config = deepcopy(stored_extra)

    call = await _invoke_operation(bot_context, operation, config_payload)

    assert call["config"].deploy_config is None
    assert bot_context.record.extra_config == stored_extra


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["scale_bot", "update_bot", "update_devices"])
@pytest.mark.parametrize(
    "config_payload",
    [
        pytest.param({"deploy_config": {}}, id="empty-deploy-config"),
        pytest.param(
            {"deploy_config": {"docker_image": NEW_IMAGE}}, id="explicit-image"
        ),
    ],
)
async def test_explicit_deployment_config_is_kept_without_a_previous_one(
    bot_context, operation, config_payload
):
    bot_context.record.extra_config = {}

    call = await _invoke_operation(bot_context, operation, config_payload)

    deploy = call["config"].deploy_config
    assert isinstance(deploy, DeployConfig)
    assert deploy.docker_image == config_payload["deploy_config"].get("docker_image")
    assert bot_context.record.extra_config == {}


@pytest.mark.asyncio
async def test_metadata_only_update_does_not_start_an_image_reset_publish(bot_context):
    before = deepcopy(bot_context.record.extra_config)

    response = await bot_context.service.update_bot(
        tenant=bot_context.bot.tenant,
        bot_uuid=bot_context.bot.bot_uuid,
        operator="tester",
        bot_name="New display name",
    )

    assert response.publish_id is None
    bot_context.publish_service.create_publish.assert_not_awaited()
    assert bot_context.record.extra_config == before
    bot_context.bot_repo.update_bot.assert_called_once_with(
        bot_id=bot_context.bot.id,
        tenant=bot_context.bot.tenant,
        env="dev",
        modifier="tester",
        name="New display name",
    )

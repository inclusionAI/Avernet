"""Real create-entry scope and NAS/UPFS safety boundaries for phase one."""

from unittest.mock import MagicMock, call

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from tests.community.core.common_config.test_bot_storage_policy import (
    SCOPE,
    allocate,
    initialize,
    latest_storage,
    make_baas,
    storage as _storage_fixture,
)
from tests.community.core.bot_management.services.test_bot_service_create_publish_guard import (
    _make_service,
)
from agentclaw.community.core.common_config.bot_config_service import STORAGE_POLICY
from tests.community.core.service_bot.services.deploy._noop_storage_policy import (
    NoopStoragePolicy,
)
from agentclaw.community.plugin_api.models import BotCommonConfig


storage = _storage_fixture


@pytest.mark.parametrize("bot_type", ["personal", "service"])
def test_real_create_calls_prepare_and_keeps_original_apply(bot_type):
    """BotService always delegates the scope decision to the policy component."""
    svc, _ = _make_service()
    device = svc._device_service_provider()
    svc._bot_storage_policy = MagicMock()
    events = []
    original_result = device.apply_device.return_value
    svc._bot_storage_policy.prepare_bot_storage_policy.side_effect = lambda **kw: (
        events.append("prepare") or {"device_provider": "baas"}
    )
    device.apply_device.side_effect = lambda **kw: (
        events.append("apply") or original_result
    )
    svc.create_bot(
        user_id="u1",
        nick_name="nick",
        bot_id="b1",
        bot_name="storage-test",
        engine_type="openclaw",
        bot_type=bot_type,
    )
    assert events == ["prepare", "apply"]
    kwargs = device.apply_device.call_args.kwargs
    assert kwargs["owner_id"] == "u1"
    assert kwargs["device_provider"] == "baas"


def test_real_create_stops_before_allocation_if_policy_preparation_fails():
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotServiceError,
    )

    svc, _ = _make_service()
    device = svc._device_service_provider()
    svc._bot_storage_policy = MagicMock()
    svc._bot_storage_policy.prepare_bot_storage_policy.side_effect = RuntimeError(
        "policy write failed"
    )
    with pytest.raises(BotServiceError):
        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_id="b1",
            bot_name="storage-test",
            engine_type="openclaw",
            bot_type="personal",
        )
    device.apply_device.assert_not_called()
    svc._repository.soft_delete_by_owner.assert_called_once_with("b1", "u1")


@pytest.mark.parametrize("with_table", [True, False])
def test_switch_off_payload_only_changes_shared_quota_without_writing_policy(
    storage, with_table
):
    db, repo, _, common, _ = storage
    if not with_table:
        BotCommonConfig.__table__.drop(db.engine)
    common.get_config.return_value["enable"] = "0"
    baas = make_baas(storage)
    allocate(baas)
    actual = baas.post_bots_api.call_args.kwargs["payload"]
    assert latest_storage(baas)["type"] == "nas"
    baas._get_bots_api.assert_not_called()
    assert db.transactions == 0
    assert repo.get(**SCOPE, config_key=STORAGE_POLICY) is None
    # Same dependencies: compare with the old allocation path and no policy service.
    baas._deploy_composer._storage_policy = NoopStoragePolicy()
    allocate(baas, new_bot=False)
    legacy = baas.post_bots_api.call_args.kwargs["payload"]
    assert legacy["config"]["deploy_config"]["storage"]["quota"] == "1Gi"
    assert actual["config"]["deploy_config"]["storage"]["quota"] == "1G"
    # Shared quota is intentional for NAS too; every other field stays unchanged.
    legacy["config"]["deploy_config"]["storage"]["quota"] = "1G"
    assert actual == legacy


def test_switch_on_missing_table_cannot_create_upfs_without_policy(storage):
    db = storage[0]
    BotCommonConfig.__table__.drop(db.engine)
    baas = make_baas(storage)
    with pytest.raises(OperationalError):
        allocate(baas)
    baas.post_bots_api.assert_not_called()


def test_switch_off_keeps_existing_upfs_for_create_retry_and_recovery(storage):
    initialize(storage[-1])
    storage[3].get_config.return_value["enable"] = "0"
    storage[3].get_config.reset_mock()
    baas = make_baas(storage)
    allocate(baas)
    assert latest_storage(baas)["type"] == "upfs"
    allocate(baas, new_bot=False)
    assert latest_storage(baas)["type"] == "upfs"
    assert (
        storage[3].get_config.call_args_list
        == [call(business_code="bot_storage", param_code="storage", env="pre")] * 2
    )
    baas._get_bots_api.assert_not_called()


@pytest.mark.parametrize("new_bot", [True, False])
def test_unreadable_saved_policy_blocks_before_container_request(storage, new_bot):
    initialize(storage[-1])
    storage[2].get_config = MagicMock(side_effect=RuntimeError("database unavailable"))
    baas = make_baas(storage)
    with pytest.raises(RuntimeError, match="database unavailable"):
        allocate(baas, new_bot=new_bot)
    baas.post_bots_api.assert_not_called()


@pytest.mark.parametrize(
    "error_type,code",
    [(ProgrammingError, 1146), (OperationalError, 2006), (ProgrammingError, 1142)],
)
def test_only_missing_table_is_compatible_not_database_or_permission_failure(
    storage, error_type, code
):
    db, repo, _, _, _ = storage
    db.orm_session = MagicMock(
        side_effect=error_type("SELECT", {}, Exception(code, "test"))
    )
    if code == 1146:
        assert repo.get(**SCOPE, config_key=STORAGE_POLICY) is None
    else:
        with pytest.raises(error_type):
            repo.get(**SCOPE, config_key=STORAGE_POLICY)


@pytest.mark.parametrize("policy", [None, {"storage_type": "upfs", "source": "manual"}])
def test_service_draft_restart_reads_policy_without_rollout(storage, policy):
    """Service draft restart reuses the saved policy; rollout stays creation-only."""
    baas = make_baas(storage)
    if policy is not None:
        storage[2].set_config(**SCOPE, config_key=STORAGE_POLICY, value=policy)

    def common_config(**kw):
        assert kw["param_code"] != "upfs_rollout", "restart must not read rollout"
        return {"param_value": {"quota": "1G"}}

    storage[3].get_config.side_effect = common_config
    storage[-1].initialize_for_template = MagicMock(
        side_effect=AssertionError("restart must not initialize")
    )
    allocate(baas, bot_type="service", new_bot=False)
    assert latest_storage(baas)["type"] == (policy["storage_type"] if policy else "nas")
    assert latest_storage(baas)["quota"] == "1G"
    baas._get_bots_api.assert_not_called()
    storage[-1].initialize_for_template.assert_not_called()


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("upfs_volume_id_pre", "volume-pre", "upfs"),
        ("upfs_volume_id_pre", None, "nas"),
        ("upfs_volume_id_pre", " ", "nas"),
        ("upfs_volume_id_pre", " volume ", "nas"),
        ("upfs_volume_id_pre", 123, "nas"),
        ("type", "LOCAL", "nas"),
    ],
)
def test_existing_template_api_checks_current_environment(
    storage, field, value, expected
):
    baas = make_baas(storage)
    config = baas._get_bots_api.return_value["config"]
    config["upfs_volume_id_prod"] = "different-env-volume"
    config[field] = value
    allocate(baas)
    assert latest_storage(baas)["type"] == expected
    baas._get_bots_api.assert_called_once_with(
        path="/api/v1/device-templates/TEMPLATE-test",
        action="get_device_template",
        log_response=False,
    )


@pytest.mark.parametrize(
    "field,value",
    [("template_uuid", "OTHER"), ("status", "CREATED"), ("type", "LOCAL")],
)
def test_template_identity_and_status_required(storage, field, value):
    baas = make_baas(storage)
    baas._get_bots_api.return_value[field] = value
    allocate(baas)
    assert latest_storage(baas)["type"] == "nas"
    assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) is None


@pytest.mark.parametrize("code", [0, 1])
def test_template_response_and_error_message_are_not_logged(storage, code):
    from unittest.mock import patch
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

    baas = make_baas(storage)
    baas._tenant = "tenant"
    template = baas._get_bots_api.return_value
    template["config"]["api_key"] = "<test-only-template-credential>"
    baas._http = MagicMock()
    baas._http.get.return_value.json.return_value = {
        "code": code,
        "data": template,
        "message": "<test-only-error-credential>",
    }
    baas._get_bots_api = BaasService._get_bots_api.__get__(baas)
    with patch(
        "agentclaw.community.core.service_bot.services.baas_service.logger"
    ) as log:
        allocate(baas)
    assert latest_storage(baas)["type"] == ("upfs" if code == 0 else "nas")
    assert "<test-only-template-credential>" not in str(log.mock_calls)
    assert "<test-only-error-credential>" not in str(log.mock_calls)
    policy = storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY)
    assert "volume" not in str(policy) and "credential" not in str(policy)
    baas._http.get.assert_called_once_with(
        "/api/v1/device-templates/TEMPLATE-test",
        params={"tenant": "tenant"},
        timeout=30.0,
    )


@pytest.mark.parametrize("env", ["pre", "prod", "dev", "test", ""])
@pytest.mark.parametrize("specific", [None, "", "volume-specific"])
def test_backend_readiness_uses_default_volume(storage, env, specific):
    baas = make_baas(storage)
    config = baas._get_bots_api.return_value["config"]
    config["upfs_volume_id"] = "volume-default"
    config["upfs_volume_id_pre"] = specific
    config["upfs_volume_id_prod"] = specific
    readiness = []

    def initialize_policy(**kwargs):
        readiness.append(kwargs["upfs_ready"]())
        return MagicMock(storage_type="upfs")

    storage[-1].initialize = MagicMock(side_effect=initialize_policy)
    storage[-1].initialize_for_template(
        bot_id="b1",
        entity_id="staff_10001",
        env=env,
        engine="openclaw",
        user_id="10001",
        template_uuid="TEMPLATE-test",
    )
    assert readiness == [True]


def test_device_services_do_not_expose_storage_business():
    from agentclaw.community.core.devices.services.device_service import DeviceService
    from agentclaw.community.core.devices.services.device_service_router import (
        DeviceServiceRouter,
    )
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

    for cls in (DeviceService, DeviceServiceRouter, BaasService):
        assert not hasattr(cls, "prepare_bot_storage_policy")
        assert not hasattr(cls, "initialize_bot_storage")


def test_new_storage_services_are_wired_in_di(test_injector):
    from agentclaw.community.core.common_config.bot_config_service import (
        BotCommonConfigService,
        BotStoragePolicyService,
    )
    from agentclaw.community.core.common_config.bot_config_protocol import (
        BotCommonConfigServiceProtocol,
    )

    assert isinstance(
        test_injector.get(BotCommonConfigServiceProtocol), BotCommonConfigService
    )
    assert isinstance(
        test_injector.get(BotStoragePolicyService), BotStoragePolicyService
    )


def test_baas_provider_is_pinned_and_router_skips_redecide(storage):
    from tests.community.core.devices.services.test_device_service_router import (
        _make_router,
        _make_operator,
    )
    from types import SimpleNamespace

    router, _, _, _ = _make_router(is_local=False)
    policy = storage[-1]
    policy._select_provider.return_value = "baas"
    policy._resolve_template.return_value = SimpleNamespace(
        template_uuid="TEMPLATE-test"
    )
    kwargs = dict(
        apply_reason="create",
        entity_id="team-1",
        entity_type="team",
        operator=_make_operator(),
        bot_id="b1",
        bot_type="personal",
        template_config={"template_uid": "default"},
    )
    prepared = policy.prepare_bot_storage_policy(**kwargs)
    assert prepared["device_provider"] == "baas"
    service = router._providers["baas"]
    service.apply_device.assert_not_called()
    router._get_provider_for_new_device = MagicMock(
        side_effect=AssertionError("already selected")
    )
    router.apply_device(**(kwargs | prepared))
    policy._select_provider.assert_called_once()
    router._get_provider_for_new_device.assert_not_called()
    service.apply_device.assert_called_once()


def test_non_baas_provider_is_not_pinned_and_router_routes_normally(storage):
    """Singlebox/test boots may inject a different policy than the router owns.

    Pinning an unregistered provider there broke demo bot creation; non-BaaS
    results must fall back to the router's original rollout path.
    """
    from tests.community.core.devices.services.test_device_service_router import (
        _make_router,
        _make_operator,
    )

    router, _, _, _ = _make_router(is_local=False)
    policy = storage[-1]
    policy._select_provider.return_value = "arca"
    kwargs = dict(
        apply_reason="create",
        entity_id="team-1",
        entity_type="team",
        operator=_make_operator(),
        bot_id="b1",
        bot_type="personal",
        template_config={"template_uid": "default"},
    )
    prepared = policy.prepare_bot_storage_policy(**kwargs)
    assert "device_provider" not in prepared
    policy._resolve_template.assert_not_called()
    storage[3].get_config.assert_not_called()
    arca = router._providers["arca"]
    router.apply_device(**(kwargs | prepared))
    arca.apply_device.assert_called_once()


@pytest.mark.parametrize("runtime", ["managed", "ack"])
def test_composition_root_selects_storage_policy_support(runtime):
    from inspect import signature

    from agentclaw.community.di import config as cfg
    from agentclaw.community.di.modules.service_bot_module import ServiceBotModule
    from agentclaw.community.kernel.deploy_runtime import DeployRuntime

    method = ServiceBotModule.deploy_config_composer
    kwargs = {
        name: MagicMock() for name in signature(method).parameters if name != "self"
    }
    kwargs["deploy_runtime"] = cfg.DeployRuntimeConfig(DeployRuntime(runtime))
    service = method(ServiceBotModule(), **kwargs)
    if runtime == "managed":
        assert service._storage_policy is kwargs["storage_policy"]
    else:
        assert not hasattr(service, "_storage_policy")


def test_di_storage_template_reader_uses_baas_query_without_cycle(test_injector):
    from unittest.mock import patch
    from agentclaw.community.core.common_config.bot_config_protocol import (
        BotStoragePolicyProtocol,
    )
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

    response = {"template_uuid": "TEMPLATE-query", "status": "ONLINE"}
    with patch.object(BaasService, "_get_bots_api", return_value=response) as query:
        policy = test_injector.get(BotStoragePolicyProtocol)
        assert policy._get_template("TEMPLATE-query") == response
    query.assert_called_once_with(
        path="/api/v1/device-templates/TEMPLATE-query",
        action="get_device_template",
        log_response=False,
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_real_bot_creation_uses_business_policy_before_original_router(storage, enabled):
    from tests.community.core.devices.services.test_baas_device_service import _make_service as make_device
    from tests.community.core.devices.services.test_device_service_router import _make_router

    storage[3].get_config.return_value["enable"] = "1" if enabled else "0"
    baas = make_baas(storage)
    device = make_device(baas_service=baas)
    device._get_storage_mode = MagicMock(return_value="oss")
    device._validate_and_generate_device_id = MagicMock(return_value=("dev1", "b1", None))
    device._after_binding_persisted = MagicMock(return_value=True)
    storage[-1]._resolve_template = device._template_resolver.resolve_template
    router, _, _, _ = _make_router(is_local=False)
    router._providers["baas"] = device
    router._get_provider_for_new_device = MagicMock(side_effect=AssertionError("provider already chosen"))
    svc, _ = _make_service()
    svc._device_service_provider = lambda: router
    svc._bot_storage_policy = storage[-1]
    svc._attach_template_uid_context = MagicMock(return_value={"template_uid": "default_template"})
    svc.create_bot(
        user_id="10001", entity_id=SCOPE["entity_id"], bot_id="b1", nick_name="test",
        bot_name="storage-entry", engine_type="openclaw", bot_type="personal",
    )
    assert latest_storage(baas)["type"] == ("upfs" if enabled else "nas")
    assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) == (
        {"storage_type": "upfs", "source": "rollout"} if enabled else None
    )
    storage[-1]._select_provider.assert_called_once()
    router._get_provider_for_new_device.assert_not_called()
    device._template_resolver.resolve_template.assert_called_once()
    assert "_prepared_bot_creation" not in str(baas.post_bots_api.call_args)

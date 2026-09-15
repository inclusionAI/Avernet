"""Phase-one storage contracts using real SQLite persistence and mocked BaaS."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.common_config.bot_config_service import (
    BotCommonConfigService,
    BotStoragePolicyService,
    STORAGE_POLICY,
    _should_rollout_upfs as should_rollout_upfs,
)
from agentclaw.community.core.repository.implementations.config.bot_common_config import (
    BotCommonConfigRepository,
)
from agentclaw.community.plugin_api.models import BotCommonConfig

SCOPE = dict(bot_id="b1", entity_id="staff_10001", env="pre")


class Database:
    def __init__(self, path):
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 10})
        BotCommonConfig.__table__.create(self.engine)
        self.factory = sessionmaker(self.engine)
        self.transactions = 0

    @contextmanager
    def orm_session(self):
        with self.factory.begin() as session:
            yield session

    @contextmanager
    def transactional_orm_session(self):
        self.transactions += 1
        with self.orm_session() as session:
            yield session


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.utils.env_utils.get_current_env", lambda: "pre"
    )
    db = Database(tmp_path / "storage.db")
    repo = BotCommonConfigRepository(db)
    config = BotCommonConfigService(repo)
    common = MagicMock()
    common.get_config.return_value = {"enable": "1", "param_value": {"allow_all": True}}
    service = BotStoragePolicyService(repo, config, common)
    yield db, repo, config, common, service
    db.engine.dispose()


def initialize(service, ready=True, **overrides):
    kwargs = dict(**SCOPE, engine="openclaw", user_id="10001", upfs_ready=lambda: ready)
    kwargs.update(overrides)
    return service.initialize(**kwargs)


@pytest.mark.parametrize("enable", [None, "0", 0, True, False, 2, "true"])
def test_master_switch_wins(enable):
    decision = should_rollout_upfs(
        config={"enable": enable, "param_value": {"allow_all": True}},
        engine="openclaw",
        user_id="10001",
    )
    assert not decision.enabled


@pytest.mark.parametrize("user", [None, "", "   "])
def test_global_all_does_not_require_user(user):
    assert should_rollout_upfs(
        config={"enable": 1, "param_value": {"allow_all": True}},
        engine="unknown",
        user_id=user,
    ).enabled


@pytest.mark.parametrize(
    "rule,user,enabled,reason",
    [
        ({"allow_all_users": True}, "10009", True, "allow_all_users"),
        ({"allow_all_users": True}, "", False, "missing_user_id"),
        ({"user_tail_digit_max": 3}, "10003", True, "user_tail_digit"),
        ({"user_tail_digit_max": 0}, "10000", True, "user_tail_digit"),
        ({"user_tail_digit_max": 9}, "10009", True, "user_tail_digit"),
        ({"user_tail_digit_max": 3}, "10004", False, "rule_not_matched"),
        ({"user_tail_digit_max": -50}, "10000", False, "rule_not_matched"),
        ({"user_tail_digit_max": 3}, "abc²", False, "rule_not_matched"),
        ({"user_tail_digit_max": 3}, "staff-x", False, "rule_not_matched"),
        ({"allow_user_groups": ["10004"]}, "10004", True, "allow_user"),
        (
            {"user_tail_digit_max": 3, "allow_user_groups": ["10004"]},
            "10004",
            True,
            "allow_user",
        ),
        (
            {"user_tail_digit_max": 3, "allow_user_groups": ["10003"]},
            "10003",
            True,
            "user_tail_digit",
        ),
        ({"user_tail_digit_max": True}, "10001", False, "rule_not_matched"),
        (
            {"user_tail_digit_max": 10, "allow_all_users": True},
            "10001",
            False,
            "rule_not_matched",
        ),
        ({"user_tail_digit_max": "3"}, "10001", False, "rule_not_matched"),
        ({"allow_user_groups": "10001"}, "10001", False, "rule_not_matched"),
        ({"allow_user_groups": [10001]}, "10001", False, "rule_not_matched"),
    ],
)
def test_user_rules(rule, user, enabled, reason):
    decision = should_rollout_upfs(
        config={
            "enable": "1",
            "param_value": {"rules": [dict(engine_bucket="openclaw", **rule)]},
        },
        engine="openclaw",
        user_id=user,
    )
    assert (decision.enabled, decision.reason) == (enabled, reason)


@pytest.mark.parametrize(
    "value", [None, "bad", [], {"allow_all": 1}, {"rules": "bad"}, {}, {"rules": []}]
)
def test_invalid_and_empty_configs_do_not_rollout(value):
    assert not should_rollout_upfs(
        config={"enable": "1", "param_value": value}, engine="openclaw", user_id="10001"
    ).enabled


def test_engine_matching_and_or_rules():
    config = {
        "enable": "1",
        "param_value": {
            "rules": [
                {"engine_bucket": "aicoding", "allow_all_users": True},
                {"engine_bucket": "openclaw", "allow_user_groups": ["10002"]},
                {"engine_bucket": "openclaw", "allow_user_groups": ["10001"]},
            ]
        },
    }
    assert should_rollout_upfs(
        config=config, engine="openclaw", user_id="10001"
    ).enabled
    assert not should_rollout_upfs(
        config=config, engine="claude_code", user_id="10001"
    ).enabled


def test_json_service_scoping_upsert_and_soft_delete(storage):
    db, repo, config, _, _ = storage
    config.set_config(**SCOPE, config_key="other", value={"a": [1]})
    config.set_config(**SCOPE, config_key="other", value={"a": [2]})
    assert config.get_config(**SCOPE, config_key="other") == {"a": [2]}
    for key in SCOPE:
        scope = dict(SCOPE, **{key: "different"})
        assert config.get_config(**scope, config_key="other") is None
    with db.orm_session() as session:
        session.query(BotCommonConfig).filter_by(**SCOPE).update({"is_delete": 1})
    assert repo.get(**SCOPE, config_key="other") is None
    config.set_config(**SCOPE, config_key="other", value=False)
    assert config.get_config(**SCOPE, config_key="other") is False


def test_upfs_persisted_before_retry_and_never_rechecks(storage):
    db, repo, config, common, service = storage
    policy = initialize(service)
    assert policy.storage_type == "upfs"
    assert config.get_config(**SCOPE, config_key=STORAGE_POLICY) == {
        "storage_type": "upfs",
        "source": "rollout",
    }
    assert db.transactions == 1
    common.get_config.side_effect = AssertionError("must not rerun rollout")
    ready = MagicMock(side_effect=AssertionError("must not query template"))
    assert initialize(service, upfs_ready=ready) == policy
    ready.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["disabled", "volume_missing", "capability_error", "config_error"]
)
def test_nas_never_writes_policy(storage, failure):
    db, repo, _, common, service = storage
    ready = MagicMock(return_value=True)
    if failure == "disabled":
        common.get_config.return_value["enable"] = "0"
    elif failure == "volume_missing":
        ready.return_value = False
    elif failure == "capability_error":
        ready.side_effect = RuntimeError("timeout")
    else:
        common.get_config.side_effect = RuntimeError("read failed")
    assert initialize(service, upfs_ready=ready).storage_type == "nas"
    assert repo.get(**SCOPE, config_key=STORAGE_POLICY) is None
    assert db.transactions == 0
    if failure in {"disabled", "config_error"}:
        ready.assert_not_called()


@pytest.mark.parametrize("value", ["bad-json", "[]", '{"storage_type":"bad"}'])
def test_invalid_policy_never_silently_downgrades(storage, value):
    _, repo, _, _, service = storage
    repo.put(**SCOPE, config_key=STORAGE_POLICY, config_value=value)
    with pytest.raises(ValueError):
        service._resolve_storage_policy(**SCOPE)
    with pytest.raises(ValueError):
        initialize(service)


def test_policy_read_exception_never_silently_downgrades(storage):
    _, _, config, _, service = storage
    config.get_config = MagicMock(side_effect=RuntimeError("read failed"))
    with pytest.raises(RuntimeError, match="read failed"):
        service._resolve_storage_policy(**SCOPE)


def test_write_failure_propagates_without_partial_policy(storage):
    _, repo, _, _, service = storage
    from unittest.mock import patch

    from sqlalchemy.orm import Session

    with patch.object(Session, "flush", side_effect=RuntimeError("write failed")):
        with pytest.raises(RuntimeError, match="write failed"):
            repo.initialize_once(
                **SCOPE, config_key=STORAGE_POLICY, config_value='{"storage_type":"upfs"}'
            )
    assert repo.get(**SCOPE, config_key=STORAGE_POLICY) is None
    repo.initialize_once = MagicMock(side_effect=RuntimeError("write failed"))
    with pytest.raises(RuntimeError, match="write failed"):
        initialize(service)


def test_concurrent_upfs_creations_commit_one_policy(storage):
    _, repo, _, _, service = storage
    ready = MagicMock(return_value=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        policies = list(
            pool.map(lambda _: initialize(service, upfs_ready=ready), range(6))
        )
    assert all(p.storage_type == "upfs" for p in policies)
    assert ready.call_count >= 1
    assert repo.get(**SCOPE, config_key=STORAGE_POLICY) is not None


def test_duplicate_policy_does_not_evaluate_or_overwrite(storage):
    _, repo, config, _, _ = storage
    config.set_config(
        **SCOPE, config_key=STORAGE_POLICY, value={"storage_type": "upfs"}
    )
    repo.initialize_once(
        **SCOPE, config_key=STORAGE_POLICY, config_value='{"storage_type":"nas"}'
    )
    assert config.get_config(**SCOPE, config_key=STORAGE_POLICY) == {
        "storage_type": "upfs"
    }


def make_baas(storage):
    from tests.community.core.service_bot.services.deploy.test_deploy_config_composer import (
        _make_service,
    )
    from agentclaw.community.core.service_bot.services.deploy.managed_composer import (
        ManagedDeployConfigComposer,
    )

    provider = MagicMock()
    provider.get_sessions_dir.return_value = "/sessions"
    registry = MagicMock()
    registry.resolve.return_value = provider
    composer = ManagedDeployConfigComposer(
        storage_path=MagicMock(), sandbox_registry=registry, bot_repo=MagicMock()
    )
    baas = _make_service(composer)
    baas._bot_storage_service = storage[-1]
    baas._storage_env = "pre"
    baas._get_bots_api = MagicMock(
        return_value={
            "template_uuid": "TEMPLATE-test",
            "type": "ARCA",
            "status": "ONLINE",
            "config": {"type": "ARCA", "upfs_volume_id_pre": "volume-pre"},
        }
    )
    baas.post_bots_api = MagicMock(
        return_value={"bot_uuid": "BOT-test", "publish_id": "pub1"}
    )
    return baas


def allocate(baas, *, new_bot=True, **overrides):
    from tests.community.core.devices.services.test_baas_device_service import (
        _make_service,
    )

    device = _make_service(baas_service=baas)
    kwargs = dict(
        entity_id=SCOPE["entity_id"],
        entity_type="staff",
        bolt_id=SCOPE["bot_id"],
        device_id="device-1",
        env="pre",
        engine="openclaw",
        bot_type="personal",
        owner_id="10001",
        bot_name="test",
        bot_desc=None,
        extra_envs=None,
        template_type=None,
        template_config={"template_uid": "default_template"},
    )
    kwargs.update(overrides)
    if new_bot and kwargs["bot_type"] == "personal":
        from tests.community.core.devices.services.test_baas_device_service import (
            _operator,
        )

        prepared = device.prepare_bot_storage_policy(
            bot_id=kwargs["bolt_id"],
            entity_id=kwargs["entity_id"],
            owner_id=kwargs["owner_id"],
            operator=_operator("10001"),
            bot_type=kwargs["bot_type"],
            engine=kwargs["engine"],
            template_type=kwargs["template_type"],
            template_config=kwargs["template_config"],
        )
        kwargs.update(prepared)
    result = device._allocate_via_baas(**kwargs)
    device._template_resolver.resolve_template.assert_called_once()
    return result


def latest_storage(baas):
    return baas.post_bots_api.call_args.kwargs["payload"]["config"]["deploy_config"][
        "storage"
    ]


@pytest.mark.parametrize("quota", [None, "2G"])
@pytest.mark.parametrize("enabled", [True, False])
def test_personal_create_payload_and_saved_storage_identity(storage, quota, enabled):
    baas = make_baas(storage)
    storage[3].get_config.return_value["enable"] = "1" if enabled else "0"
    storage[3].get_config.return_value["param_value"]["quota"] = quota
    allocate(baas)
    sent = latest_storage(baas)
    assert sent["quota"] == (quota or "1G")
    assert sent["type"] == ("upfs" if enabled else "nas")
    if enabled:
        assert sent["path"] == "/home/admin"
    assert "volume_id" not in sent
    if enabled:
        baas._get_bots_api.assert_called_once_with(
            path="/api/v1/device-templates/TEMPLATE-test",
            action="storage_template_precheck",
            log_response=False,
        )
    else:
        baas._get_bots_api.assert_not_called()
    # Plain restarts do not receive a creation context, even if the old NAS
    # whitelist says sessions-only. Policy still forces the original home mount.
    payload = baas._build_create_bot_payload(
        bot=dict(
            bot_id=SCOPE["bot_id"],
            entity_id=SCOPE["entity_id"],
            entity_type="staff",
            active_engine="openclaw",
            bot_type="personal",
        ),
        owner_id="owner-not-user",
        request_id="restart",
        device_count=1,
        migration_path="",
        mount_home_dir_storage=False,
    )
    assert payload["config"]["deploy_config"]["storage"]["quota"] == sent["quota"]
    if enabled:
        assert payload["config"]["deploy_config"]["storage"] == sent
    assert baas._get_bots_api.call_count == int(enabled)


@pytest.mark.parametrize(
    "capability",
    [
        {"template_uuid": "TEMPLATE-test", "env": "pre", "upfs_ready": False},
        {"template_uuid": "TEMPLATE-wrong", "env": "pre", "upfs_ready": True},
        {"template_uuid": "TEMPLATE-test", "env": "prod", "upfs_ready": True},
        {"template_uuid": "TEMPLATE-test", "env": "pre", "upfs_ready": "true"},
        {},
        None,
    ],
)
def test_actual_template_capability_is_required(storage, capability):
    baas = make_baas(storage)
    baas._get_bots_api.return_value = capability
    allocate(baas)
    assert latest_storage(baas)["type"] == "nas"
    assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) is None


def test_failed_nas_create_does_not_leave_a_policy(storage):
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasServiceError,
    )
    from agentclaw.community.core.devices.services.baas_device_service import (
        BaasDeviceServiceError,
    )

    baas = make_baas(storage)
    baas._get_bots_api.side_effect = RuntimeError("capability unavailable")
    baas.post_bots_api.side_effect = BaasServiceError("BaaS unavailable")
    with pytest.raises(BaasDeviceServiceError):
        allocate(baas)
    assert latest_storage(baas)["type"] == "nas"
    assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) is None


@pytest.mark.parametrize("bot_type", ["personal"])
def test_upfs_create_failure_keeps_committed_policy_on_retry(storage, bot_type):
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasServiceError,
    )
    from agentclaw.community.core.devices.services.baas_device_service import (
        BaasDeviceServiceError,
    )

    baas = make_baas(storage)
    baas.post_bots_api.side_effect = BaasServiceError("UPFS create failed")
    with pytest.raises(BaasDeviceServiceError):
        allocate(baas, bot_type=bot_type)
    assert latest_storage(baas)["type"] == "upfs"
    assert storage[1].get(**SCOPE, config_key=STORAGE_POLICY) is not None
    baas._get_bots_api.side_effect = AssertionError(
        "do not downgrade when volume removed"
    )
    with pytest.raises(BaasDeviceServiceError):
        allocate(baas, bot_type=bot_type)
    assert latest_storage(baas)["type"] == "upfs"


@pytest.mark.parametrize("bot_type", ["personal"])
def test_policy_write_failure_never_calls_baas_create(storage, bot_type):
    baas = make_baas(storage)
    storage[1].initialize_once = MagicMock(side_effect=RuntimeError("DB write failed"))
    with pytest.raises(RuntimeError, match="DB write failed"):
        allocate(baas, bot_type=bot_type)
    baas.post_bots_api.assert_not_called()


@pytest.mark.parametrize(
    "user_id,allow_all,expected",
    [
        (None, True, "upfs"),
        (None, False, "nas"),
        ("", False, "nas"),
        ("10001", False, "upfs"),
    ],
)
def test_creation_owner_is_rollout_user_not_entity_id(
    storage, user_id, allow_all, expected
):
    storage[3].get_config.return_value = {
        "enable": "1",
        "param_value": {
            "allow_all": allow_all,
            "rules": [{"engine_bucket": "openclaw", "allow_user_groups": ["10001"]}],
        },
    }
    baas = make_baas(storage)
    allocate(baas, owner_id=user_id)
    assert latest_storage(baas)["type"] == expected


@pytest.mark.parametrize("bot_type", ["personal", "service"])
def test_existing_bot_allocation_does_not_initialize(storage, bot_type):
    baas = make_baas(storage)
    allocate(baas, bot_type=bot_type, new_bot=False)
    assert latest_storage(baas)["type"] == "nas"
    baas._get_bots_api.assert_not_called()
    assert storage[1].get(**SCOPE, config_key=STORAGE_POLICY) is None


@pytest.mark.parametrize("bot_type", ["personal", "service"])
@pytest.mark.parametrize("storage_type", ["nas", "upfs"])
def test_recovery_reads_manually_inserted_policy_without_rollout(
    storage, bot_type, storage_type
):
    policy = {"storage_type": storage_type, "source": "manual"}
    storage[2].set_config(**SCOPE, config_key=STORAGE_POLICY, value=policy)
    storage[3].get_config.side_effect = AssertionError("recovery must not read rollout")
    baas = make_baas(storage)
    baas.initialize_bot_storage = MagicMock(side_effect=AssertionError("no initialize"))
    allocate(baas, bot_type=bot_type, new_bot=False)
    assert latest_storage(baas)["type"] == (
        storage_type if bot_type == "personal" else "nas"
    )
    assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) == policy
    baas.initialize_bot_storage.assert_not_called()
    baas._get_bots_api.assert_not_called()


@pytest.mark.parametrize("bot_type", ["personal"])
@pytest.mark.parametrize("force_nas", [False, True])
@pytest.mark.parametrize("ready", [False, True])
def test_creation_entry_initializes_before_shared_allocation_with_one_template(
    storage,
    bot_type,
    force_nas,
    ready,
):
    from tests.community.core.devices.services.test_baas_device_service import (
        _make_service,
        _operator,
    )

    baas = make_baas(storage)
    baas._get_bots_api.return_value["config"]["upfs_volume_id_pre"] = (
        "volume-pre" if ready else None
    )
    device = _make_service(baas_service=baas)
    device._get_storage_mode = MagicMock(return_value="oss")
    device._validate_and_generate_device_id = MagicMock(
        return_value=("dev1", "b1", None)
    )
    device._after_binding_persisted = MagicMock(return_value=True)
    original_create = baas.post_bots_api.return_value
    expected = "upfs" if ready else "nas"

    def create_after_policy(**kwargs):
        assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) == (
            {"storage_type": "upfs", "source": "rollout"} if ready else None
        )
        assert kwargs["payload"]["template_uuid"] == "TEMPLATE-test"
        assert "_prepared_bot_creation" not in str(kwargs["payload"])
        return original_create

    baas.post_bots_api.side_effect = create_after_policy
    template_config = {"template_uid": "default_template", "envs": {"CUSTOM": "kept"}}
    kwargs = dict(
        apply_reason="create",
        entity_id=SCOPE["entity_id"],
        entity_type="staff",
        operator=_operator("10001"),
        owner_id="10001",
        bot_id=SCOPE["bot_id"],
        bot_type=bot_type,
        engine="openclaw",
        force_nas=force_nas,
        template_config=template_config,
    )
    kwargs.update(device.prepare_bot_storage_policy(**kwargs))
    device.apply_device(**kwargs)
    assert latest_storage(baas)["type"] == expected
    device._template_resolver.resolve_template.assert_called_once()
    device._after_binding_persisted.assert_called_once()
    assert template_config == {
        "template_uid": "default_template",
        "envs": {"CUSTOM": "kept"},
    }


@pytest.mark.parametrize("bot_type", ["personal"])
def test_creation_template_failure_stops_before_policy_and_allocation(
    storage, bot_type
):
    from tests.community.core.devices.services.test_baas_device_service import (
        _make_service,
        _operator,
    )
    from agentclaw.community.core.devices.services.baas_template_resolver import (
        BaasTemplateResolveError,
    )

    baas = make_baas(storage)
    device = _make_service(baas_service=baas)
    device._template_resolver.resolve_template.side_effect = BaasTemplateResolveError(
        "missing template"
    )
    device.apply_device = MagicMock()
    with pytest.raises(BaasTemplateResolveError, match="missing template"):
        device.prepare_bot_storage_policy(
            bot_id=SCOPE["bot_id"],
            entity_id=SCOPE["entity_id"],
            owner_id="10001",
            operator=_operator("10001"),
            bot_type=bot_type,
            template_config={"template_uid": "default_template"},
        )
    assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) is None
    storage[3].get_config.assert_not_called()
    device.apply_device.assert_not_called()
    baas.post_bots_api.assert_not_called()


def test_default_storage_preparation_is_noop():
    from agentclaw.community.core.devices.services.device_service import DeviceService

    device = MagicMock()
    assert (
        DeviceService.prepare_bot_storage_policy(device, bot_id="b1", owner_id="10001") == {}
    )
    device.apply_device.assert_not_called()


@pytest.mark.parametrize("ready", [False, True])
def test_creation_uses_prepared_choice_without_second_policy_read(storage, ready):
    baas = make_baas(storage)
    baas._get_bots_api.return_value["config"]["upfs_volume_id_pre"] = (
        "volume-pre" if ready else None
    )
    original = baas.initialize_bot_storage

    def initialize_then_disable_reads(**kwargs):
        result = original(**kwargs)
        baas._bot_storage_service._resolve_storage_policy = MagicMock(
            side_effect=AssertionError("payload must use prepared choice")
        )
        return result

    baas.initialize_bot_storage = initialize_then_disable_reads
    allocate(baas)
    assert latest_storage(baas)["type"] == ("upfs" if ready else "nas")


def test_ack_composition_does_not_initialize(storage):
    from agentclaw.community.core.service_bot.services.deploy.ack_composer import (
        AckDeployConfigComposer,
    )

    baas = make_baas(storage)
    baas._deploy_composer = AckDeployConfigComposer()
    # ACK wiring leaves the optional policy service unset (tested below).
    baas._bot_storage_service = None
    assert (
        baas.initialize_bot_storage(
            **SCOPE, engine="openclaw", user_id="10001", template_uuid="TEMPLATE-test"
        )
        == "nas"
    )
    baas._get_bots_api.assert_not_called()
    assert storage[1].get(**SCOPE, config_key=STORAGE_POLICY) is None


def test_new_storage_services_are_wired_in_di(test_injector):
    from agentclaw.community.core.common_config.bot_config_protocol import (
        BotCommonConfigServiceProtocol,
    )

    assert isinstance(
        test_injector.get(BotCommonConfigServiceProtocol), BotCommonConfigService
    )
    assert isinstance(
        test_injector.get(BotStoragePolicyService), BotStoragePolicyService
    )


@pytest.mark.parametrize("provider", ["baas", "arca"])
def test_router_prepares_only_once_then_uses_original_allocation(provider):
    from tests.community.core.devices.services.test_device_service_router import (
        _make_router,
        _make_operator,
    )

    router, _, _, _ = _make_router(is_local=False)
    service = router._providers[provider]
    service.prepare_bot_storage_policy.return_value = {}
    router._get_provider_for_new_device = MagicMock(return_value=service)
    kwargs = dict(
        apply_reason="create",
        entity_id="team-1",
        entity_type="team",
        operator=_make_operator(),
        bot_id="b1",
        bot_type="personal",
    )
    prepared = router.prepare_bot_storage_policy(**kwargs)
    assert prepared == {"device_provider": provider}
    service.apply_device.assert_not_called()
    router.apply_device(**kwargs, **prepared)
    router._get_provider_for_new_device.assert_called_once()
    service.prepare_bot_storage_policy.assert_called_once_with(**kwargs)
    service.apply_device.assert_called_once()


@pytest.mark.parametrize("force_nas", [True, False])
def test_device_allocation_reuses_creation_owner(force_nas):
    from tests.community.core.devices.services.test_baas_device_service import (
        _make_service,
        _operator,
    )

    device = _make_service()
    device._get_storage_mode = MagicMock(return_value="oss")
    device._validate_and_generate_device_id = MagicMock(
        return_value=("dev1", "b1", None)
    )
    hook = MagicMock(side_effect=RuntimeError("stop after forwarding"))
    if force_nas:
        device._do_allocate_nas = hook
    else:
        device._do_allocate = hook
    with pytest.raises(RuntimeError, match="stop after forwarding"):
        device.apply_device(
            apply_reason="create",
            entity_id="team-1",
            entity_type="team",
            operator=_operator("10001"),
            bot_id="b1",
            owner_id="10001",
            bot_type="personal",
            force_nas=force_nas,
        )
    assert "initial_storage_decision" not in hook.call_args.kwargs
    assert hook.call_args.kwargs["owner_id"] == "10001"
    assert "initial_storage_user_id" not in hook.call_args.kwargs


@pytest.mark.parametrize("bot_type", ["personal", "service"])
@pytest.mark.parametrize("ready", [True, False])
def test_personal_guard_controls_creation(storage, bot_type, ready):
    baas = make_baas(storage)
    baas._get_bots_api.return_value["config"]["upfs_volume_id_pre"] = (
        "volume-pre" if ready else None
    )
    baas.initialize_bot_storage = MagicMock(wraps=baas.initialize_bot_storage)
    allocate(baas, bot_type=bot_type)
    selected = bot_type == "personal" and ready
    assert latest_storage(baas)["type"] == ("upfs" if selected else "nas")
    assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) == (
        {"storage_type": "upfs", "source": "rollout"} if selected else None
    )
    if bot_type == "service":
        baas.initialize_bot_storage.assert_not_called()
        baas._get_bots_api.assert_not_called()


@pytest.mark.parametrize("ready", [True, False])
def test_initialize_bot_storage_is_reusable_without_creating_a_device(storage, ready):
    baas = make_baas(storage)
    baas._get_bots_api.return_value["config"]["upfs_volume_id_pre"] = (
        "volume-pre" if ready else None
    )
    kwargs = dict(
        **SCOPE, engine="openclaw", user_id="10001", template_uuid="TEMPLATE-test"
    )
    assert baas.initialize_bot_storage(**kwargs) == ("upfs" if ready else "nas")
    baas.post_bots_api.assert_not_called()
    assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) == (
        {"storage_type": "upfs", "source": "rollout"} if ready else None
    )


def test_unrelated_integrity_error_rolls_back(storage):
    from unittest.mock import patch
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    repo = storage[1]
    with patch.object(Session, "flush", side_effect=IntegrityError("insert", {}, Exception("failure"))):
        with pytest.raises(IntegrityError):
            repo.initialize_once(
                **SCOPE, config_key=STORAGE_POLICY, config_value='{"storage_type":"upfs"}'
            )
    assert repo.get(**SCOPE, config_key=STORAGE_POLICY) is None


def test_deleted_policy_does_not_reinitialize(storage):
    db, _, _, _, service = storage
    initialize(service)
    with db.orm_session() as session:
        session.query(BotCommonConfig).filter_by(**SCOPE).update({"is_delete": 1})
    with pytest.raises(ValueError, match="missing or deleted"):
        initialize(service)


@pytest.mark.parametrize("bot_type", ["personal", "service"])
@pytest.mark.parametrize("storage_type", ["upfs", "nas", None])
def test_real_restart_entry_reuses_policy_and_baas_identity(
    storage, bot_type, storage_type
):
    """Exercise restart_bot -> upgrade_bot -> real payload, not a builder alone."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from tests.community.core.bot_management.services.test_bot_service_restart_idempotency import (
        _make_bot,
        _make_service,
    )

    baas = make_baas(storage)
    if storage_type is not None:
        storage[2].set_config(
            **SCOPE,
            config_key=STORAGE_POLICY,
            value={"storage_type": storage_type, "source": "manual"},
        )
        allocate(baas, bot_type=bot_type, new_bot=False)
        original_storage = latest_storage(baas)
    else:
        original_storage = None
    original_policy = storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY)
    storage[3].get_config.side_effect = AssertionError("restart must not run rollout")
    baas._get_bots_api.side_effect = AssertionError("restart must not run preflight")
    baas.initialize_bot_storage = MagicMock(
        side_effect=AssertionError("no initialization")
    )
    baas.list_bot_publishes = MagicMock(return_value=[])
    baas._post_bots_api = MagicMock(
        return_value={"bot_uuid": "BOT-test", "publish_id": 42}
    )
    device = MagicMock()
    device.get_device.return_value = SimpleNamespace(
        id=42,
        device_provider="baas",
        device_id="BOT-test",
        status="ACTIVE",
        device_props={},
    )
    bot = _make_bot(
        bot_id=SCOPE["bot_id"],
        owner_id="owner-not-user",
        entity_id=SCOPE["entity_id"],
        binding_id=42,
        bot_type=bot_type,
        active_engine="openclaw",
    )
    bot["env"] = SCOPE["env"]
    svc = _make_service(device_provider=device, baas_service_provider=lambda: baas)
    svc._repository.get_by_id_and_owner.return_value = bot
    svc._template_service.get_template_config.return_value = {}
    with patch.object(svc, "stop_bot") as stop, patch.object(svc, "start_bot") as start:
        svc.restart_bot(bot_id=SCOPE["bot_id"], user_id="owner-not-user")
    sent = baas._post_bots_api.call_args.kwargs
    assert sent["path"] == "/api/v1/bots/BOT-test/update"
    config = sent["payload"]["config"]["deploy_config"]
    assert config["storage"]["type"] == (
        (storage_type or "nas") if bot_type == "personal" else "nas"
    )
    if original_storage is not None:
        assert config["storage"] == original_storage
    if bot_type == "service":
        assert "--stage draft" in config["after_create_cmd_hook"]
    assert "--source_dir" not in config["after_create_cmd_hook"]
    assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) == original_policy
    baas.initialize_bot_storage.assert_not_called()
    stop.assert_not_called()
    start.assert_not_called()
    device.apply_device.assert_not_called()


def test_storage_policy_capability_does_not_depend_on_composer_name(storage):
    from agentclaw.community.core.service_bot.services.deploy.managed_composer import (
        ManagedDeployConfigComposer,
    )

    class CapableComposer(ManagedDeployConfigComposer):
        @property
        def name(self):
            raise AssertionError("capability must not inspect runtime identity")

    baas = make_baas(storage)
    baas._deploy_composer.__class__ = CapableComposer
    assert (
        baas.initialize_bot_storage(
            **SCOPE, engine="openclaw", user_id="10001", template_uuid="TEMPLATE-test"
        )
        == "upfs"
    )


@pytest.mark.parametrize("stage", ["verify", "online"])
@pytest.mark.parametrize("migration_path", ["", "/published/build"])
def test_published_and_caller_payloads_do_not_consume_draft_policy(
    storage,
    stage,
    migration_path,
):
    """Shared payload composition must not enroll later-phase instances."""
    baas = make_baas(storage)
    storage[3].get_config.return_value["param_value"]["quota"] = "2G"
    allocate(baas, bot_type="service")
    reader = MagicMock(side_effect=AssertionError("do not read the draft policy"))
    baas._bot_storage_service._resolve_storage_policy = reader
    kwargs = dict(
        bot=dict(
            bot_id=SCOPE["bot_id"],
            entity_id=SCOPE["entity_id"],
            entity_type="staff",
            active_engine="openclaw",
            bot_type="service",
        ),
        owner_id="owner-not-user",
        request_id="published",
        device_count=1,
        migration_path=migration_path,
        stage=stage,
    )
    payload = baas._build_create_bot_payload(**kwargs)
    baas._bot_storage_service = None
    legacy_payload = baas._build_create_bot_payload(**kwargs)
    assert payload["config"]["deploy_config"]["storage"]["quota"] == "2G"
    legacy_payload["config"]["deploy_config"]["storage"]["quota"] = "2G"
    assert payload == legacy_payload
    reader.assert_not_called()


def test_personal_artifact_deploy_does_not_consume_source_policy(storage):
    baas = make_baas(storage)
    allocate(baas)
    baas._bot_storage_service._resolve_storage_policy = MagicMock(
        side_effect=AssertionError("artifact deployment is outside phase one")
    )
    payload = baas._build_create_bot_payload(
        bot=dict(
            bot_id=SCOPE["bot_id"],
            entity_id=SCOPE["entity_id"],
            bot_type="personal",
            active_engine="openclaw",
        ),
        owner_id="owner",
        request_id="artifact",
        device_count=1,
        migration_path="/published/build",
    )
    assert payload["config"]["deploy_config"]["storage"]["type"] == "nas"
    baas._bot_storage_service._resolve_storage_policy.assert_not_called()


@pytest.mark.parametrize("runtime", ["managed", "ack"])
def test_composition_root_selects_storage_policy_support(runtime):
    from inspect import signature

    from agentclaw.community.di import config as cfg
    from agentclaw.community.di.modules.service_bot_module import ServiceBotModule
    from agentclaw.community.kernel.deploy_runtime import DeployRuntime

    method = ServiceBotModule.baas_service
    kwargs = {
        name: MagicMock() for name in signature(method).parameters if name != "self"
    }
    kwargs["deploy_runtime"] = cfg.DeployRuntimeConfig(DeployRuntime(runtime))
    service = method(ServiceBotModule(), **kwargs)
    expected = kwargs["bot_storage_service"] if runtime == "managed" else None
    assert service._bot_storage_service is expected


def test_storage_logs_record_outcome_without_exception_contents(storage):
    service = storage[-1]
    with patch(
        "agentclaw.community.core.common_config.bot_config_service.logger"
    ) as log:
        initialize(service)
        initialize(service)
        service._resolve_storage_policy(**SCOPE)
        storage[2].get_config = MagicMock(
            side_effect=RuntimeError("secret-exception-value")
        )
        with pytest.raises(RuntimeError):
            service._resolve_storage_policy(**SCOPE)
    calls = str(log.mock_calls)
    for event in (
        "initialization_complete",
        "initialization_reused",
        "event=reused",
        "event=read_failed",
    ):
        assert event in calls
    assert "RuntimeError" in calls and "secret-exception-value" not in calls
    assert SCOPE["bot_id"] in calls and SCOPE["entity_id"] in calls


def test_payload_log_correlates_bot_request_and_template_without_env_secrets(storage):
    baas = make_baas(storage)
    with patch(
        "agentclaw.community.core.service_bot.services.baas_service.logger"
    ) as log:
        allocate(baas, extra_envs={"TOKEN": "<test-only-env-secret>"})
    calls = str(log.mock_calls)
    assert "event=payload_prepared" in calls and "event=template_precheck" in calls
    assert "TEMPLATE-test" in calls and SCOPE["bot_id"] in calls
    assert baas.post_bots_api.call_args.kwargs["payload"]["request_id"] in calls
    assert "<test-only-env-secret>" not in calls

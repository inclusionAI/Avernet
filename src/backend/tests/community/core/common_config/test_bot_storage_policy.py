"""Phase-one storage contracts using real SQLite persistence and mocked BaaS."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import MagicMock

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
def storage(tmp_path):
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
def test_nas_policy_and_retry_stays_nas(storage, failure):
    _, repo, _, common, service = storage
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
    assert '"storage_type": "nas"' in repo.get(**SCOPE, config_key=STORAGE_POLICY)
    common.get_config.side_effect = None
    common.get_config.return_value = {"enable": "1", "param_value": {"allow_all": True}}
    ready.reset_mock(side_effect=True, return_value=True)
    ready.return_value = True
    assert initialize(service, upfs_ready=ready).storage_type == "nas"
    ready.assert_not_called()


@pytest.mark.parametrize("value", ["bad-json", "[]", '{"storage_type":"bad"}'])
def test_invalid_policy_compatible_read_but_strict_creation(storage, value):
    _, repo, _, _, service = storage
    repo.put(**SCOPE, config_key=STORAGE_POLICY, config_value=value)
    assert service._resolve_storage_policy(**SCOPE) is None
    with pytest.raises(ValueError):
        initialize(service)


def test_policy_read_exception_returns_none(storage):
    _, _, config, _, service = storage
    config.get_config = MagicMock(side_effect=RuntimeError("read failed"))
    assert service._resolve_storage_policy(**SCOPE) is None


def test_write_failure_propagates_without_partial_policy(storage):
    _, repo, _, _, service = storage
    with pytest.raises(RuntimeError, match="factory failed"):
        repo.initialize_once(
            **SCOPE,
            config_key=STORAGE_POLICY,
            factory=MagicMock(side_effect=RuntimeError("factory failed")),
        )
    assert repo.get(**SCOPE, config_key=STORAGE_POLICY) is None
    repo.initialize_once = MagicMock(side_effect=RuntimeError("write failed"))
    with pytest.raises(RuntimeError, match="write failed"):
        initialize(service)


def test_concurrent_first_creations_run_only_one_decision(storage):
    _, repo, _, _, service = storage
    ready = MagicMock(return_value=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        policies = list(
            pool.map(lambda _: initialize(service, upfs_ready=ready), range(6))
        )
    assert all(p.storage_type == "upfs" for p in policies)
    ready.assert_called_once()
    assert repo.get(**SCOPE, config_key=STORAGE_POLICY) is not None


def test_duplicate_policy_does_not_evaluate_or_overwrite(storage):
    _, repo, config, _, _ = storage
    config.set_config(
        **SCOPE, config_key=STORAGE_POLICY, value={"storage_type": "upfs"}
    )
    factory = MagicMock(side_effect=AssertionError("must not decide again"))
    repo.initialize_once(**SCOPE, config_key=STORAGE_POLICY, factory=factory)
    factory.assert_not_called()
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
            "env": "pre",
            "upfs_ready": True,
        }
    )
    baas.post_bots_api = MagicMock(
        return_value={"bot_uuid": "BOT-test", "publish_id": "pub1"}
    )
    return baas


def allocate(baas, **overrides):
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
        owner_id="owner-not-user",
        bot_name="test",
        bot_desc=None,
        extra_envs=None,
        template_type=None,
        template_config={"template_uid": "default_template"},
        initial_storage_decision=True,
        initial_storage_user_id="10001",
    )
    kwargs.update(overrides)
    return device._allocate_via_baas(**kwargs)


def latest_storage(baas):
    return baas.post_bots_api.call_args.kwargs["payload"]["config"]["deploy_config"][
        "storage"
    ]


def test_personal_create_payload_and_saved_storage_identity(storage):
    baas = make_baas(storage)
    allocate(baas)
    sent = latest_storage(baas)
    assert sent["type"] == "upfs"
    assert sent["path"] == "/home/admin"
    assert "volume_id" not in sent
    baas._get_bots_api.assert_called_once_with(
        path="/api/v1/device-templates/TEMPLATE-test/storage-capability",
        action="storage_capability",
        params={"env": "pre"},
    )
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
    assert payload["config"]["deploy_config"]["storage"] == sent
    assert baas._get_bots_api.call_count == 1


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
    assert (
        storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY)["storage_type"]
        == "nas"
    )


@pytest.mark.parametrize("bot_type", ["personal", "service"])
def test_nas_create_failure_then_retry_never_rechecks(storage, bot_type):
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
        allocate(baas, bot_type=bot_type)
    assert latest_storage(baas)["type"] == "nas"
    baas._get_bots_api.side_effect = None
    baas.post_bots_api.side_effect = None
    allocate(baas, bot_type=bot_type)
    assert latest_storage(baas)["type"] == "nas"
    assert baas._get_bots_api.call_count == 1


@pytest.mark.parametrize("bot_type", ["personal", "service"])
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


@pytest.mark.parametrize("bot_type", ["personal", "service"])
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
def test_business_user_id_not_owner_or_entity_id(storage, user_id, allow_all, expected):
    storage[3].get_config.return_value = {
        "enable": "1",
        "param_value": {
            "allow_all": allow_all,
            "rules": [{"engine_bucket": "openclaw", "allow_user_groups": ["10001"]}],
        },
    }
    baas = make_baas(storage)
    allocate(baas, initial_storage_user_id=user_id)
    assert latest_storage(baas)["type"] == expected


@pytest.mark.parametrize("bot_type", ["personal", "service"])
def test_existing_bot_allocation_does_not_initialize(storage, bot_type):
    baas = make_baas(storage)
    allocate(baas, bot_type=bot_type, initial_storage_decision=False)
    assert latest_storage(baas)["type"] == "nas"
    baas._get_bots_api.assert_not_called()
    assert storage[1].get(**SCOPE, config_key=STORAGE_POLICY) is None


def test_ack_composition_does_not_initialize(storage):
    from agentclaw.community.core.service_bot.services.deploy.ack_composer import (
        AckDeployConfigComposer,
    )

    baas = make_baas(storage)
    baas._deploy_composer = AckDeployConfigComposer()
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
def test_router_only_forwards_initial_decision_to_baas(provider):
    from tests.community.core.devices.services.test_device_service_router import (
        _make_router,
        _make_operator,
    )

    router, _, _, _ = _make_router(is_local=False)
    service = router._providers[provider]
    router.apply_device(
        apply_reason="create",
        entity_id="team-1",
        entity_type="team",
        operator=_make_operator(),
        bot_id="b1",
        device_provider=provider,
        bot_type="personal",
        initial_storage_decision=True,
    )
    assert service.apply_device.call_args.kwargs.get(
        "initial_storage_decision", False
    ) == (provider == "baas")


@pytest.mark.parametrize("force_nas", [True, False])
def test_device_allocation_preserves_initial_user_context(force_nas):
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
            owner_id="owner-not-user",
            bot_type="personal",
            force_nas=force_nas,
            initial_storage_decision=True,
        )
    assert hook.call_args.kwargs["initial_storage_decision"] is True
    assert hook.call_args.kwargs["initial_storage_user_id"] == "10001"


@pytest.mark.parametrize("bot_type", ["personal", "service"])
@pytest.mark.parametrize("ready", [True, False])
def test_both_create_types_share_policy_initialization(storage, bot_type, ready):
    baas = make_baas(storage)
    baas._get_bots_api.return_value["upfs_ready"] = ready
    baas.initialize_bot_storage = MagicMock(wraps=baas.initialize_bot_storage)
    allocate(baas, bot_type=bot_type)
    expected = "upfs" if ready else "nas"
    assert latest_storage(baas)["type"] == expected
    assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) == {
        "storage_type": expected,
        "source": "rollout",
    }
    baas.initialize_bot_storage.assert_called_once_with(
        **SCOPE, engine="openclaw", user_id="10001", template_uuid="TEMPLATE-test"
    )
    if bot_type == "service":
        assert "--stage draft" in baas.post_bots_api.call_args.kwargs["payload"][
            "config"
        ]["deploy_config"]["after_create_cmd_hook"]
    storage[3].get_config.side_effect = AssertionError("must reuse decision")
    allocate(baas, bot_type=bot_type)
    assert latest_storage(baas)["type"] == expected
    assert baas._get_bots_api.call_count == 1


@pytest.mark.parametrize("ready", [True, False])
def test_initialize_bot_storage_is_reusable_without_creating_a_device(storage, ready):
    baas = make_baas(storage)
    baas._get_bots_api.return_value["upfs_ready"] = ready
    kwargs = dict(**SCOPE, engine="openclaw", user_id="10001", template_uuid="TEMPLATE-test")
    expected = "upfs" if ready else "nas"
    assert baas.initialize_bot_storage(**kwargs) == expected
    baas._get_bots_api.side_effect = AssertionError("existing policy skips precheck")
    storage[3].get_config.side_effect = AssertionError("existing policy skips rollout")
    assert baas.initialize_bot_storage(**kwargs) == expected
    baas.post_bots_api.assert_not_called()
    assert storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY) == {
        "storage_type": expected,
        "source": "rollout",
    }


def test_factory_integrity_error_rolls_back(storage):
    from sqlalchemy.exc import IntegrityError

    repo = storage[1]
    with pytest.raises(IntegrityError):
        repo.initialize_once(
            **SCOPE,
            config_key=STORAGE_POLICY,
            factory=MagicMock(
                side_effect=IntegrityError("bad factory", {}, Exception("failure"))
            ),
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
def test_real_restart_entry_reuses_policy_and_baas_identity(storage, bot_type, storage_type):
    """Exercise restart_bot -> upgrade_bot -> real payload, not a builder alone."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from tests.community.core.bot_management.services.test_bot_service_restart_idempotency import (
        _make_bot,
        _make_service,
    )

    baas = make_baas(storage)
    if storage_type is not None:
        baas._get_bots_api.return_value["upfs_ready"] = storage_type == "upfs"
        allocate(baas, bot_type=bot_type)
        original_storage = latest_storage(baas)
    else:
        original_storage = None
    original_policy = storage[2].get_config(**SCOPE, config_key=STORAGE_POLICY)
    storage[3].get_config.side_effect = AssertionError("restart must not run rollout")
    baas._get_bots_api.side_effect = AssertionError("restart must not run preflight")
    baas.initialize_bot_storage = MagicMock(side_effect=AssertionError("no initialization"))
    baas.list_bot_publishes = MagicMock(return_value=[])
    baas._post_bots_api = MagicMock(return_value={"bot_uuid": "BOT-test", "publish_id": 42})
    device = MagicMock()
    device.get_device.return_value = SimpleNamespace(
        id=42, device_provider="baas", device_id="BOT-test", status="ACTIVE", device_props={}
    )
    bot = _make_bot(
        bot_id=SCOPE["bot_id"], owner_id="owner-not-user", entity_id=SCOPE["entity_id"],
        binding_id=42, bot_type=bot_type, active_engine="openclaw",
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
    assert config["storage"]["type"] == (storage_type or "nas")
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
    assert baas.initialize_bot_storage(
        **SCOPE, engine="openclaw", user_id="10001", template_uuid="TEMPLATE-test"
    ) == "upfs"

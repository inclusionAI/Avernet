"""Unit tests for the BaaS -> ChangeFlow mapping contract."""

from types import SimpleNamespace

from secbaas.community.core.service.publish_manage._changeflow_mapping import (
    build_batch_context,
    build_register_context,
    build_terminal_context,
    candidate_version,
    is_online_publish,
    map_batch_outcome,
    map_environment,
    map_target_state,
    map_terminal_outcome,
)
from secbaas.community.core.service.publish_manage._publish_service import (
    _public_publish_config,
)


def _publish(**overrides):
    values = {
        "id": 42,
        "tenant": "tenant-a",
        "publish_type": "CREATE",
        "config_version": None,
        "creator": "alice",
        "extra_config": {"publish_stage": "online"},
        "bot_id": 7,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _batch(**overrides):
    values = {
        "id": 3,
        "publish_id": 42,
        "stage": "PROD_FIRST_BATCH",
        "batch_index": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_environment_mapping_is_prod_or_pre_only():
    assert map_environment("prod") == "prod"
    for env in ("singlebox", "dev", "pre", "stable", "gray"):
        assert map_environment(env) == "pre"


def test_is_online_publish_requires_online_stage_and_audited_type():
    assert is_online_publish(_publish()) is True
    assert is_online_publish(_publish(publish_type="RESTART")) is True
    assert is_online_publish(_publish(extra_config={})) is False
    assert (
        is_online_publish(_publish(extra_config={"publish_stage": "verify"})) is False
    )
    assert is_online_publish(_publish(publish_type="DESTROY")) is False


def test_status_mappings():
    assert map_batch_outcome("COMPLETED") == "succeeded"
    assert map_batch_outcome("FAILED") == "failed"
    assert map_batch_outcome("RUNNING") == "pending"
    assert map_target_state("SUCCESS") == "succeeded"
    assert map_target_state("FAILED") == "failed"
    assert map_target_state("PROCESSING") == "pending"
    assert map_terminal_outcome("SUCCESS") == "success"
    assert map_terminal_outcome("FAILED") == "failed"
    assert map_terminal_outcome("REJECTED") == "cancelled"
    assert map_terminal_outcome("REVOKED") == "cancelled"


def test_candidate_version_is_stable_synthetic():
    assert candidate_version(_publish(config_version="1.2.3")) == "v42"
    assert candidate_version(_publish(config_version=None)) == "v42"


def test_register_context_carries_stage_batches_and_engine():
    publish = _publish()
    bot = SimpleNamespace(id=7, name="svc-bot", replica_desired=1)
    bot_config = SimpleNamespace(
        deploy_config=SimpleNamespace(envs={"AGENTCLAW_ENGINE": "teclaw"})
    )
    batch_ctx = build_batch_context(
        publish_record=publish,
        batch=_batch(),
        records=[
            SimpleNamespace(device_uuid="dev-1", result_status="SUCCESS"),
            SimpleNamespace(device_uuid=None, result_status="FAILED"),
        ],
        change_id="chg-1",
    )
    register = build_register_context(
        publish_record=publish,
        bot_record=bot,
        bot_config=bot_config,
        environment="prod",
        batches_by_stage={"PROD_FIRST_BATCH": [batch_ctx]},
    )
    assert register.publish_id == 42
    assert register.bot_name == "svc-bot"
    assert register.engine == "teclaw"
    assert register.environment == "prod"
    assert register.candidate_version == "v42"
    assert register.stages[0].name == "PROD_FIRST_BATCH"
    assert [t.target for t in register.stages[0].batches[0].targets] == ["dev-1"]


def test_terminal_context_maps_status():
    ctx = build_terminal_context(
        publish_record=_publish(),
        change_id="chg-1",
        publish_status="REJECTED",
        reason="operator rejected",
    )
    assert ctx.outcome == "cancelled"
    assert ctx.reason == "operator rejected"


def test_public_publish_config_hides_cf_internal_keys():
    config = _public_publish_config(
        {"publish_stage": "online", "cf_change_id": "chg-secret", "auto_complete": True}
    )
    dumped = config.model_dump(exclude_none=True)
    assert dumped["publish_stage"] == "online"
    assert "cf_change_id" not in dumped

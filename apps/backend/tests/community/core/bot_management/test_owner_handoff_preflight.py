"""Preflight failures leave the real Bot, journal and maintenance lock unchanged."""

from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.models import BotCollabLockModel
from agentclaw.community.core.bot_management.owner_handoff.contracts import HandoffError
from agentclaw.community.core.bot_management.owner_handoff.guard import assert_bot_mutable
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.plugin_api.models import BotModel
from tests.community.core.bot_management.test_owner_handoff import begin, drain, world  # noqa: F401
from tests.community.core.bot_management.test_resource_handoff import persisted  # noqa: F401


def assert_unfrozen(world):
    bot = world.bots.get_by_pk(world.bot["id"])
    assert_bot_mutable(bot)
    assert bot["owner_id"] == "A"
    assert not world.repo.recoverable()
    with world.db.orm_session() as db:
        assert db.query(BotCollabLockModel).count() == 0
    assert not world.passport.issues
    assert not world.runtime.calls
    assert not world.queue.calls


def bind(world, *, name="published-device", reason="test", provider="baas"):
    return world.devices.insert_binding(
        entity_id="A", entity_type="staff", device_id=name,
        device_provider=provider, env="dev", device_props={}, status="ACTIVE",
        apply_reason=reason, applied_by="A",
    )


def publish(world, status, ext):
    return world.publishes.insert({
        "source_bot_pk": world.bot["id"], "source_bot_id": "service",
        "publish_bot_id": "service", "owner_id": "A", "env": "dev",
        "name": "service", "status": status, "version": 1,
        "permission_owner": "owner", "ext": ext,
    })


def test_no_passport_history_is_rejected_before_freezing(world, monkeypatch):
    monkeypatch.setattr(world.passport, "query_agent_passport", lambda **_: None)
    with pytest.raises(HandoffError, match="CURRENT_PASSPORT_REQUIRED"):
        begin(world)
    assert_unfrozen(world)


def test_failed_preflight_does_not_initialize_target_personal_space(world, monkeypatch):
    source = world.spaces.initialize_personal(user_id="A", create_sc_team=False)[0]
    world.bots.update_by_owner("service", "A", {"space_id": source.id})
    monkeypatch.setattr(world.passport, "query_agent_passport", lambda **_: None)
    with pytest.raises(HandoffError, match="CURRENT_PASSPORT_REQUIRED"):
        begin(world)
    assert world.spaces_repo.get_personal_space(user_id="B", env="dev") is None
    assert_unfrozen(world)


def test_expired_history_is_still_allowed_without_issuing_or_authenticating(world, monkeypatch):
    query = Mock(return_value=None)
    snapshot = Mock(return_value={"credential_id": "expired-history"})
    monkeypatch.setattr(world.passport, "query_agent_passport", query)
    monkeypatch.setattr(world.passport, "query_handoff_passport", snapshot)
    op = begin(world)
    snapshot.assert_called_once_with(bot_id="service", owner_workno="A", entity_id="A")
    assert world.repo.get(op)["phase"] == "QUEUED"
    assert not world.passport.issues and not world.runtime.calls


def test_valid_history_does_not_require_the_expired_snapshot_rpc(world, monkeypatch):
    snapshot = Mock(side_effect=AssertionError("must not call expired snapshot"))
    monkeypatch.setattr(world.passport, "query_handoff_passport", snapshot)
    begin(world)
    snapshot.assert_not_called()


@pytest.mark.parametrize("boundary", ["capability", "passport", "snapshot", "bindings"])
def test_read_failures_are_sanitized_and_do_not_freeze(world, monkeypatch, boundary):
    failure = Mock(side_effect=RuntimeError("sensitive provider response"))
    if boundary == "capability":
        monkeypatch.setattr(world.passport, "owner_handoff_supported", failure)
    elif boundary == "passport":
        monkeypatch.setattr(world.passport, "query_agent_passport", failure)
    elif boundary == "snapshot":
        monkeypatch.setattr(world.passport, "query_agent_passport", lambda **_: None)
        monkeypatch.setattr(world.passport, "query_handoff_passport", failure)
    else:
        monkeypatch.setattr(world.devices, "get_by_id", failure)
    with pytest.raises(HandoffError, match="^HANDOFF_PREFLIGHT_UNAVAILABLE$"):
        begin(world)
    assert_unfrozen(world)


def test_missing_binding_cannot_start_a_handoff(world):
    world.bots.update_by_owner("service", "A", {"binding_id": 999999})
    with pytest.raises(HandoffError, match="HANDOFF_RUNTIME_TARGET_INVALID"):
        begin(world)
    assert_unfrozen(world)


@pytest.mark.parametrize("field,value", [
    ("status", "PENDING"), ("status", "FAILED"), ("status", "RELEASED"),
    ("device_id", ""), ("device_provider", ""), ("env", "prod"),
])
def test_invalid_draft_metadata_is_rejected(world, field, value):
    with world.db.orm_session() as db:
        setattr(db.get(EntityDeviceBinding, world.bot["binding_id"]), field, value)
    with pytest.raises(HandoffError, match="HANDOFF_RUNTIME_TARGET_INVALID"):
        begin(world)
    assert_unfrozen(world)


@pytest.mark.parametrize("status,stage", [("success", "online"), ("validating", "verify")])
def test_all_running_publish_stages_are_checked(world, status, stage):
    publish(world, status, {"binding": {stage: 999999}})
    with pytest.raises(HandoffError, match="HANDOFF_RUNTIME_TARGET_INVALID"):
        begin(world)
    assert_unfrozen(world)


@pytest.mark.parametrize("status", ["success", "validating"])
def test_running_publish_record_requires_its_stage_binding(world, status):
    publish(world, status, {})
    with pytest.raises(HandoffError, match="HANDOFF_RUNTIME_TARGET_INVALID"):
        begin(world)
    assert_unfrozen(world)


def test_no_targets_is_rejected_without_changing_bot(world):
    world.bots.update_by_owner("service", "A", {"binding_id": None})
    with pytest.raises(HandoffError, match="HANDOFF_RUNTIME_TARGET_REQUIRED"):
        begin(world)
    assert_unfrozen(world)


@pytest.mark.parametrize("kind", ["online", "caller"])
def test_no_draft_binding_is_compatible_when_another_target_exists(world, kind):
    world.bots.update_by_owner("service", "A", {"binding_id": None})
    if kind == "online":
        publish(world, "success", {"binding": {"online": bind(world)}})
    else:
        bind(world, reason="caller_instance:service")
    assert world.repo.get(begin(world))["phase"] == "QUEUED"


def test_broken_active_caller_target_is_checked_even_with_valid_draft(world):
    bind(world, name="", reason="caller_instance:service")
    with pytest.raises(HandoffError, match="HANDOFF_RUNTIME_TARGET_INVALID"):
        begin(world)
    assert_unfrozen(world)


def test_completed_or_inactive_publish_records_do_not_add_runtime_targets(world):
    publish(world, "upgraded", {"binding": {"online": 999999}})
    assert world.repo.get(begin(world))["phase"] == "QUEUED"


def test_runtime_target_lookup_uses_entity_after_a_previous_handoff(world, monkeypatch):
    with world.db.orm_session() as db:
        db.get(BotModel, world.bot["id"]).owner_id = "C"
    publish(world, "success", {"binding": {"online": bind(world)}})
    reads = Mock(wraps=world.publishes.get_by_publish_bot_id)
    monkeypatch.setattr(world.publishes, "get_by_publish_bot_id", reads)
    row = world.service().create(
        bot_pk=world.bot["id"], actor_id="C", new_owner_id="B", request_key="second-handoff",
    )
    assert row["old_owner_id"] == "C" and row["entity_id"] == "A"
    assert all(call.kwargs["owner_id"] == "A" for call in reads.call_args_list)


@pytest.mark.parametrize("phase", ["QUEUED", "SUCCEEDED", "CANCELLED"])
def test_idempotent_replay_does_not_depend_on_preflight_or_directory(world, monkeypatch, phase):
    op = begin(world)
    if phase == "SUCCEEDED":
        drain(world, op)
    elif phase == "CANCELLED":
        world.service().cancel(operation_id=op, actor_id="A")
    failure = Mock(side_effect=AssertionError("replays must not recheck external state"))
    monkeypatch.setattr(world.passport, "owner_handoff_supported", failure)
    monkeypatch.setattr(world.devices, "get_by_id", failure)
    monkeypatch.setattr(world.service()._staff.__class__, "get_user_by_work_no", failure)
    assert begin(world) == op
    assert world.repo.get(op)["phase"] == phase
    failure.assert_not_called()


def test_invalid_actor_is_rejected_before_preflight(world, monkeypatch):
    query = Mock(side_effect=AssertionError("unauthorized request must not query Passport"))
    monkeypatch.setattr(world.passport, "owner_handoff_supported", query)
    with pytest.raises(HandoffError, match="OWNER_REQUIRED"):
        world.service().create(
            bot_pk=world.bot["id"], actor_id="C", new_owner_id="B", request_key="unrelated",
        )
    query.assert_not_called()
    assert_unfrozen(world)

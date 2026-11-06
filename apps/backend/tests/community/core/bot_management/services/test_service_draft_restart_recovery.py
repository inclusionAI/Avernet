"""Service-draft retries select lifecycle-scoped history before status checks."""
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.community.core.bot_management.services.test_bot_service_restart_idempotency import (
    _make_bot,
    _make_service,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
)


CALLBACK_SENTINEL = "synthetic-callback-never-log"


def _binding(binding_id=42, *, status="RELEASED", provider="arca", props=None,
             apply_reason="", entity_id="staff_user001", env="dev"):
    lifecycle_props = {
        "bolt_id": "bot001",
        "entity_id": "staff_user001",
        "entity_type": "staff",
        "callback_token": CALLBACK_SENTINEL,
    }
    return SimpleNamespace(
        id=binding_id, entity_id=entity_id, entity_type="staff", env=env,
        device_provider=provider, status=status, apply_reason=apply_reason,
        device_props=lifecycle_props if props is None else props,
    )


def _service_with_history(bindings, *, lock_repo=None):
    svc = _make_service(lock_repo)
    bot = _make_bot(status="FAILED", binding_id=None, bot_type="service",
                    active_engine="openclaw")
    svc._repository.get_by_id_and_owner.return_value = bot
    svc._device_binding_repo.list_bindings.return_value = (len(bindings), bindings)
    return svc, bot


def test_failed_draft_skips_newer_pending_caller_before_recovering_provider(caplog):
    caplog.set_level(logging.INFO, logger="start")
    caller = _binding(43, status="PENDING", provider="baas",
                      props={"bolt_id": "bot001"},
                      apply_reason="caller_instance:bot001")
    draft = _binding()
    svc, bot = _service_with_history([caller, draft])

    with patch.object(svc, "stop_bot") as stop, \
         patch.object(svc, "start_bot", return_value=bot) as start:
        assert svc.restart_bot(bot_id="bot001", user_id="user001") == bot

    assert start.call_args.kwargs["device_provider"] == "arca"
    assert svc._restart_lock_repo.acquire_calls == 1
    stop.assert_not_called()
    assert draft.status == "RELEASED"
    assert "caller" in caplog.text
    assert "binding_id=43" in caplog.text
    assert CALLBACK_SENTINEL not in caplog.text


def test_unknown_service_history_refuses_before_old_draft():
    unknown = _binding(43, status="RELEASED", props={"bolt_id": "bot001"})
    svc, _ = _service_with_history([unknown, _binding()])
    with patch.object(svc, "start_bot") as start:
        with pytest.raises(BotInvalidLifecycleStateError) as error:
            svc.restart_bot(bot_id="bot001", user_id="user001")
    assert error.value.current_status == "FAILED_WITHOUT_BINDING"
    assert svc._restart_lock_repo.acquire_calls == 0
    start.assert_not_called()


@pytest.mark.parametrize("foreign_props,reason", [
    ({"bolt_id": "bot001", "publish_id": 70}, ""),
    ({"bolt_id": "bot001", "baas_publish_id": 70}, ""),
    ({"bolt_id": "bot001", "destroy_publish_id": 70}, ""),
    ({"bolt_id": "bot001", "AGENTCLAW_DEFAULT_TAG": "eval"}, ""),
    ({"bolt_id": "bot001", "default_tag": "eval"}, ""),
    ({**_binding().device_props, "stage": "online"}, ""),
    (_binding().device_props, "caller_instance:bot001"),
])
def test_known_foreign_scope_is_skipped_before_live_status(foreign_props, reason):
    foreign = _binding(43, status="ACTIVE", provider="baas", props=foreign_props,
                       apply_reason=reason)
    svc, bot = _service_with_history([foreign, _binding()])
    with patch.object(svc, "start_bot", return_value=bot) as start:
        svc.restart_bot(bot_id="bot001", user_id="user001")
    assert start.call_args.kwargs["device_provider"] == "arca"


@pytest.mark.parametrize("status", ["RELEASED", "FAILED", "STOPPED"])
def test_terminal_managed_baas_draft_with_publish_id_can_supply_provider(status):
    draft = _binding(status=status, provider="baas")
    draft.device_props["publish_id"] = 70
    svc, bot = _service_with_history([draft])
    with patch.object(svc, "_restart_bot_baas") as in_place, \
         patch.object(svc, "start_bot", return_value=bot) as start:
        svc.restart_bot(bot_id="bot001", user_id="user001")
    assert start.call_args.kwargs["device_provider"] == "baas"
    in_place.assert_not_called()
    assert svc._repository.update_by_owner.call_args.args[2]["binding_id"] is None


@pytest.mark.parametrize("status", ["ACTIVE", "PENDING"])
def test_live_managed_draft_blocks_retry_before_older_history(status):
    svc, _ = _service_with_history([_binding(43, status=status), _binding()])
    with patch.object(svc, "start_bot") as start:
        with pytest.raises(BotInvalidLifecycleStateError):
            svc.restart_bot(bot_id="bot001", user_id="user001")
    start.assert_not_called()
    assert svc._restart_lock_repo.acquire_calls == 0


@pytest.mark.parametrize("field,value", [
    ("callback_token", None), ("callback_token", ""), ("callback_token", 7),
    ("callback_token", "  "), ("entity_id", "other-entity"),
    ("entity_type", "team"),
])
def test_missing_or_mismatched_lifecycle_identity_is_not_guessed(field, value, caplog):
    caplog.set_level(logging.INFO, logger="start")
    draft = _binding()
    draft.device_props[field] = value
    svc, _ = _service_with_history([draft, _binding(41)])
    with patch.object(svc, "start_bot") as start:
        with pytest.raises(BotInvalidLifecycleStateError):
            svc.restart_bot(bot_id="bot001", user_id="user001")
    assert "scope=unknown" in caplog.text
    assert "entity_id=staff_user001" in caplog.text
    assert CALLBACK_SENTINEL not in caplog.text
    start.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("entity_id", "other-entity"), ("entity_type", "team"), ("env", "prod"),
])
def test_record_for_another_resource_is_skipped(field, value):
    foreign = _binding(43, status="ACTIVE")
    setattr(foreign, field, value)
    svc, bot = _service_with_history([foreign, _binding()])
    with patch.object(svc, "start_bot", return_value=bot) as start:
        svc.restart_bot(bot_id="bot001", user_id="user001")
    assert start.call_args.kwargs["device_provider"] == "arca"


def test_other_bot_is_skipped_and_repository_keeps_exact_scope():
    other_bot = _binding(43, status="ACTIVE")
    other_bot.device_props["bolt_id"] = "bot001-suffix"
    svc, bot = _service_with_history([other_bot, _binding()])
    with patch.object(svc, "start_bot", return_value=bot):
        svc.restart_bot(bot_id="bot001", user_id="user001")
    svc._device_binding_repo.list_bindings.assert_called_once_with(
        env="dev", entity_id="staff_user001", entity_type="staff", status=None,
        page=1, page_size=100,
    )


def test_complete_bot_id_required_for_caller_marker():
    draft = _binding(apply_reason="caller_instance:bot001-suffix")
    svc, bot = _service_with_history([draft])
    with patch.object(svc, "start_bot", return_value=bot) as start:
        svc.restart_bot(bot_id="bot001", user_id="user001")
    assert start.call_args.kwargs["device_provider"] == "arca"


def test_known_foreign_rows_can_be_skipped_across_pages():
    caller = _binding(99, status="PENDING", props={"bolt_id": "bot001"},
                      apply_reason="caller_instance:bot001")
    svc, bot = _service_with_history([])
    svc._device_binding_repo.list_bindings.side_effect = [
        (101, [caller] * 100), (101, [_binding()]),
    ]
    with patch.object(svc, "start_bot", return_value=bot) as start:
        svc.restart_bot(bot_id="bot001", user_id="user001")
    assert start.call_args.kwargs["device_provider"] == "arca"
    assert [call.kwargs["page"] for call in
            svc._device_binding_repo.list_bindings.call_args_list] == [1, 2]


@pytest.mark.parametrize("history", [[], [_binding(provider="")]])
def test_no_trusted_provider_does_not_allocate(history):
    svc, _ = _service_with_history(history)
    with patch.object(svc, "start_bot") as start:
        with pytest.raises(BotInvalidLifecycleStateError):
            svc.restart_bot(bot_id="bot001", user_id="user001")
    start.assert_not_called()


def test_unbound_service_retry_obeys_existing_held_lock():
    svc, bot = _service_with_history([_binding()])
    svc._restart_lock_repo.acquire("dev", "staff_user001", "bot001", "user001")
    with patch.object(svc, "start_bot") as start:
        result = svc.restart_bot(bot_id="bot001", user_id="user001")
    assert result["status"] == "PENDING"
    assert result["binding_id"] is None
    assert svc._restart_lock_repo.acquire_calls == 3
    start.assert_not_called()


def test_failed_allocation_releases_lock_and_next_retry_writes_new_binding():
    from unittest.mock import MagicMock
    from tests.community.core.bot_management.services.test_bot_service_restart_idempotency import (
        _stateful_bot_repository,
        _SyncThread,
    )

    draft = _binding()
    svc, bot = _service_with_history([draft])
    repository, state = _stateful_bot_repository(bot)
    svc._repository = repository
    device_service = MagicMock()
    device_service.apply_device.side_effect = [
        RuntimeError("synthetic allocation failure"),
        SimpleNamespace(id=84, device_id="fresh-device", device_provider="arca",
                        status="ACTIVE"),
    ]
    svc._device_service_provider = lambda: device_service
    svc._skill_set_factory.create.return_value.get_symlink_mappings.return_value = []
    svc._template_service.get_template_config.return_value = None
    svc._query_admin_worknos = MagicMock(return_value=[])

    with patch("agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
               _SyncThread), \
         patch.object(svc, "_register_bot_to_bcn_as_provider"):
        svc.restart_bot(bot_id="bot001", user_id="user001")
        assert state["status"] == "FAILED"
        assert state["binding_id"] is None
        assert not svc._restart_lock_repo.rows
        result = svc.restart_bot(bot_id="bot001", user_id="user001")

    assert result["binding_id"] == state["binding_id"] == 84
    assert state["device_id"] == "fresh-device"
    assert state["status"] == "ACTIVE"
    assert device_service.apply_device.call_count == 2
    assert all(call.kwargs["device_provider"] == "arca" for call in
               device_service.apply_device.call_args_list)
    assert svc._restart_lock_repo.release_calls == 2
    assert draft.id == 42 and draft.status == "RELEASED"

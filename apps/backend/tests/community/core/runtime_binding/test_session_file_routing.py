"""Session-file policy is scoped to this resolver entry point, not global AUTO."""

from dataclasses import replace

import pytest

from agentclaw.community.core.runtime_binding.errors import (
    CallerInstanceNotReadyError, RuntimeBindingNotFoundError,
)
from agentclaw.community.core.runtime_binding.models import (
    RuntimeBindingRequest, RuntimeBindingSource,
)
from tests.community.core.runtime_binding.test_service import (
    BOT, CALLER, OWNER, _Binding, _PublishRecord, _service,
)


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "teclaw"])
@pytest.mark.parametrize("call_type", ["caller", "owner"])
@pytest.mark.parametrize("stage,binding_id,source", [
    ("draft", 12, RuntimeBindingSource.SERVICE_DRAFT),
    ("verify", 31, RuntimeBindingSource.SERVICE_VERIFY),
    ("online", 31, RuntimeBindingSource.SERVICE_ONLINE),
])
def test_operator_without_friend_uses_requested_service_stage(engine, call_type, stage, binding_id, source):
    service, instances = _service(
        {"id": 3, "bot_type": "service", "call_type": call_type,
         "active_engine": engine, "binding_id": 12},
        bindings={12: _Binding(12), 31: _Binding(31),
                  41: _Binding(41, apply_reason=f"caller_instance:{BOT}", applied_by=CALLER)},
        records=[_PublishRecord(31)],
        caller_instance={"status": "success", "ext": {"binding_id": 41}},
    )
    request = RuntimeBindingRequest(BOT, OWNER, CALLER, stage)
    result = service.resolve_session_file(request)
    assert (result.binding_id, result.source) == (binding_id, source)
    assert instances.calls == []
    assert service._bot_repository.calls == [(BOT, OWNER)]
    assert request.actor_user_id == CALLER


@pytest.mark.parametrize("engine", ["openclaw", "claude_code"])
@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
@pytest.mark.parametrize("actor", [CALLER, OWNER])
def test_explicit_friend_uses_subject_instance_for_self_and_owner_inspection(engine, stage, actor):
    service, instances = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller",
         "active_engine": engine, "binding_id": 12},
        bindings={12: _Binding(12), 31: _Binding(31),
                  41: _Binding(41, apply_reason=f"caller_instance:{BOT}", applied_by=CALLER)},
        records=[_PublishRecord(31)],
        caller_instance={"status": "success", "ext": {"binding_id": 41}},
    )
    request = RuntimeBindingRequest(BOT, OWNER, actor, stage)
    result = service.resolve_session_file(request, session_user_id=CALLER)
    assert (result.binding_id, result.source) == (41, RuntimeBindingSource.CALLER_INSTANCE)
    assert instances.calls == [(CALLER, BOT, OWNER)]
    assert service._bot_repository.calls == [(BOT, OWNER)]
    assert request.actor_user_id == actor


@pytest.mark.parametrize("engine,call_type", [("teclaw", "caller"), ("openclaw", "owner")])
@pytest.mark.parametrize("stage,binding_id,source", [
    ("draft", 12, RuntimeBindingSource.SERVICE_DRAFT),
    ("verify", 31, RuntimeBindingSource.SERVICE_VERIFY),
    ("online", 31, RuntimeBindingSource.SERVICE_ONLINE),
])
def test_shared_friend_runtime_keeps_stage_binding_without_instance_lookup(engine, call_type, stage, binding_id, source):
    service, instances = _service(
        {"id": 3, "bot_type": "service", "call_type": call_type,
         "active_engine": engine, "binding_id": 12},
        bindings={12: _Binding(12), 31: _Binding(31)}, records=[_PublishRecord(31)],
    )
    result = service.resolve_session_file(
        RuntimeBindingRequest(BOT, OWNER, OWNER, stage), session_user_id=CALLER,
    )
    assert (result.binding_id, result.source) == (binding_id, source)
    assert instances.calls == []


@pytest.mark.parametrize("subject", [None, CALLER])
def test_personal_files_use_draft_binding(subject):
    service, instances = _service(
        {"id": 1, "bot_type": "personal", "binding_id": 12}, bindings={12: _Binding(12)},
    )
    result = service.resolve_session_file(
        RuntimeBindingRequest(BOT, OWNER, OWNER), session_user_id=subject,
    )
    assert (result.binding_id, result.source) == (12, RuntimeBindingSource.PERSONAL)
    assert instances.calls == []


@pytest.mark.parametrize("instance", [None, {"status": "failed", "ext": {"binding_id": 41}},
    {"status": "init", "ext": {"binding_id": 41}},
    {"status": "success", "ext": {}},
])
def test_friend_without_successful_instance_never_falls_back_to_service(instance):
    service, _ = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller", "binding_id": 12},
        bindings={12: _Binding(12), 31: _Binding(31)}, records=[_PublishRecord(31)],
        caller_instance=instance,
    )
    with pytest.raises(CallerInstanceNotReadyError):
        service.resolve_session_file(RuntimeBindingRequest(BOT, OWNER, OWNER), session_user_id=CALLER)


@pytest.mark.parametrize("changes", [
    {"status": "INACTIVE"}, {"entity_id": "other-owner"}, {"applied_by": OWNER},
    {"apply_reason": "caller_instance:other-bot"}, {"env": "prod"}, {"device_provider": "teclaw"},
])
def test_friend_binding_scope_is_checked_against_subject_without_fallback(changes, caplog):
    binding = replace(_Binding(41, apply_reason=f"caller_instance:{BOT}", applied_by=CALLER), **changes)
    service, instances = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller", "binding_id": 12},
        bindings={12: _Binding(12), 31: _Binding(31), 41: binding},
        records=[_PublishRecord(31)],
        caller_instance={"status": "success", "ext": {
            "binding_id": 41, "credentials": {"token": "<test-friend-token>"},
        }},
    )
    with caplog.at_level("INFO"), pytest.raises(RuntimeBindingNotFoundError):
        service.resolve_session_file(RuntimeBindingRequest(BOT, OWNER, OWNER), session_user_id=CALLER)
    assert instances.calls == [(CALLER, BOT, OWNER)]
    assert "<test-friend-token>" not in caplog.text


def test_session_file_selection_does_not_change_global_auto():
    service, instances = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller", "binding_id": 12},
        bindings={12: _Binding(12), 41: _Binding(41, apply_reason=f"caller_instance:{BOT}", applied_by=CALLER)},
        caller_instance={"status": "success", "ext": {"binding_id": 41}},
    )
    request = RuntimeBindingRequest(BOT, OWNER, CALLER)
    assert service.resolve_session_file(request).binding_id == 12
    assert service.resolve(request).binding_id == 41
    assert instances.calls == [(CALLER, BOT, OWNER)]


def test_friend_missing_binding_does_not_use_available_shared_binding():
    service, _ = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller", "binding_id": 12},
        bindings={12: _Binding(12), 31: _Binding(31)}, records=[_PublishRecord(31)],
        caller_instance={"status": "success", "ext": {"binding_id": 41}},
    )
    with pytest.raises(RuntimeBindingNotFoundError):
        service.resolve_session_file(RuntimeBindingRequest(BOT, OWNER, OWNER), session_user_id=CALLER)


def test_selection_log_keeps_actor_subject_and_binding_without_credentials(caplog):
    service, _ = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller", "binding_id": 12},
        bindings={41: _Binding(41, apply_reason=f"caller_instance:{BOT}", applied_by=CALLER)},
        caller_instance={"status": "success", "ext": {
            "binding_id": 41, "credentials": {"token": "<test-friend-token>"},
        }},
    )
    with caplog.at_level("INFO"):
        service.resolve_session_file(RuntimeBindingRequest(BOT, OWNER, OWNER), session_user_id=CALLER)
    assert "session_files.binding.selected" in caplog.text
    assert f"actor_user_id={OWNER}" in caplog.text
    assert f"session_user_id={CALLER}" in caplog.text
    assert "binding_id=41" in caplog.text
    assert "source=caller_instance" in caplog.text
    assert "<test-friend-token>" not in caplog.text

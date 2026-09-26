from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from agentclaw.community.core.runtime_binding.errors import (
    CallerInstanceNotReadyError,
    RuntimeBindingNotFoundError,
    RuntimeBotNotFoundError,
)
from agentclaw.community.core.runtime_binding.models import (
    RuntimeBindingRequest,
    RuntimeBindingSource,
    RuntimeBindingTarget,
)
from agentclaw.community.core.runtime_binding.service import (
    RuntimeBindingResolutionService,
)

BOT = "service-bot"
OWNER = "owner-1"
CALLER = "caller-1"


@dataclass
class _Binding:
    id: int
    status: str = "ACTIVE"
    device_provider: str = "baas"
    entity_id: str = OWNER
    apply_reason: str | None = None
    applied_by: str = OWNER
    env: str = "dev"


class _BotRepository:
    def __init__(self, bot: dict) -> None:
        self.bot = bot
        self.calls = []

    def get_by_id_and_owner(self, _bot_id: str, _owner_id: str) -> dict:
        self.calls.append((_bot_id, _owner_id))
        return self.bot


class _PublishRecord:
    id = 1
    status = "success"

    def __init__(self, binding_id: int) -> None:
        self.ext = {"binding": {"online": binding_id, "verify": binding_id}}


class _PublishRepository:
    def __init__(self, records: list[_PublishRecord] | None = None) -> None:
        self.records = records or []

    def list_by_source_bot(self, _bot_pk: int, _env: str) -> list[_PublishRecord]:
        return self.records


class _BindingRepository:
    def __init__(self, bindings: dict[int, _Binding]) -> None:
        self.bindings = bindings

    def get_by_id(self, binding_id: int) -> _Binding | None:
        return self.bindings.get(binding_id)


class _CallerInstanceRepository:
    def __init__(self, instance: dict | None = None) -> None:
        self.instance = instance
        self.calls: list[tuple[str, str, str]] = []

    def get_instance(self, user_id: str, bot_id: str, owner_id: str) -> dict | None:
        self.calls.append((user_id, bot_id, owner_id))
        return self.instance


def _service(
    bot: dict,
    *,
    bindings: dict[int, _Binding],
    records: list[_PublishRecord] | None = None,
    caller_instance: dict | None = None,
) -> tuple[RuntimeBindingResolutionService, _CallerInstanceRepository]:
    caller_repository = _CallerInstanceRepository(caller_instance)
    return (
        RuntimeBindingResolutionService(
            bot_repository=_BotRepository(bot),
            publish_repository=_PublishRepository(records),
            binding_repository=_BindingRepository(bindings),
            caller_instance_repository=caller_repository,
            environment_provider=lambda: "dev",
        ),
        caller_repository,
    )


def test_personal_bot_resolves_its_draft_binding():
    service, _ = _service(
        {"id": 1, "bot_type": "personal", "binding_id": 11},
        bindings={11: _Binding(11)},
    )

    resolved = service.resolve(RuntimeBindingRequest(BOT, OWNER, OWNER))

    assert resolved.binding_id == 11
    assert resolved.source is RuntimeBindingSource.PERSONAL


def test_service_online_resolves_the_published_binding():
    service, _ = _service(
        {"id": 2, "bot_type": "service", "call_type": "owner", "binding_id": 12},
        bindings={31: _Binding(31)},
        records=[_PublishRecord(31)],
    )

    resolved = service.resolve(RuntimeBindingRequest(BOT, OWNER, OWNER, "online"))

    assert resolved.binding_id == 31
    assert resolved.source is RuntimeBindingSource.SERVICE_ONLINE


def test_caller_bot_uses_the_authenticated_users_online_instance_record():
    service, caller_repository = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller"},
        bindings={
            41: _Binding(
                41,
                entity_id=OWNER,
                apply_reason=f"caller_instance:{BOT}",
                applied_by=CALLER,
            )
        },
        caller_instance={"status": "success", "ext": {"binding_id": 41}},
    )

    resolved = service.resolve(RuntimeBindingRequest(BOT, OWNER, CALLER, "online"))

    assert resolved.binding_id == 41
    assert resolved.source is RuntimeBindingSource.CALLER_INSTANCE
    assert caller_repository.calls == [(CALLER, BOT, OWNER)]


def test_caller_bot_can_explicitly_resolve_its_shared_online_service_binding():
    service, caller_repository = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller", "binding_id": 12},
        bindings={31: _Binding(31)},
        records=[_PublishRecord(31)],
        caller_instance={"status": "success", "ext": {"binding_id": 41}},
    )

    resolved = service.resolve(
        RuntimeBindingRequest(
            BOT,
            OWNER,
            CALLER,
            "online",
            target=RuntimeBindingTarget.CALLER_SERVICE,
        )
    )

    assert resolved.binding_id == 31
    assert resolved.source is RuntimeBindingSource.SERVICE_ONLINE
    assert caller_repository.calls == []


def test_caller_bot_can_explicitly_resolve_its_caller_instance():
    service, caller_repository = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller"},
        bindings={41: _Binding(
            41,
            entity_id=OWNER,
            apply_reason=f"caller_instance:{BOT}",
            applied_by=CALLER,
        )},
        caller_instance={"status": "success", "ext": {"binding_id": 41}},
    )

    resolved = service.resolve(
        RuntimeBindingRequest(
            BOT,
            OWNER,
            CALLER,
            "online",
            target=RuntimeBindingTarget.CALLER_INSTANCE,
        )
    )

    assert resolved.binding_id == 41
    assert resolved.source is RuntimeBindingSource.CALLER_INSTANCE
    assert caller_repository.calls == [(CALLER, BOT, OWNER)]


def test_caller_bot_does_not_fall_back_when_its_instance_is_missing():
    service, _ = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller"},
        bindings={31: _Binding(31)},
        records=[_PublishRecord(31)],
    )

    with pytest.raises(CallerInstanceNotReadyError):
        service.resolve(RuntimeBindingRequest(BOT, OWNER, CALLER, "online"))


def test_inactive_binding_is_rejected_without_device_selection():
    service, _ = _service(
        {"id": 1, "bot_type": "personal", "binding_id": 11},
        bindings={11: _Binding(11, status="INACTIVE")},
    )

    with pytest.raises(RuntimeBindingNotFoundError):
        service.resolve(RuntimeBindingRequest(BOT, OWNER, OWNER))


@pytest.mark.parametrize(
    ("bot_id", "owner_id", "actor_id"),
    [(BOT, "TEAM_OWNER_42", CALLER), ("Service-Bot", OWNER, CALLER),
     (BOT, OWNER, "Caller-42"), ("Service-Bot", "TEAM_OWNER_42", "Caller-42")],
)
def test_caller_binding_preserves_matching_identity_case(bot_id, owner_id, actor_id):
    service, caller_repository = _service(
        {"id": 3, "bot_type": "SERVICE", "call_type": "CALLER"},
        bindings={41: _Binding(
            41, entity_id=owner_id, apply_reason=f"caller_instance:{bot_id}",
            applied_by=actor_id, env="DEV", device_provider="BAAS", status="active",
        )},
        caller_instance={"status": "SUCCESS", "ext": {"binding_id": 41}},
    )
    resolved = service.resolve(RuntimeBindingRequest(bot_id, owner_id, actor_id, "online"))
    assert resolved.binding_id == 41
    assert resolved.source is RuntimeBindingSource.CALLER_INSTANCE
    assert caller_repository.calls == [(actor_id, bot_id, owner_id)]
    assert service._bot_repository.calls == [(bot_id, owner_id)]


@pytest.mark.parametrize(("field", "value"), [
    ("entity_id", "other-owner"), ("entity_id", OWNER.upper()),
    ("applied_by", "other-caller"), ("applied_by", CALLER.upper()),
    ("apply_reason", "caller_instance:other-bot"),
    ("apply_reason", f"caller_instance:{BOT.upper()}"),
    ("entity_id", None), ("applied_by", None), ("apply_reason", None),
    ("env", "prod"), ("device_provider", "arca"),
])
def test_caller_binding_scope_rejects_mismatch_and_logs_field(field, value, caplog):
    binding = _Binding(41, apply_reason=f"caller_instance:{BOT}", applied_by=CALLER)
    setattr(binding, field, value)
    binding.token = "synthetic-sensitive-marker"
    service, _ = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller"},
        bindings={41: binding},
        caller_instance={"status": "success", "ext": {
            "binding_id": 41, "credentials": {"token": binding.token},
        }},
    )
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeBindingNotFoundError, match="Caller binding scope is invalid"):
            service.resolve(RuntimeBindingRequest(BOT, OWNER, CALLER, "online"))
    assert "caller_binding_scope_invalid" in caplog.text
    assert f"field={field}" in caplog.text
    assert "binding_id=41" in caplog.text
    assert binding.token not in caplog.text


def test_success_does_not_log_scope_rejection_or_credentials(caplog):
    binding = _Binding(41, apply_reason=f"caller_instance:{BOT}", applied_by=CALLER)
    binding.token = "synthetic-sensitive-marker"
    service, _ = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller"},
        bindings={41: binding},
        caller_instance={"status": "success", "ext": {
            "binding_id": 41, "credentials": {"token": binding.token},
        }},
    )
    with caplog.at_level(logging.DEBUG):
        assert service.resolve(RuntimeBindingRequest(BOT, OWNER, CALLER)).binding_id == 41
    assert "caller_binding_scope_invalid" not in caplog.text
    assert binding.token not in caplog.text


@pytest.mark.parametrize("binding_id", [None, True, 0, -1, "41"])
def test_caller_rejects_invalid_binding_id(binding_id):
    service, _ = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller"},
        bindings={}, caller_instance={"status": "success", "ext": {"binding_id": binding_id}},
    )
    with pytest.raises(CallerInstanceNotReadyError, match="binding is unavailable"):
        service.resolve(RuntimeBindingRequest(BOT, OWNER, CALLER))


@pytest.mark.parametrize(("status", "allowed_id", "ready"), [
    ("init", None, False), ("init", 42, False), ("init", 41, True),
    ("failed", 41, False), ("success", None, True),
])
def test_caller_initialization_allowlist_remains_exact(status, allowed_id, ready):
    service, _ = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller"},
        bindings={41: _Binding(41, apply_reason=f"caller_instance:{BOT}", applied_by=CALLER)},
        caller_instance={"status": status, "ext": {"binding_id": 41}},
    )
    request = RuntimeBindingRequest(BOT, OWNER, CALLER, allow_initializing_caller_binding_id=allowed_id)
    if ready:
        assert service.resolve(request).binding_id == 41
    else:
        with pytest.raises(CallerInstanceNotReadyError, match="not ready"):
            service.resolve(request)


@pytest.mark.parametrize(("bot", "stage", "target", "error"), [
    ({}, "draft", RuntimeBindingTarget.AUTO, RuntimeBotNotFoundError),
    ({"bot_type": "personal"}, "online", RuntimeBindingTarget.AUTO, RuntimeBindingNotFoundError),
    ({"bot_type": "personal"}, "draft", RuntimeBindingTarget.CALLER_SERVICE, RuntimeBindingNotFoundError),
    ({"bot_type": "personal"}, "draft", RuntimeBindingTarget.AUTO, RuntimeBindingNotFoundError),
    ({"bot_type": "service"}, "online", RuntimeBindingTarget.AUTO, RuntimeBotNotFoundError),
])
def test_shared_binding_unavailable_boundaries(bot, stage, target, error):
    service, _ = _service(bot, bindings={})
    with pytest.raises(error):
        service.resolve(RuntimeBindingRequest(BOT, OWNER, OWNER, stage, target=target))


@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
@pytest.mark.parametrize("target", [RuntimeBindingTarget.AUTO, RuntimeBindingTarget.CALLER_SERVICE])
def test_shared_service_stage_selection(stage, target):
    service, _ = _service(
        {"id": 3, "bot_type": "service", "call_type": "owner", "binding_id": 31},
        bindings={31: _Binding(31)}, records=[_PublishRecord(31)],
    )
    resolved = service.resolve(RuntimeBindingRequest(BOT, OWNER, OWNER, stage, target=target))
    assert resolved.binding_id == 31
    assert resolved.source.value == f"service_{stage}"


@pytest.mark.parametrize("binding", [None, _Binding(41, status="INACTIVE")])
def test_caller_requires_existing_active_binding(binding):
    service, _ = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller"},
        bindings={} if binding is None else {41: binding},
        caller_instance={"status": "success", "ext": {"binding_id": 41}},
    )
    with pytest.raises(RuntimeBindingNotFoundError, match="runtime binding is inactive"):
        service.resolve(RuntimeBindingRequest(BOT, OWNER, CALLER))


@pytest.mark.parametrize("ext", [None, "invalid"])
def test_caller_rejects_missing_or_malformed_binding_metadata(ext):
    service, _ = _service(
        {"id": 3, "bot_type": "service", "call_type": "caller"},
        bindings={}, caller_instance={"status": "success", "ext": ext},
    )
    with pytest.raises(CallerInstanceNotReadyError, match="binding is unavailable"):
        service.resolve(RuntimeBindingRequest(BOT, OWNER, CALLER))

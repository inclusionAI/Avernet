from __future__ import annotations

from dataclasses import dataclass

import pytest

from agentclaw.community.core.runtime_binding.errors import (
    CallerInstanceNotReadyError,
    RuntimeBindingNotFoundError,
)
from agentclaw.community.core.runtime_binding.models import (
    RuntimeBindingRequest,
    RuntimeBindingSource,
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

    def get_by_id_and_owner(self, _bot_id: str, _owner_id: str) -> dict:
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

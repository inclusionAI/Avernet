"""SQLite integration tests for digital employee identity persistence."""

from __future__ import annotations

import json

import pytest

from agentclaw.community.core.digital_employee.contracts import (
    DigitalEmployeeError,
    DigitalEmployeeRepositoryProtocol,
)
from agentclaw.community.core.execution_identity.contracts import (
    ExecutionIdentityChangeInProgressError,
    ExecutionIdentityNotFoundError,
    ExecutionIdentityStatus,
    ExecutionIdentityType,
)
from agentclaw.community.core.execution_identity.models import (
    BotExecutionIdentityBindingModel,
)
from agentclaw.community.core.repository.protocols.identity import (
    ExecutionIdentityRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from tests.community.factories.bot_collaborator import make_bot
def _service_bot(world, *, bot_id: str = "employee-repository-bot") -> dict:
    return make_bot(
        world,
        bot_id=bot_id,
        owner_id="employee-owner",
        bot_type="service",
        status="ACTIVE",
        active_engine="openclaw",
    )


def test_employee_binding_is_persisted_and_advances_atomically(world) -> None:
    bot = _service_bot(world)
    make_bot(
        world,
        bot_id="employee-personal-bot",
        owner_id="employee-owner",
        bot_type="personal",
    )
    make_bot(
        world,
        bot_id="other-creators-service-bot",
        owner_id="another-owner",
        bot_type="service",
    )
    repository = world.get(DigitalEmployeeRepositoryProtocol)

    listed = repository.list_service_bots("employee-owner")
    assert [item["id"] for item in listed] == [bot["id"]]
    assert repository.get_bot(bot["id"])["ext"] == {}

    binding = repository.begin_binding(
        bot["id"],
        "AI00000124",
        "event-1",
        {
            "name": "Support Employee",
            "platformCode": "employee-platform",
            "agentId": str(bot["id"]),
        },
    )
    assert binding == {
        "work_no": "AI00000124",
        "event_id": "event-1",
        "status": "BINDING",
        "phase": "DETAIL_READY",
        "name": "Support Employee",
        "platform_code": "employee-platform",
        "agent_id": str(bot["id"]),
    }

    duplicate = repository.begin_binding(
        bot["id"],
        "AI00000124",
        "event-2",
        {"platformCode": "ignored", "agentId": "ignored"},
    )
    assert duplicate == binding
    assert repository.change_binding_phase(
        bot["id"], "another-employee", "DETAIL_READY", "REISSUING"
    ) is False
    assert repository.change_binding_phase(
        bot["id"], "AI00000124", "DETAIL_READY", "REISSUING"
    ) is True
    assert repository.change_binding_phase(
        bot["id"], "AI00000124", "REISSUING", "ACTIVE"
    ) is True

    stored = repository.get_bot(bot["id"])["ext"]["digital_employee"]
    assert stored["phase"] == "ACTIVE"
    assert stored["status"] == "ACTIVE"


def test_employee_repository_rejects_missing_personal_and_conflicting_bots(world) -> None:
    service_bot = _service_bot(world)
    personal_bot = make_bot(
        world,
        bot_id="employee-personal-binding",
        owner_id="employee-owner",
        bot_type="personal",
    )
    repository = world.get(DigitalEmployeeRepositoryProtocol)

    with pytest.raises(DigitalEmployeeError, match="Bot not found"):
        repository.get_bot(999_999)
    with pytest.raises(DigitalEmployeeError, match="Only service"):
        repository.begin_binding(
            personal_bot["id"],
            "AI00000124",
            "event-personal",
            {"platformCode": "employee-platform", "agentId": "personal"},
        )

    repository.begin_binding(
        service_bot["id"],
        "AI00000124",
        "event-original",
        {"platformCode": "employee-platform", "agentId": str(service_bot["id"])},
    )
    with pytest.raises(DigitalEmployeeError, match="different employee"):
        repository.begin_binding(
            service_bot["id"],
            "AI00000999",
            "event-conflict",
            {"platformCode": "employee-platform", "agentId": str(service_bot["id"])},
        )


def test_employee_repository_rejects_non_object_metadata(world) -> None:
    bot = _service_bot(world)
    database = world.get(DatabasePlugin)
    with database.transactional_orm_session() as session:
        row = session.query(BotModel).filter(BotModel.id == bot["id"]).one()
        row.ext = json.dumps(["invalid"])

    repository = world.get(DigitalEmployeeRepositoryProtocol)
    with pytest.raises(DigitalEmployeeError, match="metadata is invalid"):
        repository.get_bot(bot["id"])


def test_execution_identity_lifecycle_supersedes_the_previous_binding(world) -> None:
    bot = _service_bot(world, bot_id="execution-identity-lifecycle")
    repository = world.get(ExecutionIdentityRepositoryProtocol)

    assert repository.get_active(bot_pk=bot["id"]) is None
    assert repository.get_pending(bot_pk=bot["id"]) is None

    first = repository.begin_pending(
        bot_pk=bot["id"],
        execution_workno="employee-one",
        identity_type=ExecutionIdentityType.STAFF,
        modifier_id="operator-one",
    )
    assert repository.get_pending(bot_pk=bot["id"]) == first

    recorded = repository.record_credential_result(
        binding_id=first.id,
        authorization_id="authorization-one",
        credential_id="credential-one",
        agent_id="agent-one",
        credential_status="ISSUED",
        modifier_id="operator-two",
    )
    assert recorded.authorization_id == "authorization-one"
    assert recorded.credential_id == "credential-one"
    assert recorded.agent_id == "agent-one"

    active_first = repository.activate_pending(
        binding_id=first.id,
        modifier_id="operator-three",
    )
    assert active_first.status is ExecutionIdentityStatus.ACTIVE
    assert repository.get_active(bot_pk=bot["id"]) == active_first
    assert repository.get_pending(bot_pk=bot["id"]) is None

    second = repository.begin_pending(
        bot_pk=bot["id"],
        execution_workno="employee-two",
        identity_type=ExecutionIdentityType.DIGITAL_EMPLOYEE,
        modifier_id="operator-four",
    )
    active_second = repository.activate_pending(
        binding_id=second.id,
        modifier_id="operator-five",
    )
    assert active_second.status is ExecutionIdentityStatus.ACTIVE
    assert active_second.execution_workno == "employee-two"
    assert repository.get_active(bot_pk=bot["id"]) == active_second

    database = world.get(DatabasePlugin)
    with database.orm_session() as session:
        previous = session.query(BotExecutionIdentityBindingModel).filter(
            BotExecutionIdentityBindingModel.id == first.id
        ).one()
        assert previous.status == ExecutionIdentityStatus.SUPERSEDED.value
        assert previous.modifier_id == "operator-five"


def test_execution_identity_repository_rejects_invalid_transitions(world) -> None:
    bot = _service_bot(world, bot_id="execution-identity-errors")
    repository = world.get(ExecutionIdentityRepositoryProtocol)
    pending = repository.begin_pending(
        bot_pk=bot["id"],
        execution_workno="employee-pending",
        identity_type=ExecutionIdentityType.DIGITAL_EMPLOYEE,
        modifier_id="operator",
    )

    with pytest.raises(ExecutionIdentityChangeInProgressError):
        repository.begin_pending(
            bot_pk=bot["id"],
            execution_workno="employee-conflict",
            identity_type=ExecutionIdentityType.DIGITAL_EMPLOYEE,
            modifier_id="operator",
        )
    with pytest.raises(ExecutionIdentityNotFoundError, match="Pending binding"):
        repository.record_credential_result(
            binding_id=999_999,
            authorization_id=None,
            credential_id=None,
            agent_id=None,
            credential_status=None,
            modifier_id="operator",
        )
    with pytest.raises(ExecutionIdentityNotFoundError, match="Pending binding"):
        repository.activate_pending(binding_id=999_999, modifier_id="operator")

    repository.mark_failed(
        binding_id=pending.id,
        failure_reason="platform rejected the credential",
        modifier_id="operator-failure",
    )
    assert repository.get_pending(bot_pk=bot["id"]) is None
    repository.mark_failed(
        binding_id=999_999,
        failure_reason="already gone",
        modifier_id="operator",
    )

    database = world.get(DatabasePlugin)
    with database.orm_session() as session:
        failed = session.query(BotExecutionIdentityBindingModel).filter(
            BotExecutionIdentityBindingModel.id == pending.id
        ).one()
        assert failed.status == ExecutionIdentityStatus.FAILED.value
        assert failed.failure_reason == "platform rejected the credential"
        assert failed.modifier_id == "operator-failure"

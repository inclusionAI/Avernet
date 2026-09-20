from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.execution_identity.contracts import (
    ExecutionIdentityBinding,
    ExecutionIdentityNotFoundError,
    ExecutionIdentityOperationNotAllowedError,
    ExecutionIdentityStatus,
    ExecutionIdentityType,
)
from agentclaw.community.core.execution_identity.service import ExecutionIdentityService


def _binding(*, status: ExecutionIdentityStatus) -> ExecutionIdentityBinding:
    return ExecutionIdentityBinding(
        id=8, bot_pk=17, execution_workno="digital-1",
        identity_type=ExecutionIdentityType.DIGITAL_EMPLOYEE, status=status,
    )


def _service() -> tuple[ExecutionIdentityService, MagicMock, MagicMock, MagicMock]:
    bots = MagicMock()
    bots.get_by_id_and_owner.return_value = {
        "id": 17, "bot_id": "bot-1", "owner_id": "owner-1",
        "entity_id": "entity-1", "bot_type": "service",
        "bot_name": "Service Bot", "bot_desc": "description",
        "active_engine": "openclaw",
    }
    bindings, passport, runtime = MagicMock(), MagicMock(), MagicMock()
    return ExecutionIdentityService(bots, bindings, passport, runtime), bindings, passport, runtime


def test_legacy_bot_resolves_owner_when_no_active_binding() -> None:
    service, bindings, _, _ = _service()
    bindings.get_active.return_value = None
    assert service.resolve_execution_workno(bot_pk=17, owner_id="owner-1") == "owner-1"


def test_reissue_persists_pending_before_agentpass_and_does_not_inject() -> None:
    service, bindings, passport, runtime = _service()
    bindings.begin_pending.return_value = _binding(status=ExecutionIdentityStatus.PENDING)
    bindings.record_credential_result.return_value = _binding(status=ExecutionIdentityStatus.PENDING)
    passport.reissue_agent_credentials.return_value = {
        "status": "PENDING", "authorization_id": "auth-1",
        "confirmation_url": "https://confirmation.invalid/id",
    }
    result = service.change_execution_identity(
        bot_id="bot-1", owner_id="owner-1", execution_workno="digital-1",
        identity_type="DIGITAL_EMPLOYEE", action="reissue", modifier_id="operator-1",
    )
    bindings.begin_pending.assert_called_once()
    passport.reissue_agent_credentials.assert_called_once_with(
        bot_id="bot-1", owner_workno="owner-1", entity_id="entity-1",
        execution_workno="digital-1", bot_name="Service Bot",
        bot_desc="description", engine_type="openclaw",
    )
    runtime.hot_update_passport_token_to_device.assert_not_called()
    assert result["status"] == "PENDING"
    assert result["token_injected"] is False


def test_activate_uses_persisted_pending_executor_then_commits() -> None:
    service, bindings, passport, runtime = _service()
    bindings.get_pending.return_value = _binding(status=ExecutionIdentityStatus.PENDING)
    bindings.activate_pending.return_value = _binding(status=ExecutionIdentityStatus.ACTIVE)
    passport.query_auth_status.return_value = {"status": "ISSUED", "token": "secret"}
    passport.query_agent_passport.return_value = {"execution_workno": "digital-1"}
    runtime.hot_update_passport_token_to_device.return_value = {"bindings": [7]}
    result = service.change_execution_identity(
        bot_id="bot-1", owner_id="owner-1", action="activate", modifier_id="operator-1",
    )
    runtime.hot_update_passport_token_to_device.assert_called_once_with(
        bot_id="bot-1", user_id="owner-1", token="secret"
    )
    bindings.activate_pending.assert_called_once_with(binding_id=8, modifier_id="operator-1")
    assert result["status"] == "ACTIVE"
    assert result["token_injected"] is True
    assert "token" not in result


def test_activate_does_not_commit_mismatched_agentpass() -> None:
    service, bindings, passport, runtime = _service()
    bindings.get_pending.return_value = _binding(status=ExecutionIdentityStatus.PENDING)
    passport.query_auth_status.return_value = {"status": "ISSUED", "token": "secret"}
    passport.query_agent_passport.return_value = {"execution_workno": "someone-else"}
    with pytest.raises(ExecutionIdentityOperationNotAllowedError):
        service.change_execution_identity(
            bot_id="bot-1", owner_id="owner-1", action="activate", modifier_id="operator-1",
        )
    runtime.hot_update_passport_token_to_device.assert_not_called()
    bindings.activate_pending.assert_not_called()


def test_activate_requires_a_persisted_pending_binding() -> None:
    service, bindings, _, _ = _service()
    bindings.get_pending.return_value = None
    with pytest.raises(ExecutionIdentityNotFoundError):
        service.change_execution_identity(
            bot_id="bot-1", owner_id="owner-1", action="activate", modifier_id="operator-1",
        )

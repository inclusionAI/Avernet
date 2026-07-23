from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.repository.protocol import (
    BotLookupAmbiguousError,
)
from agentclaw.community.core.caller_identity.credential import (
    CALLER_TARGET_AMBIGUOUS,
    CallerCredentialError,
    CallerToken,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.kernel.device_dto import (
    HeaderOperationRule,
    OutBoundOperationRule,
)


def _bare_baas_service() -> BaasService:
    return object.__new__(BaasService)


def test_caller_identity_uses_supplied_binding_or_falls_back_to_resolution() -> None:
    service = _bare_baas_service()
    service._bot_repo = MagicMock()
    service._bot_repo.get_by_id_and_entity.return_value = {
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "bot_type": "service",
        "status": "ACTIVE",
        "binding_id": 9,
    }
    service._device_binding_repo = MagicMock()
    service._device_binding_repo.get_by_id.return_value = SimpleNamespace(
        status="ACTIVE",
        device_id="baas-bot-1",
    )
    service._outbound_rule_provider = MagicMock()
    service._outbound_rule_provider.build_caller_rule.return_value = (
        OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["https://mcp.example"],
                    action="set",
                    header_name="x-caller-token",
                    value="caller-token",
                )
            ]
        )
    )
    service.list_devices_by_bot_uuid = MagicMock(
        return_value=[{"provider_device_id": "device-1@template-1"}]
    )
    service.append_caller_outbound_rule = MagicMock(return_value=True)
    service._resolve_caller_binding_id = MagicMock(return_value=9)

    update_kwargs = {
        "bot_id": "bot-1",
        "owner_user_id": "owner-1",
        "caller_user_id": "caller-1",
        "caller_token": CallerToken(
            access_token="caller-token",
            subject_user_id="caller-1",
            expires_at=datetime.now(),
            fingerprint="ignored",
        ),
        "stage": "draft",
        "publish_id": None,
        "entity_id": "entity-1",
    }
    service.update_caller_identity(**update_kwargs)

    service._resolve_caller_binding_id.assert_called_once()
    service._device_binding_repo.get_by_id.assert_called_once_with(9)
    service._resolve_caller_binding_id.reset_mock()
    service._device_binding_repo.get_by_id.reset_mock()

    service.update_caller_identity(
        **update_kwargs,
        binding_id=11,
    )

    service._resolve_caller_binding_id.assert_not_called()
    service._device_binding_repo.get_by_id.assert_called_once_with(11)
    assert service._bot_repo.get_by_id_and_entity.call_count == 2
    service._bot_repo.get_by_id_and_owner.assert_not_called()
    service._outbound_rule_provider.build_caller_rule.assert_called_with(
        caller_token="caller-token"
    )
    paas_device_id, outbound_rule = service.append_caller_outbound_rule.call_args.args
    assert paas_device_id == "device-1@template-1"
    assert outbound_rule.header_operation_rules[0].header_name == "x-caller-token"
    assert outbound_rule.header_operation_rules[0].action == "set"
    assert outbound_rule.header_operation_rules[0].value == "caller-token"


def test_caller_identity_prefers_baas_bot_uuid_from_binding_props() -> None:
    service = _bare_baas_service()
    service._bot_repo = MagicMock()
    service._bot_repo.get_by_id_and_entity.return_value = {
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "bot_type": "service",
        "status": "ACTIVE",
    }
    service._device_binding_repo = MagicMock()
    service._device_binding_repo.get_by_id.return_value = SimpleNamespace(
        status="ACTIVE",
        device_id="teamclaw-device-1",
        device_props={"bot_uuid": "baas-bot-1"},
    )
    service._outbound_rule_provider = MagicMock()
    service._outbound_rule_provider.build_caller_rule.return_value = (
        OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["https://mcp.example"],
                    action="set",
                    header_name="x-caller-token",
                    value="caller-token",
                )
            ]
        )
    )
    service.list_devices_by_bot_uuid = MagicMock(
        return_value=[{"provider_device_id": "device-1@template-1"}]
    )
    service.append_caller_outbound_rule = MagicMock(return_value=True)

    service.update_caller_identity(
        bot_id="bot-1",
        owner_user_id="owner-1",
        caller_user_id="caller-1",
        caller_token=CallerToken(
            access_token="caller-token",
            subject_user_id="caller-1",
            expires_at=datetime.now(),
            fingerprint="ignored",
        ),
        stage="draft",
        publish_id=None,
        entity_id="entity-1",
        binding_id=9,
    )

    service.list_devices_by_bot_uuid.assert_called_once_with(
        "baas-bot-1", timeout=3.0
    )


def test_caller_identity_rejects_ambiguous_bot_without_entity_id() -> None:
    service = _bare_baas_service()
    service._bot_repo = MagicMock()
    service._bot_repo.get_unique_by_id.side_effect = BotLookupAmbiguousError

    with pytest.raises(CallerCredentialError) as exc_info:
        service.update_caller_identity(
            bot_id="default",
            owner_user_id="owner-1",
            caller_user_id="caller-1",
            caller_token=CallerToken(
                access_token="caller-token",
                subject_user_id="caller-1",
                expires_at=datetime.now(),
                fingerprint="ignored",
            ),
            stage="draft",
            publish_id=None,
        )

    assert exc_info.value.code == CALLER_TARGET_AMBIGUOUS
    service._bot_repo.get_by_id_and_owner.assert_not_called()


def test_caller_identity_test_exchange_allows_active_personal_bot() -> None:
    service = _bare_baas_service()
    service._bot_repo = MagicMock()
    service._bot_repo.get_by_id_and_entity.return_value = {
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "bot_type": "personal",
        "status": "ACTIVE",
    }
    service._device_binding_repo = MagicMock()
    service._device_binding_repo.get_by_id.return_value = SimpleNamespace(
        status="ACTIVE",
        device_id="baas-bot-1",
    )
    service._outbound_rule_provider = MagicMock()
    service._outbound_rule_provider.build_caller_rule.return_value = (
        OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["https://mcp.example"],
                    action="set",
                    header_name="x-caller-token",
                    value="caller-token",
                )
            ]
        )
    )
    service.list_devices_by_bot_uuid = MagicMock(
        return_value=[{"provider_device_id": "device-1@template-1"}]
    )
    service.append_caller_outbound_rule = MagicMock(return_value=True)

    service.update_caller_identity(
        bot_id="bot-1",
        owner_user_id="owner-1",
        caller_user_id="owner-1",
        caller_token=CallerToken(
            access_token="caller-token",
            subject_user_id="owner-1",
            expires_at=datetime.now(),
            fingerprint="ignored",
        ),
        stage="draft",
        publish_id=None,
        entity_id="entity-1",
        binding_id=9,
        is_test_exchange=True,
    )

    service.append_caller_outbound_rule.assert_called_once()

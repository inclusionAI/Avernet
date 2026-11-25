"""Owner Passport callbacks stay independent of hourly Caller refresh."""
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services import bot_service as bot_service_module
from agentclaw.community.core.bot_management.services.bot_service import BotService, BotServiceError


def _service(bot_type="service"):
    service = BotService.__new__(BotService)
    service._repository = MagicMock()
    service._repository.get_by_id_and_owner.return_value = {
        "bot_id": "service-1", "owner_id": "current-owner", "entity_id": "stable-entity",
        "bot_type": bot_type, "binding_id": 7,
    }
    service._passport_plugin = MagicMock()
    service._scheduled_caller_refresh = MagicMock()
    service._hot_update_service_bot_passport_token = MagicMock(return_value=[{"binding_id": 7}])
    service._hot_update_by_device_binding = MagicMock(return_value={"binding_id": 7, "devices": []})
    return service


def _refresh(service):
    with patch("agentclaw.community.core.bot_management.utils.resolve_agent_code", return_value="agent-1"), patch(
        "agentclaw.community.core.bot_management.services.bot_service.get_current_env", return_value="prod"
    ):
        return service.hot_update_passport_token_to_device("service-1", "current-owner", "owner-secret")


def test_owner_callback_only_installs_owner_token(caplog):
    caplog.set_level("INFO")
    service = _service()
    calls = []
    service._hot_update_service_bot_passport_token.side_effect = lambda **kw: calls.append("owner") or []
    service._scheduled_caller_refresh.refresh.side_effect = lambda **kw: calls.append("caller")
    result = _refresh(service)
    assert calls == ["owner"]
    assert result == {"token_prefix": "owner-secret", "bindings": []}
    service._scheduled_caller_refresh.refresh.assert_not_called()
    assert "scheduled_caller_refresh_requested" not in caplog.text
    assert "owner-secret" not in caplog.text


@pytest.mark.parametrize("failure", [RuntimeError("caller-secret"), TimeoutError("iam-secret"), BotServiceError("asc-secret")])
def test_caller_capability_is_not_called_by_owner_callback(caplog, failure):
    caplog.set_level("INFO")
    service = _service()
    service._scheduled_caller_refresh.refresh.side_effect = failure
    result = _refresh(service)
    assert result == {"token_prefix": "owner-secret", "bindings": [{"binding_id": 7}]}
    service._hot_update_service_bot_passport_token.assert_called_once()
    service._scheduled_caller_refresh.refresh.assert_not_called()
    assert "scheduled_caller_refresh_failed" not in caplog.text
    assert all(secret not in caplog.text for secret in ("caller-secret", "owner-secret", "iam-secret", "asc-secret"))


@pytest.mark.asyncio
async def test_real_http_callback_stays_successful_after_caller_failure():
    from unittest.mock import AsyncMock
    from agentclaw.community.adapters.http.bot_management.router import refresh_bot_passport_token

    service = _service()
    service._scheduled_caller_refresh.refresh.side_effect = TimeoutError("caller-secret")
    request = MagicMock()
    request.json = AsyncMock(return_value={"bot_id": "service-1", "owner_workno": "current-owner", "token": "owner-secret"})
    with patch("agentclaw.community.core.bot_management.utils.resolve_agent_code", return_value="agent-1"):
        response = await refresh_bot_passport_token(request=request, bot_service=service)
    assert response.success is True
    assert response.error_code == 200
    assert response.data["bindings"] == [{"binding_id": 7}]


def test_owner_failure_does_not_invoke_caller_refresh():
    service = _service()
    service._hot_update_service_bot_passport_token.side_effect = BotServiceError("owner failed")
    with pytest.raises(BotServiceError, match="owner failed"):
        _refresh(service)
    service._scheduled_caller_refresh.refresh.assert_not_called()


@pytest.mark.parametrize("bot_type", ["personal", "desktop"])
def test_other_bot_types_do_not_invoke_caller_refresh(bot_type):
    service = _service(bot_type)
    _refresh(service)
    service._scheduled_caller_refresh.refresh.assert_not_called()


def test_unconfigured_optional_capability_keeps_legacy_callback():
    service = _service()
    service._scheduled_caller_refresh = None
    assert _refresh(service)["bindings"] == [{"binding_id": 7}]


def test_cross_tenant_owner_callback_does_not_invoke_caller():
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope, get_current_avernet_tenant

    service = _service()
    service._repository.get_by_id_and_owner.return_value["avernet_tenant"] = "external-tenant"
    with avernet_tenant_scope("teamclaw"):
        assert _refresh(service)["bindings"] == [{"binding_id": 7}]
        assert get_current_avernet_tenant() == "teamclaw"
    service._scheduled_caller_refresh.refresh.assert_not_called()


@pytest.mark.parametrize("configured", [True, False])
def test_owner_callback_does_not_prepare_scheduled_refresh(caplog, configured):
    caplog.set_level("INFO")
    service = _service()
    port = service._scheduled_caller_refresh
    port.refresh.side_effect = AssertionError("Caller must not execute")
    if not configured:
        del service._scheduled_caller_refresh
    with patch.object(bot_service_module, "get_current_env", side_effect=AssertionError("Caller preparation must not execute")), patch(
        "agentclaw.community.core.bot_management.utils.resolve_agent_code", return_value="agent-1"
    ):
        result = service.hot_update_passport_token_to_device("service-1", "current-owner", "owner-secret")
    assert result == {"token_prefix": "owner-secret", "bindings": [{"binding_id": 7}]}
    service._hot_update_service_bot_passport_token.assert_called_once()
    port.refresh.assert_not_called()
    assert "scheduled_caller_refresh_requested" not in caplog.text
    assert "owner-secret" not in caplog.text

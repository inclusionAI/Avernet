"""Owner renewal delegates Caller refresh only after owner installation."""
from unittest.mock import MagicMock, patch

import pytest

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


def test_owner_installation_precedes_caller_refresh(caplog):
    caplog.set_level("INFO")
    service = _service()
    calls = []
    service._hot_update_service_bot_passport_token.side_effect = lambda **kw: calls.append("owner") or []
    service._scheduled_caller_refresh.refresh.side_effect = lambda **kw: calls.append("caller")
    _refresh(service)
    assert calls == ["owner", "caller"]
    assert "scheduled_caller_refresh_requested" in caplog.text
    assert "scheduled_caller_refresh_succeeded" in caplog.text
    assert "owner-secret" not in caplog.text
    service._scheduled_caller_refresh.refresh.assert_called_once_with(
        bot_id="service-1", owner_user_id="current-owner", entity_id="stable-entity", env="prod"
    )


def test_caller_failure_is_retryable_without_exposing_credentials(caplog):
    service = _service()
    service._scheduled_caller_refresh.refresh.side_effect = RuntimeError("caller-secret")
    with pytest.raises(BotServiceError, match="Caller credential refresh failed") as error:
        _refresh(service)
    assert "caller-secret" not in str(error.value)
    assert "owner-secret" not in str(error.value)
    assert "scheduled_caller_refresh_failed" in caplog.text
    assert "caller-secret" not in caplog.text
    assert "owner-secret" not in caplog.text


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


@pytest.mark.parametrize("fail", [False, True])
def test_cross_tenant_callback_scopes_refresh_to_resolved_bot_and_restores_context(fail):
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope, get_current_avernet_tenant

    service = _service()
    service._repository.get_by_id_and_owner.return_value["avernet_tenant"] = "external-tenant"
    observed = []

    def record_tenant(**kwargs):
        observed.append(get_current_avernet_tenant())
        if fail:
            raise RuntimeError("failed")

    service._scheduled_caller_refresh.refresh.side_effect = record_tenant
    with avernet_tenant_scope("teamclaw"):
        if fail:
            with pytest.raises(BotServiceError, match="Caller credential refresh failed"):
                _refresh(service)
        else:
            _refresh(service)
        assert get_current_avernet_tenant() == "teamclaw"
    assert observed == ["external-tenant"]


def test_default_service_bot_keeps_stable_entity_for_caller_refresh():
    service = _service()
    service._repository.get_by_id_and_owner.return_value["bot_id"] = "default"
    with patch("agentclaw.community.core.bot_management.utils.resolve_agent_code", return_value="agent-1"):
        service.hot_update_passport_token_to_device("default", "current-owner", "owner-secret")
    assert service._scheduled_caller_refresh.refresh.call_args.kwargs["entity_id"] == "stable-entity"

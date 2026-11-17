"""Administrative eligibility remains separate from ordinary token installation."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.caller_identity.expiry_correction import (
    CallerExpiryCorrectionError, CallerExpiryCorrectionService,
)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr("agentclaw.community.core.caller_identity.expiry_correction.admin_scopes.super_admin", lambda: {"admin"})
    bots = Mock(get_by_id_and_entity=Mock(return_value={"bot_type": "service", "call_type": "caller", "active_engine": "openclaw"}))
    from agentclaw.community.utils.env_utils import get_current_env
    binding = SimpleNamespace(status="ACTIVE", env=get_current_env(), entity_id="entity", applied_by="caller",
                              entity_type="staff", apply_reason="caller_instance:bot", device_provider="arca")
    bindings = Mock(get_by_id=Mock(return_value=binding))
    instances = Mock(correct_caller_token_expiry=Mock(return_value={"instance_id": 1, "previous_expires_at": None}))
    service = CallerExpiryCorrectionService(bots=bots, bindings=bindings, instances=instances)
    args = dict(actor_id="admin", bot_id="bot", entity_id="entity", caller_user_id="caller", binding_id=7,
                expires_at=datetime(2000, 1, 1, tzinfo=UTC), expected_expires_at=None, reason="verify refresh")
    return service, args, bots, binding, instances


def test_correct_past_expiry_only_changes_the_explicit_ledger(setup, caplog):
    service, args, bots, binding, instances = setup
    caplog.set_level("INFO")
    result = service.correct(**args)
    assert result["instance_id"] == 1
    assert result["expires_at"] == args["expires_at"].isoformat()
    instances.correct_caller_token_expiry.assert_called_once_with(
        user_id="caller", bot_id="bot", owner_id="entity", binding_id=7,
        expires_at=args["expires_at"], expected_expires_at=None)
    assert "caller_expiry_corrected" in caplog.text


def test_non_admin_cannot_use_target_admin_identity(setup):
    service, args, bots, binding, instances = setup
    args.update(actor_id="ordinary", caller_user_id="admin")
    with pytest.raises(CallerExpiryCorrectionError, match="forbidden"):
        service.correct(**args)
    bots.get_by_id_and_entity.assert_not_called()
    instances.correct_caller_token_expiry.assert_not_called()


@pytest.mark.parametrize("change", ["missing", "personal", "owner", "teclaw"])
def test_ineligible_bot_never_writes_ledger(setup, change):
    service, args, bots, binding, instances = setup
    bot = bots.get_by_id_and_entity.return_value
    if change == "missing":
        bots.get_by_id_and_entity.return_value = None
    else:
        bot[{"personal": "bot_type", "owner": "call_type", "teclaw": "active_engine"}[change]] = change
    with pytest.raises(CallerExpiryCorrectionError):
        service.correct(**args)
    instances.correct_caller_token_expiry.assert_not_called()


@pytest.mark.parametrize(("field", "value"), [("status", "DELETED"), ("env", "other"),
    ("entity_id", "other"), ("applied_by", "other"), ("entity_type", "team"),
    ("apply_reason", "service"), ("device_provider", "teclaw")])
def test_wrong_binding_never_writes(setup, field, value):
    service, args, bots, binding, instances = setup
    setattr(binding, field, value)
    with pytest.raises(CallerExpiryCorrectionError, match="binding_changed"):
        service.correct(**args)
    instances.correct_caller_token_expiry.assert_not_called()


def test_handoff_keeps_correction_on_the_stable_entity(setup):
    service, args, bots, binding, instances = setup
    bots.get_by_id_and_entity.return_value.update(entity_id="entity", owner_id="new-human-owner")
    service.correct(**args)
    bots.get_by_id_and_entity.assert_called_once_with("bot", "entity")
    assert instances.correct_caller_token_expiry.call_args.kwargs["owner_id"] == "entity"

from types import SimpleNamespace
from unittest.mock import MagicMock
import logging
import pytest

from agentclaw.community.core.caller_identity.credential import CallerCredentialError
from agentclaw.community.core.caller_identity.session_validator import CallerSessionValidator
from agentclaw.community.core.runtime_binding.models import RuntimeBindingTarget


def _validator(provider="teclaw"):
    bindings = MagicMock()
    bindings.get_by_id.return_value = SimpleNamespace(device_provider=provider)
    return CallerSessionValidator(binding_repository=bindings, runtime_bindings=MagicMock())


def _request(key="original-session"):
    return dict(bot_id="bot-1", owner_id="owner-1", caller_user_id="caller-1",
                binding_id=9, stage="draft", session_key=key)


@pytest.mark.parametrize("key", [None, "", " \t"])
def test_teclaw_missing_key_rejects(key):
    validator = _validator()
    with pytest.raises(CallerCredentialError):
        validator.validate(**_request(key))


def test_nonempty_session_is_forwarded_without_ownership_lookup_and_logged(caplog):
    validator = _validator()
    key = "agent:中文 original/&+?%"
    with caplog.at_level(logging.INFO):
        validator.validate(**_request(key))
    assert "caller_session_key_validated" in caplog.text
    assert key in caplog.text
    validator._runtime_bindings.resolve.assert_not_called()
    validator._bindings.get_by_id.assert_called_once_with(9)


@pytest.mark.parametrize("provider", ["arca", "openclaw"])
def test_non_teclaw_needs_no_session_key(provider):
    _validator(provider).validate(**_request(None))


def test_missing_binding_preserves_existing_authorized_binding_resolution():
    validator = _validator()
    validator._runtime_bindings.resolve.return_value = SimpleNamespace(binding_id=19)
    request = _request()
    request["binding_id"] = None
    validator.validate(**request)
    resolution = validator._runtime_bindings.resolve.call_args.args[0]
    assert resolution.bot_id == "bot-1"
    assert resolution.owner_id == "owner-1"
    assert resolution.actor_user_id == "caller-1"
    assert resolution.stage == "draft"
    assert resolution.target is RuntimeBindingTarget.CALLER_SERVICE
    validator._bindings.get_by_id.assert_called_once_with(19)


def test_missing_binding_record_preserves_existing_non_teclaw_behavior():
    validator = _validator()
    validator._bindings.get_by_id.return_value = None
    validator.validate(**_request(None))

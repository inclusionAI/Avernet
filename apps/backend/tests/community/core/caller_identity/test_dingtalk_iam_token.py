from types import SimpleNamespace
from dataclasses import replace
from datetime import datetime, timedelta
import logging

import pytest

from agentclaw.community.core.caller_identity.contracts import CallerIdentityStage
from agentclaw.community.core.caller_identity.credential import CallerCredentialError
from agentclaw.community.core.runtime_binding.errors import RuntimeBindingResolutionError
from agentclaw.community.core.runtime_binding.models import ResolvedRuntimeBinding, RuntimeBindingSource, RuntimeBindingTarget
from tests.community.core.caller_identity.test_iam_token_service import _service, _request, _caller_token


async def _exchange(service, **kwargs):
    values = dict(iam_token="opaque-device-secret", auth_request=_request(), bot_id="bot-1",
                  stage=CallerIdentityStage.DRAFT, publish_id=None, entity_id="entity-1",
                  is_test_exchange=False, token_type="dingtalk")
    values.update(kwargs)
    return await service.get_iam_token(**values)


@pytest.mark.asyncio
async def test_device_exchange_only_updates_exact_caller_without_sso(caplog):
    caplog.set_level(logging.INFO)
    service, identity, auth, provider, _, bindings, locks = _service()
    service._token_provider = SimpleNamespace(exchange_dingtalk=provider.exchange_dingtalk)
    provider.exchange_dingtalk.return_value = replace(_caller_token(), expires_at=datetime.now() + timedelta(hours=1))
    bindings.resolve.side_effect = None
    bindings.resolve.return_value = ResolvedRuntimeBinding(41, RuntimeBindingSource.CALLER_INSTANCE)
    result = await _exchange(service)
    assert result.error is None
    provider.exchange.assert_not_called()
    auth.resolve_user_from_request.assert_not_called()
    identity.exchange_caller_token.assert_not_called()
    locks.get_by_key.assert_not_called()
    request = bindings.resolve.call_args.args[0]
    assert request.target is RuntimeBindingTarget.CALLER_INSTANCE
    assert request.actor_user_id == "caller-1"
    assert request.owner_id == "owner-1"
    assert provider.exchange_dingtalk.call_args.kwargs == {
        "device_token": "opaque-device-secret", "bot_id": "bot-1",
        "owner_user_id": "owner-1", "task_metadata": {"taskCode": "teamclaw_chat", "taskName": "TeamClaw Chat", "taskType": "CALLER", "triggerSource": "TEAMCLAW_CHAT"},
    }
    assert identity.exchange_caller_identity.call_args.kwargs["binding_id"] == 41
    assert identity.exchange_caller_identity.call_args.kwargs["caller_token"] is provider.exchange_dingtalk.return_value
    assert "caller_dingtalk_request" in caplog.text
    assert "caller_dingtalk_succeeded" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "'binding_id': 41" in caplog.text
    assert "'user_id': 'caller-1'" in caplog.text
    assert "caller-token" not in caplog.text
    assert "opaque-device-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["subject-none", "subject-empty", "subject-blank", "subject-padded", "token-empty", "token-padded", "target", "upstream", "bot", "test", "expired", "expiry-none", "changed", "context", "unsupported", "empty", "provider", "noncaller", "missing-owner", "wrong-target", "install", "session"])
async def test_device_failure_never_installs_or_falls_back(failure, caplog):
    service, identity, auth, provider, _, bindings, _ = _service()
    service._token_provider = SimpleNamespace(exchange_dingtalk=provider.exchange_dingtalk)
    valid = replace(_caller_token(), expires_at=datetime.now() + timedelta(hours=1))
    provider.exchange_dingtalk.return_value = valid
    bindings.resolve.side_effect = None
    bindings.resolve.return_value = ResolvedRuntimeBinding(41, RuntimeBindingSource.CALLER_INSTANCE)
    kwargs = {}
    subjects = {"subject-none": None, "subject-empty": "", "subject-blank": " ", "subject-padded": " caller-1 "}
    if failure in subjects:
        provider.exchange_dingtalk.return_value = replace(valid, subject_user_id=subjects[failure])
    elif failure.startswith("token-"):
        provider.exchange_dingtalk.return_value = replace(valid, access_token="" if failure == "token-empty" else " caller-token ")
    elif failure == "target":
        bindings.resolve.side_effect = RuntimeBindingResolutionError("opaque-device-secret")
    elif failure == "upstream":
        provider.exchange_dingtalk.side_effect = CallerCredentialError("CALLER_CREDENTIAL_UPSTREAM_FAILED")
    elif failure == "bot":
        kwargs["bot_id"] = None
    elif failure == "test":
        kwargs["is_test_exchange"] = True
    elif failure == "expired":
        provider.exchange_dingtalk.return_value = _caller_token()
    elif failure == "expiry-none":
        provider.exchange_dingtalk.return_value = replace(valid, expires_at=None)
    elif failure == "changed":
        bindings.resolve.side_effect = [ResolvedRuntimeBinding(41, RuntimeBindingSource.CALLER_INSTANCE), ResolvedRuntimeBinding(42, RuntimeBindingSource.CALLER_INSTANCE)]
    elif failure == "context":
        identity.get_iam_token_context.side_effect = RuntimeError("opaque-device-secret")
    elif failure == "unsupported":
        kwargs["token_type"] = "unsupported"
    elif failure == "empty":
        kwargs["iam_token"] = ""
    elif failure == "provider":
        service._token_provider = SimpleNamespace()
    elif failure == "noncaller":
        identity.get_iam_token_context.return_value = replace(identity.get_iam_token_context.return_value, should_exchange_caller_token=False)
    elif failure == "wrong-target":
        bindings.resolve.return_value = ResolvedRuntimeBinding(9, RuntimeBindingSource.SERVICE_DRAFT)
    elif failure == "install":
        identity.exchange_caller_identity.side_effect = RuntimeError("opaque-device-secret")
    elif failure == "session":
        service._session_validator.validate.side_effect = CallerCredentialError("CALLER_CREDENTIAL_REQUEST_INVALID")
    else:
        identity.get_iam_token_context.return_value = replace(identity.get_iam_token_context.return_value, owner_id=None)
    result = await _exchange(service, **kwargs)
    assert result.error is not None
    if failure != "unsupported":
        assert "caller_dingtalk_failed" in caplog.text
        assert "error_code=" in caplog.text
        assert "duration_ms=" in caplog.text
    if failure in {"missing-owner", "bot", "empty", "test", "context", "noncaller"}:
        provider.exchange_dingtalk.assert_not_called()
    if failure != "install":
        identity.exchange_caller_identity.assert_not_called()
    identity.exchange_caller_token.assert_not_called()
    provider.exchange.assert_not_called()
    auth.resolve_user_from_request.assert_not_called()
    assert "caller-token" not in caplog.text
    assert "opaque-device-secret" not in caplog.text


@pytest.mark.asyncio
async def test_verified_subject_is_used_verbatim_before_target_resolution():
    service, identity, _, provider, _, bindings, _ = _service()
    service._token_provider = SimpleNamespace(exchange_dingtalk=provider.exchange_dingtalk)
    provider.exchange_dingtalk.return_value = replace(_caller_token(), subject_user_id="007654", expires_at=datetime.now() + timedelta(hours=1))
    bindings.resolve.side_effect = None
    bindings.resolve.return_value = ResolvedRuntimeBinding(41, RuntimeBindingSource.CALLER_INSTANCE)
    def verify(**_kwargs):
        bindings.resolve.assert_not_called()
        return provider.exchange_dingtalk.return_value
    provider.exchange_dingtalk.side_effect = verify
    result = await _exchange(service)
    assert result.error is None
    assert bindings.resolve.call_args.args[0].actor_user_id == "007654"
    assert identity.exchange_caller_identity.call_args.kwargs["caller_user_id"] == "007654"


def test_community_device_provider_is_explicitly_unavailable():
    from agentclaw.community.api.caller_credential import DingTalkCallerTokenProvider, UnavailableCallerTokenProvider
    provider = UnavailableCallerTokenProvider()
    assert isinstance(provider, DingTalkCallerTokenProvider)
    with pytest.raises(CallerCredentialError, match="CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE"):
        provider.exchange_dingtalk(device_token="opaque", bot_id="bot", owner_user_id="owner", task_metadata={})


@pytest.mark.asyncio
async def test_forbidden_bot_context_never_exchanges_or_installs(caplog):
    from agentclaw.community.core.caller_identity.contracts import CallerIdentityPermissionError

    service, identity, auth, provider, _, bindings, _ = _service()
    service._token_provider = SimpleNamespace(exchange_dingtalk=provider.exchange_dingtalk)
    identity.get_iam_token_context.side_effect = CallerIdentityPermissionError()

    result = await _exchange(service)

    assert result.error == "CALLER_IDENTITY_FORBIDDEN"
    provider.exchange_dingtalk.assert_not_called()
    bindings.resolve.assert_not_called()
    identity.exchange_caller_identity.assert_not_called()
    auth.resolve_user_from_request.assert_not_called()
    assert "caller_dingtalk_failed" in caplog.text
    assert "error_code=CALLER_IDENTITY_FORBIDDEN" in caplog.text
    assert "caller-token" not in caplog.text
    assert "opaque-device-secret" not in caplog.text

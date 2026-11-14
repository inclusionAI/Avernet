from unittest.mock import MagicMock
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
                  is_test_exchange=False, token_type="dingtalk", expected_user_id="caller-1",
                  expected_owner_id="owner-1")
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
    assert bindings.resolve.call_args.args[0].target is RuntimeBindingTarget.CALLER_INSTANCE
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
@pytest.mark.parametrize("failure", ["subject", "target", "upstream", "owner", "user", "bot", "test", "expired", "changed", "context", "unsupported", "empty", "provider", "noncaller", "missing-owner", "blank-user", "wrong-target", "install"])
async def test_device_failure_never_installs_or_falls_back(failure, caplog):
    service, identity, auth, provider, _, bindings, _ = _service()
    service._token_provider = SimpleNamespace(exchange_dingtalk=provider.exchange_dingtalk)
    provider.exchange_dingtalk.return_value = replace(_caller_token(), expires_at=datetime.now() + timedelta(hours=1))
    bindings.resolve.side_effect = None
    bindings.resolve.return_value = ResolvedRuntimeBinding(41, RuntimeBindingSource.CALLER_INSTANCE)
    kwargs = {}
    if failure == "subject":
        provider.exchange_dingtalk.return_value = MagicMock(subject_user_id="other-user")
    elif failure == "target":
        bindings.resolve.side_effect = RuntimeBindingResolutionError("opaque-device-secret")
    elif failure == "upstream":
        provider.exchange_dingtalk.side_effect = CallerCredentialError("CALLER_CREDENTIAL_UPSTREAM_FAILED")
    elif failure == "owner":
        kwargs["expected_owner_id"] = "other-owner"
    elif failure == "user":
        kwargs["expected_user_id"] = None
    elif failure == "bot":
        kwargs["bot_id"] = None
    elif failure == "test":
        kwargs["is_test_exchange"] = True
    elif failure == "expired":
        provider.exchange_dingtalk.return_value = _caller_token()
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
    elif failure == "missing-owner":
        kwargs["expected_owner_id"] = None
    else:
        kwargs["expected_user_id"] = " "
    result = await _exchange(service, **kwargs)
    assert result.error is not None
    if failure != "unsupported":
        assert "caller_dingtalk_failed" in caplog.text
        assert "error_code=" in caplog.text
        assert "duration_ms=" in caplog.text
        assert "'operation': 'caller_dingtalk'" in caplog.text
    if failure in {"missing-owner", "blank-user", "user", "bot", "empty", "test"}:
        provider.exchange_dingtalk.assert_not_called()
    if failure != "install":
        identity.exchange_caller_identity.assert_not_called()
    identity.exchange_caller_token.assert_not_called()
    provider.exchange.assert_not_called()
    auth.resolve_user_from_request.assert_not_called()
    assert "caller-token" not in caplog.text
    assert "opaque-device-secret" not in caplog.text


def test_community_device_provider_is_explicitly_unavailable():
    from agentclaw.community.api.caller_credential import AuthContext, DingTalkCallerTokenProvider, UnavailableCallerTokenProvider
    provider = UnavailableCallerTokenProvider()
    assert isinstance(provider, DingTalkCallerTokenProvider)
    with pytest.raises(CallerCredentialError, match="CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE"):
        provider.exchange_dingtalk(auth_context=AuthContext(user_id="caller"), device_token="opaque", bot_id="bot", owner_user_id="owner", task_metadata={})

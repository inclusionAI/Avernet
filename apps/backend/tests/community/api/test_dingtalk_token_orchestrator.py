"""Route regression through the real token orchestrator and Caller services."""
from dataclasses import replace
from datetime import datetime, timedelta
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from agentclaw.community.core.caller_identity.service import CallerIdentityService
from agentclaw.community.core.token_exchange import TokenExchangeOrchestrator, NoopTokenPluginPipeline
from agentclaw.community.core.runtime_binding.models import ResolvedRuntimeBinding, RuntimeBindingSource
from agentclaw.community.di.modules.infrastructure.community.caller_identity import CommunityCallerIamTokenService
from agentclaw.community.di.modules.infrastructure.singlebox.caller_identity import SingleboxCallerIamTokenService
from tests.community.api.test_dingtalk_iam_token import _app
from tests.community.core.caller_identity.test_iam_token_service import _service, _caller_token


def _orchestrated_service():
    caller, identity, auth, provider, updater, bindings, _ = _service()
    # Run the actual installation method, substituting only context and ports.
    real_identity = CallerIdentityService.__new__(CallerIdentityService)
    real_identity._session_validator = MagicMock()
    real_identity.get_iam_token_context = identity.get_iam_token_context
    caller._caller_identity = real_identity
    caller._token_provider = SimpleNamespace(exchange_dingtalk=provider.exchange_dingtalk)
    resolved_identity = replace(_caller_token(), expires_at=datetime.now() + timedelta(hours=1))
    provider.exchange_dingtalk.return_value = resolved_identity
    bindings.resolve.side_effect = None
    bindings.resolve.return_value = ResolvedRuntimeBinding(41, RuntimeBindingSource.CALLER_INSTANCE)
    orchestrator = TokenExchangeOrchestrator(caller_service=caller, token_pipeline=NoopTokenPluginPipeline(), bot_repository=MagicMock())
    return orchestrator, provider, updater, bindings, auth, resolved_identity


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["valid", "missing", "rejected"])
async def test_http_dingtalk_reaches_exchange_and_exact_runtime_update(state, caplog):
    from agentclaw.community.core.caller_identity.credential import CallerCredentialError, CALLER_CREDENTIAL_REQUEST_INVALID

    caplog.set_level(logging.INFO)
    orchestrator, provider, updater, bindings, auth, resolved_identity = _orchestrated_service()
    if state == "rejected":
        provider.exchange_dingtalk.side_effect = CallerCredentialError(CALLER_CREDENTIAL_REQUEST_INVALID)
    headers = {} if state == "missing" else {"Cookie": "IAM_TOKEN=device-secret"}
    async with AsyncClient(transport=ASGITransport(app=_app(orchestrator)), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", params={"token_type": "dingtalk", "bot_id": "bot-1", "entity_id": "entity-1", "session_key": "session-1"}, headers=headers)
    assert response.status_code == (200 if state == "valid" else 400)
    assert response.json()["success"] is (state == "valid")
    assert "caller_token_source_selected" in caplog.text
    assert "token_type=dingtalk" in caplog.text
    assert "device-secret" not in caplog.text
    assert resolved_identity.access_token not in caplog.text
    auth.resolve_user_from_request.assert_not_called()
    if state == "valid":
        assert provider.exchange_dingtalk.call_args.kwargs["device_token"] == "device-secret"
        assert provider.exchange_dingtalk.call_args.kwargs["owner_user_id"] == "owner-1"
        assert bindings.resolve.call_count == 2
        updater.update_caller_identity.assert_called_once_with(bot_id="bot-1", owner_user_id="owner-1", caller_user_id="caller-1", caller_token=resolved_identity, stage="draft", publish_id=None, entity_id="entity-1", binding_id=41, session_key="session-1")
    else:
        updater.update_caller_identity.assert_not_called()
        if state == "missing":
            provider.exchange_dingtalk.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_class", [CommunityCallerIamTokenService, SingleboxCallerIamTokenService])
async def test_unsupported_profiles_fail_closed_for_dingtalk(service_class):
    orchestrator = TokenExchangeOrchestrator(caller_service=service_class(), token_pipeline=NoopTokenPluginPipeline(), bot_repository=MagicMock())
    async with AsyncClient(transport=ASGITransport(app=_app(orchestrator)), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", params={"token_type": "dingtalk", "bot_id": "bot-1"}, headers={"Cookie": "IAM_TOKEN=device-secret"})
        default = await client.get("/api/v1/token/iam")
    assert response.status_code == 503
    assert response.json() == {"success": False, "error": "CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE"}
    assert default.status_code == 200
    assert default.json()["success"] is True


@pytest.mark.asyncio
async def test_default_iam_compatible_with_legacy_signature():
    from agentclaw.community.core.caller_identity.contracts import CallerIamTokenOutcome

    class LegacyCaller:
        async def get_iam_token(self, *, iam_token, auth_request, bot_id, stage, publish_id, entity_id, is_test_exchange, session_key=None):
            return CallerIamTokenOutcome(iam_token=iam_token)

    orchestrator = TokenExchangeOrchestrator(caller_service=LegacyCaller(), token_pipeline=NoopTokenPluginPipeline(), bot_repository=MagicMock())
    async with AsyncClient(transport=ASGITransport(app=_app(orchestrator)), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", headers={"Cookie": "IAM_TOKEN=iam-secret"})
    assert response.status_code == 200
    assert response.json() == {"success": True, "iam_token": "iam-secret"}

"""Requester identity is independent from the Bot resource and allowlist subject."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.caller_identity.contracts import (
    CallerIamTokenOutcome,
    CallerIdentityStage,
)
from agentclaw.community.core.token_exchange import TokenExchangeOrchestrator
from agentclaw.community.plugin_api.auth import AuthRequestContext


class _Auth:
    def __init__(self, actor="caller-B", error=None):
        self.actor = actor
        self.error = error
        self.requests = []

    async def resolve_user_from_request(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return SimpleNamespace(staffId=self.actor) if self.actor is not None else None


class _Caller:
    def __init__(self, error=None):
        self.error = error
        self.requests = []
        self.outcome = CallerIamTokenOutcome(iam_token="synthetic-original-result")

    async def get_iam_token(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return self.outcome


class _Bots:
    def get_by_id_and_entity(self, bot_id, entity_id):
        return {"owner_id": "owner-O", "bot_type": "service", "env": "pre"}


class _Pipeline:
    def __init__(self):
        self.contexts = []

    async def run(self, context):
        self.contexts.append(context)
        return []


def _service(auth, *, caller_error=None):
    caller, pipeline = _Caller(caller_error), _Pipeline()
    service = TokenExchangeOrchestrator(
        caller_service=caller, token_pipeline=pipeline, bot_repository=_Bots(), auth_plugin=auth,
    )
    return service, caller, pipeline


def _request(**overrides):
    return {
        "iam_token": "synthetic-input-secret",
        "auth_request": AuthRequestContext(cookies={"opaque": "synthetic-cookie-secret"}),
        "bot_id": "bot-1", "stage": CallerIdentityStage.ONLINE,
        "publish_id": None, "entity_id": "resource-E", "is_test_exchange": False,
        **overrides,
    }


@pytest.mark.asyncio
async def test_authenticated_actor_is_separate_from_owner_resource_and_allowlist(caplog):
    caplog.set_level("INFO")
    auth = _Auth("000-B")
    service, caller, pipeline = _service(auth)
    request = _request()
    assert await service.get_iam_token(**request) is caller.outcome
    context = pipeline.contexts[0]
    assert getattr(context, "actor_user_id", None) == "000-B"
    assert context.owner_user_id == "owner-O"
    assert context.entity_id == "resource-E"
    assert context.user_list_entity_id == "resource-E"
    assert auth.requests == [request["auth_request"]]
    assert "token_plugin_context_built" in caplog.text
    for field in ("entity_id=resource-E", "owner_user_id=owner-O", "actor_user_id=000-B"):
        assert field in caplog.text
    assert "synthetic-input-secret" not in caplog.text
    assert "synthetic-cookie-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [None, "", "  ", 123])
async def test_missing_authenticated_actor_skips_plugins_without_entity_fallback(actor, caplog):
    service, caller, pipeline = _service(_Auth(actor))
    assert await service.get_iam_token(**_request()) is caller.outcome
    assert pipeline.contexts == []
    assert "reason=missing_authenticated_actor" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_fails", [False, True])
async def test_auth_failure_preserves_original_caller_success_or_error(caller_fails, caplog):
    original_error = RuntimeError("caller-install-failed") if caller_fails else None
    service, caller, pipeline = _service(
        _Auth(error=ValueError("synthetic-auth-secret")), caller_error=original_error,
    )
    if caller_fails:
        with pytest.raises(RuntimeError) as raised:
            await service.get_iam_token(**_request())
        assert raised.value is original_error
    else:
        assert await service.get_iam_token(**_request()) is caller.outcome
    assert pipeline.contexts == []
    assert "token_plugin_context_build_failed" in caplog.text
    assert "error_type=ValueError" in caplog.text
    assert "synthetic-auth-secret" not in caplog.text
    assert "synthetic-input-secret" not in caplog.text
    assert "synthetic-cookie-secret" not in caplog.text


@pytest.mark.asyncio
async def test_friend_input_cannot_replace_the_authenticated_subject(caplog):
    service, caller, pipeline = _service(_Auth("caller-B"))
    assert await service.get_iam_token(**_request(friend_user_id="spoofed-friend")) is caller.outcome
    assert pipeline.contexts == []
    assert caller.requests[0]["friend_user_id"] == "spoofed-friend"
    assert "reason=friend_subject_mismatch" in caplog.text


@pytest.mark.asyncio
async def test_verified_friend_still_uses_authenticated_subject():
    service, caller, pipeline = _service(_Auth("caller-B"))
    assert await service.get_iam_token(**_request(friend_user_id="caller-B")) is caller.outcome
    assert pipeline.contexts[0].actor_user_id == "caller-B"
    assert pipeline.contexts[0].user_list_entity_id == "resource-E"


@pytest.mark.asyncio
@pytest.mark.parametrize("token_type", ["dingtalk", "unsupported"])
@pytest.mark.parametrize("caller_fails", [False, True])
async def test_non_iam_type_passes_to_caller_without_inventing_jit_identity(token_type, caller_fails, caplog):
    caplog.set_level("INFO")
    auth = _Auth(error=AssertionError("non-IAM request must not use browser identity"))
    original_error = ValueError("caller-error") if caller_fails else None
    service, caller, pipeline = _service(auth, caller_error=original_error)
    if caller_fails:
        with pytest.raises(ValueError) as raised:
            await service.get_iam_token(**_request(token_type=token_type))
        assert raised.value is original_error
    else:
        assert await service.get_iam_token(**_request(token_type=token_type)) is caller.outcome
    assert caller.requests[0]["token_type"] == token_type
    assert "reason=non_iam_credential" in caplog.text
    assert "synthetic-input-secret" not in caplog.text
    assert auth.requests == []
    assert pipeline.contexts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("bot_id", "entity_id"), [(None, "resource-E"), ("bot-1", None)])
async def test_unscoped_request_does_not_add_authentication(bot_id, entity_id):
    auth = _Auth(error=AssertionError("unscoped authentication is unnecessary"))
    service, caller, pipeline = _service(auth)
    assert await service.get_iam_token(**_request(bot_id=bot_id, entity_id=entity_id)) is caller.outcome
    assert auth.requests == []
    assert pipeline.contexts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_type", ["personal", "service"])
async def test_owner_and_personal_requests_keep_resource_and_rollout_scope(bot_type):
    service, caller, pipeline = _service(_Auth("owner-O"))
    service._bot_repository.get_by_id_and_entity = lambda *args: {
        "owner_id": "owner-O", "bot_type": bot_type, "env": "prod",
    }
    assert await service.get_iam_token(**_request(stage=CallerIdentityStage.DRAFT)) is caller.outcome
    context = pipeline.contexts[0]
    assert context.actor_user_id == "owner-O"
    assert context.owner_user_id == "owner-O"
    assert context.entity_id == context.user_list_entity_id == "resource-E"
    assert context.bot_type == bot_type
    assert context.env == "prod"
    assert "token_type" not in caller.requests[0]


@pytest.mark.asyncio
async def test_real_identity_module_injects_auth_plugin_into_orchestrator():
    from injector import Binder, Injector, InstanceProvider

    from agentclaw.community.api.caller_iam_token_service import (
        CallerIamTokenServiceProtocol,
    )
    from agentclaw.community.core.caller_identity.iam_token_service import (
        CallerIamTokenService,
    )
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    from agentclaw.community.core.token_exchange import TokenPluginPipeline
    from agentclaw.community.di.modules.caller_identity_module import (
        CallerIdentityModule,
    )
    from agentclaw.community.plugin_api.auth import AuthPlugin

    auth, caller, pipeline = _Auth("caller-B"), _Caller(), _Pipeline()

    def boundaries(binder: Binder):
        for contract, value in (
            (CallerIamTokenService, caller), (TokenPluginPipeline, pipeline),
            (BotRepository, _Bots()), (AuthPlugin, auth),
        ):
            binder.bind(contract, to=InstanceProvider(value))

    container = Injector([CallerIdentityModule(), boundaries], auto_bind=False)
    service = container.get(CallerIamTokenServiceProtocol)
    assert isinstance(service, TokenExchangeOrchestrator)
    assert service._auth_plugin is auth
    assert await service.get_iam_token(**_request()) is caller.outcome
    assert pipeline.contexts[0].actor_user_id == "caller-B"


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [
    {}, {"caller_runtime_target": "service"}, {"caller_runtime_target": "caller_instance"},
    {"device_uuid": "DEVICE-02"}, {"device_uuid": " 副本/a:b "}, {"device_uuid": "DEVICE-02", "caller_runtime_target": "service"},
])
async def test_jit_reads_selectors_after_unchanged_caller_refresh(selection):
    auth = _Auth()
    service, caller, pipeline = _service(auth)
    request = _request(auth_request=AuthRequestContext(query_params=selection))
    assert await service.get_iam_token(**request) is caller.outcome
    assert caller.requests == [request]
    context, = pipeline.contexts
    assert context.caller_runtime_target == selection.get("caller_runtime_target")
    assert context.device_uuid == selection.get("device_uuid")


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [
    {"caller_runtime_target": ""}, {"caller_runtime_target": "AUTO"},
    {"device_uuid": ""},
])
async def test_invalid_jit_selectors_preserve_caller_without_fallback(selection):
    service, caller, pipeline = _service(_Auth())
    request = _request(auth_request=AuthRequestContext(query_params=selection))
    assert await service.get_iam_token(**request) is caller.outcome
    assert caller.requests == [request]
    assert pipeline.contexts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [{"caller_runtime_target": "wrong"}, {"device_uuid": ""}])
async def test_http_invalid_selection_preserves_caller_response(selection):
    from httpx import ASGITransport, AsyncClient
    from tests.community.api.test_dingtalk_iam_token import _app

    service, caller, pipeline = _service(_Auth())
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/token/iam", params={"bot_id": "bot-1", "entity_id": "resource-E", **selection},
        )
    assert response.status_code == 200
    assert response.json() == {"success": True, "iam_token": caller.outcome.iam_token}
    assert len(caller.requests) == 1
    assert {"caller_runtime_target", "device_uuid"}.isdisjoint(caller.requests[0])
    assert pipeline.contexts == []


@pytest.mark.asyncio
async def test_jit_selector_failure_preserves_original_caller_exception():
    caller_error = RuntimeError("caller refresh failed")
    service, caller, pipeline = _service(_Auth(), caller_error=caller_error)
    request = _request(auth_request=AuthRequestContext(query_params={"caller_runtime_target": "invalid"}))
    with pytest.raises(RuntimeError) as exc:
        await service.get_iam_token(**request)
    assert exc.value is caller_error
    assert caller.requests == [request]
    assert pipeline.contexts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["service", "caller_instance"])
async def test_verified_friend_keeps_requested_runtime_for_target_authorization(target):
    service, caller, pipeline = _service(_Auth("friend-1"))
    request = _request(friend_user_id="friend-1", auth_request=AuthRequestContext(
        query_params={"caller_runtime_target": target},
    ))
    assert await service.get_iam_token(**request) is caller.outcome
    assert caller.requests == [request]
    assert [ctx.caller_runtime_target for ctx in pipeline.contexts] == [target]

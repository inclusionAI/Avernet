from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.caller_identity.contracts import (
    CallerIamTokenContext,
    CallerIdentityAmbiguousError,
    CallerIdentityPermissionError,
    CallerIdentityStage,
    McpCallType,
)
from agentclaw.community.core.caller_identity.credential import (
    CALLER_CREDENTIAL_REQUEST_INVALID,
    CALLER_OUTBOUND_UPDATE_FAILED,
    CallerCredentialError,
    CallerToken,
)
from agentclaw.community.core.caller_identity.iam_token_service import (
    CallerIamTokenService,
)
from agentclaw.community.core.runtime_binding.errors import RuntimeBindingResolutionError
from agentclaw.community.core.runtime_binding.models import (
    ResolvedRuntimeBinding,
    RuntimeBindingSource,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext
from agentclaw.community.api.caller_credential import UnavailableCallerTokenProvider
from agentclaw.community.di.modules.caller_identity_module import CallerIdentityModule


def _context(*, exchange: bool = True, owner_id: str | None = "owner-1") -> CallerIamTokenContext:
    return CallerIamTokenContext(
        bot_id="bot-1",
        owner_id=owner_id,
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        bot_call_type=McpCallType.CALLER,
        should_exchange_caller_token=exchange,
        binding_id=9,
    )


def _caller_token() -> CallerToken:
    return CallerToken(
        access_token="caller-token",
        subject_user_id="caller-1",
        expires_at=datetime.now(),
        fingerprint="fingerprint",
    )


def _service(
    *,
    context: CallerIamTokenContext | None = None,
    service_target: ResolvedRuntimeBinding | Exception | None = None,
    caller_target: ResolvedRuntimeBinding | Exception | None = None,
    lock_holder: str | None = "caller-1",
):
    identity = MagicMock()
    identity.get_iam_token_context.return_value = context or _context()
    identity.exchange_caller_identity.return_value = _caller_token()
    identity.exchange_caller_token.return_value = _caller_token()
    auth = MagicMock()
    auth.resolve_user_from_request = AsyncMock(
        return_value=SimpleNamespace(staffId="caller-1")
    )
    provider = MagicMock()
    updater = MagicMock()
    runtime_bindings = MagicMock()
    if service_target is None:
        service_target = ResolvedRuntimeBinding(9, RuntimeBindingSource.SERVICE_DRAFT)
    runtime_bindings.resolve.side_effect = [
        service_target,
        caller_target or RuntimeBindingResolutionError("caller target missing"),
    ]
    lock_repository = MagicMock()
    lock_repository.get_by_key.return_value = (
        SimpleNamespace(holder_user_id=lock_holder) if lock_holder else None
    )
    return (
        CallerIamTokenService(
            caller_identity=identity,
            auth_plugin=auth,
            token_provider=provider,
            runtime_updater=updater,
            runtime_bindings=runtime_bindings,
            lock_repository=lock_repository,
        ),
        identity,
        auth,
        provider,
        updater,
        runtime_bindings,
        lock_repository,
    )


def _request() -> AuthRequestContext:
    return AuthRequestContext({}, {}, {}, "http://test/")


@pytest.mark.asyncio
async def test_service_installs_caller_token_without_returning_it() -> None:
    service, identity, *_ = _service(
        service_target=ResolvedRuntimeBinding(9, RuntimeBindingSource.SERVICE_DRAFT),
        caller_target=RuntimeBindingResolutionError("caller target missing"),
        lock_holder="caller-1",
    )

    result = await service.get_iam_token(
        iam_token="iam-token",
        auth_request=AuthRequestContext({}, {}, {}, "http://test/"),
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id="entity-1",
        is_test_exchange=False,
    )

    assert result.error is None
    assert result.iam_token == "iam-token"
    assert identity.exchange_caller_identity.call_args.kwargs["binding_id"] == 9


@pytest.mark.asyncio
async def test_service_refresh_updates_service_and_caller_instance_targets():
    service_target = ResolvedRuntimeBinding(31, RuntimeBindingSource.SERVICE_ONLINE)
    caller_target = ResolvedRuntimeBinding(41, RuntimeBindingSource.CALLER_INSTANCE)
    service, identity, _, _, _, runtime_bindings, _ = _service(
        service_target=service_target,
        caller_target=caller_target,
        lock_holder="caller-1",
    )

    result = await service.get_iam_token(
        iam_token="iam-token",
        auth_request=_request(),
        bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE,
        publish_id=None,
        entity_id="owner-1",
        is_test_exchange=False,
    )

    assert result.error is None
    assert runtime_bindings.resolve.call_count == 2
    assert identity.exchange_caller_identity.call_count == 2
    identity.exchange_caller_token.assert_called_once()
    first_call, second_call = identity.exchange_caller_identity.call_args_list
    assert first_call.kwargs["binding_id"] == 31
    assert first_call.kwargs["caller_token"] is identity.exchange_caller_token.return_value
    assert second_call.kwargs["binding_id"] == 41
    assert second_call.kwargs["caller_token"] is identity.exchange_caller_token.return_value


@pytest.mark.asyncio
async def test_owner_without_lock_updates_caller_service_target():
    service, identity, _, _, _, runtime_bindings, lock_repository = _service(
        context=_context(owner_id="caller-1"),
        service_target=ResolvedRuntimeBinding(31, RuntimeBindingSource.SERVICE_ONLINE),
        caller_target=RuntimeBindingResolutionError("caller target missing"),
        lock_holder=None,
    )

    result = await service.get_iam_token(
        iam_token="iam-token",
        auth_request=_request(),
        bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE,
        publish_id=None,
        entity_id="owner-1",
        is_test_exchange=False,
    )

    assert result.error is None
    lock_repository.get_by_key.assert_called_once_with("bot-1:caller-1")
    assert runtime_bindings.resolve.call_count == 2
    identity.exchange_caller_identity.assert_called_once()
    assert identity.exchange_caller_identity.call_args.kwargs["binding_id"] == 31


@pytest.mark.asyncio
async def test_non_holder_skips_caller_service_but_updates_caller_instance():
    caller_target = ResolvedRuntimeBinding(41, RuntimeBindingSource.CALLER_INSTANCE)
    service, identity, _, _, _, runtime_bindings, _ = _service(
        service_target=ResolvedRuntimeBinding(31, RuntimeBindingSource.SERVICE_ONLINE),
        caller_target=caller_target,
        lock_holder="other-user",
    )
    runtime_bindings.resolve.side_effect = [caller_target]

    result = await service.get_iam_token(
        iam_token="iam-token",
        auth_request=_request(),
        bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE,
        publish_id=None,
        entity_id="owner-1",
        is_test_exchange=False,
    )

    assert result.error is None
    runtime_bindings.resolve.assert_called_once()
    identity.exchange_caller_identity.assert_called_once()
    assert identity.exchange_caller_identity.call_args.kwargs["binding_id"] == 41


@pytest.mark.asyncio
async def test_service_refresh_keeps_success_when_only_caller_instance_updates():
    caller_target = ResolvedRuntimeBinding(41, RuntimeBindingSource.CALLER_INSTANCE)
    service, identity, _, _, _, runtime_bindings, _ = _service(
        service_target=RuntimeBindingResolutionError("service target missing"),
        caller_target=caller_target,
    )

    result = await service.get_iam_token(
        iam_token="iam-token",
        auth_request=_request(),
        bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE,
        publish_id=None,
        entity_id="owner-1",
        is_test_exchange=False,
    )

    assert result.error is None
    assert runtime_bindings.resolve.call_count == 2
    identity.exchange_caller_identity.assert_called_once()
    assert identity.exchange_caller_identity.call_args.kwargs["binding_id"] == 41


@pytest.mark.asyncio
async def test_service_refresh_fails_when_no_target_is_updated():
    service, identity, _, _, _, runtime_bindings, _ = _service(
        service_target=RuntimeBindingResolutionError("service target missing"),
        caller_target=RuntimeBindingResolutionError("caller target missing"),
    )

    result = await service.get_iam_token(
        iam_token="iam-token",
        auth_request=_request(),
        bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE,
        publish_id=None,
        entity_id="owner-1",
        is_test_exchange=False,
    )

    assert result.error == "CALLER_OUTBOUND_UPDATE_FAILED"
    assert runtime_bindings.resolve.call_count == 2
    identity.exchange_caller_identity.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("iam_token", "bot_id", "test_exchange", "expected_error"),
    [
        ("", "bot-1", False, "IAM_TOKEN cookie not found"),
        ("iam-token", None, True, CALLER_CREDENTIAL_REQUEST_INVALID),
        ("iam-token", None, False, None),
    ],
)
async def test_service_returns_early_iam_outcomes(
    iam_token: str,
    bot_id: str | None,
    test_exchange: bool,
    expected_error: str | None,
) -> None:
    service, identity, *_ = _service()

    result = await service.get_iam_token(
        iam_token=iam_token,
        auth_request=_request(),
        bot_id=bot_id,
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id=None,
        is_test_exchange=test_exchange,
    )

    assert result.error == expected_error
    if expected_error is None:
        assert result.iam_token == "iam-token"
    identity.get_iam_token_context.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "expected_error"),
    [
        (CallerIdentityAmbiguousError(), "CALLER_IDENTITY_AMBIGUOUS"),
        (CallerIdentityPermissionError(), "CALLER_IDENTITY_FORBIDDEN"),
        (RuntimeError("repository unavailable"), None),
    ],
)
async def test_service_fails_closed_or_falls_back_for_context_errors(
    exception: Exception,
    expected_error: str | None,
) -> None:
    service, identity, *_ = _service()
    identity.get_iam_token_context.side_effect = exception

    result = await service.get_iam_token(
        iam_token="iam-token",
        auth_request=_request(),
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id=None,
        is_test_exchange=False,
    )

    assert result.error == expected_error
    assert result.iam_token == ("" if expected_error else "iam-token")


@pytest.mark.asyncio
async def test_service_skips_non_caller_and_rejects_missing_owner() -> None:
    service, identity, *_ = _service(context=_context(exchange=False))

    skipped = await service.get_iam_token(
        iam_token="iam-token", auth_request=_request(), bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT, publish_id=None, entity_id=None,
        is_test_exchange=False,
    )
    assert skipped.error is None

    identity.get_iam_token_context.return_value = _context(owner_id=None)
    missing_owner = await service.get_iam_token(
        iam_token="iam-token", auth_request=_request(), bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT, publish_id=None, entity_id=None,
        is_test_exchange=False,
    )
    assert missing_owner.error == CALLER_CREDENTIAL_REQUEST_INVALID


@pytest.mark.asyncio
async def test_service_maps_exchange_and_runtime_failures() -> None:
    service, identity, *_ = _service()
    identity.exchange_caller_identity.side_effect = CallerCredentialError("UPSTREAM")

    credential_failure = await service.get_iam_token(
        iam_token="iam-token", auth_request=_request(), bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT, publish_id=None, entity_id=None,
        is_test_exchange=True,
    )
    assert credential_failure.error == "UPSTREAM"
    identity.authorize_iam_token_exchange.assert_called_once()

    identity.exchange_caller_identity.side_effect = RuntimeError("BaaS unavailable")
    runtime_failure = await service.get_iam_token(
        iam_token="iam-token", auth_request=_request(), bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT, publish_id=None, entity_id=None,
        is_test_exchange=False,
    )
    assert runtime_failure.error == CALLER_OUTBOUND_UPDATE_FAILED


def test_unavailable_provider_and_module_provider_fail_closed() -> None:
    with pytest.raises(CallerCredentialError):
        UnavailableCallerTokenProvider().exchange(
            auth_context=MagicMock(), iam_token="iam", bot_id="bot",
            owner_user_id="owner", task_metadata={},
        )

    service = CallerIdentityModule().caller_iam_token_service(
        caller_identity=MagicMock(), auth_plugin=MagicMock(),
        token_provider=MagicMock(), runtime_updater=MagicMock(),
        runtime_bindings=MagicMock(), lock_repository=MagicMock(),
    )
    assert isinstance(service, CallerIamTokenService)

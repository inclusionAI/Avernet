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
)
from agentclaw.community.core.caller_identity.iam_token_service import (
    CallerIamTokenService,
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


def _service(*, context: CallerIamTokenContext | None = None):
    identity = MagicMock()
    identity.get_iam_token_context.return_value = context or _context()
    auth = MagicMock()
    auth.resolve_user_from_request = AsyncMock(
        return_value=SimpleNamespace(staffId="caller-1")
    )
    provider = MagicMock()
    updater = MagicMock()
    return (
        CallerIamTokenService(
            caller_identity=identity,
            auth_plugin=auth,
            token_provider=provider,
            runtime_updater=updater,
        ),
        identity,
        auth,
        provider,
        updater,
    )


def _request() -> AuthRequestContext:
    return AuthRequestContext({}, {}, {}, "http://test/")


@pytest.mark.asyncio
async def test_service_installs_caller_token_without_returning_it() -> None:
    identity = MagicMock()
    identity.get_iam_token_context.return_value = CallerIamTokenContext(
        bot_id="bot-1",
        owner_id="owner-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        bot_call_type=McpCallType.CALLER,
        should_exchange_caller_token=True,
        binding_id=9,
    )
    auth = MagicMock()
    auth.resolve_user_from_request = AsyncMock(
        return_value=SimpleNamespace(staffId="caller-1")
    )
    service = CallerIamTokenService(
        caller_identity=identity,
        auth_plugin=auth,
        token_provider=MagicMock(),
        runtime_updater=MagicMock(),
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
    )
    assert isinstance(service, CallerIamTokenService)

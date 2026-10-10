from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.adapters.http.token_exchange.router import get_iam_token
from agentclaw.community.api.caller_iam_token_service import (
    CallerIamTokenServiceProtocol,
)
from agentclaw.community.api.caller_identity_service import CallerIdentityStage
from agentclaw.community.core.caller_identity.contracts import CallerIamTokenOutcome


class _Request:
    cookies = {"IAM_TOKEN": "iam-token"}
    headers: dict[str, str] = {}
    query_params: dict[str, str] = {}
    base_url = "http://test/"


def _service(*, iam_token: str = "iam-token", error: str | None = None) -> MagicMock:
    service = MagicMock(spec=CallerIamTokenServiceProtocol)
    service.get_iam_token = AsyncMock(
        return_value=CallerIamTokenOutcome(iam_token=iam_token, error=error)
    )
    return service


@pytest.mark.asyncio
async def test_iam_route_delegates_caller_exchange_without_returning_token() -> None:
    service = _service()

    response = await get_iam_token(
        _Request(),
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id="entity-1",
        service=service,
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"success": True, "iam_token": "iam-token"}
    assert service.get_iam_token.call_args.kwargs["bot_id"] == "bot-1"


@pytest.mark.asyncio
async def test_iam_route_test_exchange_forces_exchange_for_non_caller_context() -> None:
    service = _service()

    response = await get_iam_token(
        _Request(),
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id=None,
        is_test_exchange=True,
        service=service,
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"success": True, "iam_token": "iam-token"}
    assert service.get_iam_token.call_args.kwargs["is_test_exchange"] is True


@pytest.mark.asyncio
async def test_iam_route_test_exchange_rejects_non_owner() -> None:
    response = await get_iam_token(
        _Request(),
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id=None,
        is_test_exchange=True,
        service=_service(error="CALLER_IDENTITY_FORBIDDEN"),
    )

    assert response.status_code == 403
    assert json.loads(response.body) == {
        "success": False,
        "error": "CALLER_IDENTITY_FORBIDDEN",
    }


@pytest.mark.asyncio
async def test_iam_route_test_exchange_rejects_production_environment() -> None:
    response = await get_iam_token(
        _Request(),
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id=None,
        is_test_exchange=True,
        service=_service(error="CALLER_IDENTITY_FORBIDDEN"),
    )

    assert response.status_code == 403
    assert json.loads(response.body) == {
        "success": False,
        "error": "CALLER_IDENTITY_FORBIDDEN",
    }


@pytest.mark.asyncio
async def test_iam_route_test_exchange_requires_bot_id() -> None:
    response = await get_iam_token(
        _Request(),
        bot_id=None,
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id=None,
        is_test_exchange=True,
        service=_service(error="CALLER_CREDENTIAL_REQUEST_INVALID"),
    )

    assert response.status_code == 400
    assert json.loads(response.body) == {
        "success": False,
        "error": "CALLER_CREDENTIAL_REQUEST_INVALID",
    }


@pytest.mark.asyncio
async def test_iam_route_rejects_ambiguous_bot_without_entity() -> None:
    response = await get_iam_token(
        _Request(),
        bot_id="default",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id=None,
        service=_service(error="CALLER_IDENTITY_AMBIGUOUS"),
    )

    assert response.status_code == 409
    assert json.loads(response.body) == {
        "success": False,
        "error": "CALLER_IDENTITY_AMBIGUOUS",
    }

@pytest.mark.asyncio
async def test_session_key_is_projected_and_credential_free_logs(caplog):
    import logging
    service = _service()
    with caplog.at_level(logging.INFO):
        response = await get_iam_token(_Request(), bot_id="bot-1", stage=CallerIdentityStage.DRAFT,
                                       publish_id=None, entity_id="entity-1", session_key="private-session", service=service)
    assert response.status_code == 200
    assert service.get_iam_token.call_args.kwargs["session_key"] == "private-session"
    assert "caller_iam_request_received" in caplog.text
    assert "caller_iam_response_succeeded" in caplog.text
    assert "private-session" not in caplog.text
    assert "iam-token" not in caplog.text


@pytest.mark.asyncio
async def test_session_boundary_failure_log_hides_exception_credentials(caplog):
    import logging
    service = _service(error="CALLER_IDENTITY_FORBIDDEN")
    with caplog.at_level(logging.INFO):
        response = await get_iam_token(_Request(), bot_id="bot-1", stage=CallerIdentityStage.DRAFT,
                                       publish_id=None, entity_id=None, session_key="private-session", service=service)
    assert response.status_code == 403
    assert "caller_iam_response_failed" in caplog.text
    assert "private-session" not in caplog.text and "iam-token" not in caplog.text

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 503])
async def test_iam_controlled_http_error_keeps_status_and_safe_detail(status, caplog):
    from fastapi import HTTPException
    service = _service()
    service.get_iam_token.side_effect = HTTPException(status_code=status, detail="private-session iam-token", headers={"Retry-After": "1"})
    with pytest.raises(HTTPException) as error:
        await get_iam_token(_Request(), bot_id="bot-1", stage=CallerIdentityStage.DRAFT,
                            publish_id=None, entity_id=None, session_key="private-session", service=service)
    assert error.value.status_code == status
    assert error.value.headers == {"Retry-After": "1"}
    assert error.value.__suppress_context__
    assert "private-session" not in str(error.value)
    assert "iam-token" not in caplog.text


@pytest.mark.asyncio
async def test_iam_unexpected_http_error_is_safe_500(caplog):
    from fastapi import HTTPException
    service = _service()
    service.get_iam_token.side_effect = ValueError("http://baas.test?session_key=private-session")
    with pytest.raises(HTTPException) as error:
        await get_iam_token(_Request(), bot_id="bot-1", stage=CallerIdentityStage.DRAFT,
                            publish_id=None, entity_id=None, session_key="private-session", service=service)
    assert error.value.status_code == 500
    assert error.value.__suppress_context__
    assert "private-session" not in str(error.value)
    assert "private-session" not in caplog.text

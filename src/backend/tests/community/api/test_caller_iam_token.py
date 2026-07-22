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

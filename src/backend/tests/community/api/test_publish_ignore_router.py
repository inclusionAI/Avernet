"""HTTP mapping stays thin; domain service owns permission and target policy."""

from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.service_bot.router_publish import (
    change_publish_ignore,
)
from agentclaw.community.adapters.http.service_bot.schemas_publish import (
    PublishIgnoreRequest,
)
from agentclaw.community.api.publish_ignore_service import PublishIgnoreError


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
@pytest.mark.parametrize(
    "error,code",
    [
        (None, 200),
        ("permission_denied", 403),
        ("binding_conflict", 409),
        ("unexpected", 500),
    ],
)
async def test_router_mapping(error, code, stage):
    service = AsyncMock()
    service.change.return_value = {"success": True, "results": []}
    if error:
        service.change.side_effect = (
            ValueError("secret-value")
            if error == "unexpected"
            else PublishIgnoreError(error)
        )
    request = PublishIgnoreRequest(
        bot_id="bot",
        entity_id="entity",
        stage=stage,
        operation="remove",
        path="workspace/cache",
    )
    result = await change_publish_ignore(
        request, AuthenticatedUser("id", "actor", "name"), service
    )
    assert result.error_code == code
    assert "secret-value" not in result.model_dump_json()
    assert service.change.call_args.args[1] == "actor"
    assert service.change.call_args.args[0].operation == "remove"
    assert service.change.call_args.args[0].stage == stage


@pytest.mark.parametrize(
    "field,value",
    [("stage", "eval"), ("operation", "delete"), ("version", 3)],
)
def test_request_validation(field, value):
    data = dict(
        bot_id="bot",
        entity_id="entity",
        stage="online",
        operation="add",
        path="cache",
    )
    data[field] = value
    with pytest.raises(ValidationError):
        PublishIgnoreRequest(**data)

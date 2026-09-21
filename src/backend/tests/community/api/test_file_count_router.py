"""HTTP adaptation emits stable errors and never exposes exception text."""
from unittest.mock import AsyncMock
import pytest
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.service_bot.router_file_count import query_file_count
from agentclaw.community.kernel.file_count import FileCountError


@pytest.mark.asyncio
@pytest.mark.parametrize("error,code", [(None, 200), ("permission_denied", 403),
                                      ("stage_not_bound", 409), ("unexpected", 500)])
async def test_safe_http_mapping(error, code, caplog):
    service = AsyncMock()
    service.query.return_value = {"success": True, "results": []}
    if error:
        service.query.side_effect = ValueError("secret-value") if error == "unexpected" else FileCountError(error)
    result = await query_file_count("bot", "entity", "draft", ".",
                                    AuthenticatedUser("id", "actor", "name"), service)
    assert result.error_code == code
    assert "secret-value" not in result.model_dump_json() + caplog.text
    query = service.query.call_args.args[0]
    assert query.stage == "draft"
    assert query.path == "."
    assert query.request_id


@pytest.mark.parametrize("field,value", [("stage", "eval"), ("path", "\x00"), ("path", "x" * 4097),
                                       ("bot_id", ""), ("entity_id", "")])
def test_invalid_http_parameters(client, field, value):
    params = {"bot_id": "bot", "entity_id": "entity", "stage": "draft", "path": "."}
    params[field] = value
    response = client.get("/api/service-bot/publish/ops/file-count", params=params,
                          headers={"x-user-id": "actor"})
    assert response.status_code == 422

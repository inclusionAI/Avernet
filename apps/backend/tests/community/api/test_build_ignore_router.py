"""Build-rule boundary mapping, schema, and error log confidentiality."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.service_bot.router_publish import (
    query_build_ignore,
    change_build_ignore,
)
from agentclaw.community.adapters.http.service_bot.schemas_publish import (
    BuildIgnoreRequest,
)
from agentclaw.community.kernel.build_ignore import BuildIgnoreError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,expected", [("permission_denied", 403), ("bot_not_found", 409)]
)
async def test_query_domain_error_mapping(code, expected):
    service = SimpleNamespace(query=AsyncMock(side_effect=BuildIgnoreError(code)))
    result = await query_build_ignore("b", "e", SimpleNamespace(staffId="u"), service)
    assert result.success is False
    assert result.message == code
    assert result.error_code == expected


@pytest.mark.asyncio
async def test_mutation_unexpected_error_hides_sql_credentials(caplog):
    service = SimpleNamespace(
        change=AsyncMock(side_effect=RuntimeError("password=secret-fixture"))
    )
    request = BuildIgnoreRequest(
        bot_id="b", entity_id="e", operation="add", path="workspace/bin"
    )
    result = await change_build_ignore(request, SimpleNamespace(staffId="u"), service)
    assert result.error_code == 500
    assert result.message == "build_ignore_failed"
    assert "secret-fixture" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_no_stage_version_or_client_selected_engine():
    for field in ("stage", "version", "engine_type"):
        with pytest.raises(ValidationError):
            BuildIgnoreRequest(
                bot_id="b",
                entity_id="e",
                operation="add",
                path="workspace/bin",
                **{field: "x"},
            )

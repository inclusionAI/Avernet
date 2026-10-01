"""Direct-call coverage for the entity-level identity router handlers.

These endpoints have no interceptor, so they can be invoked directly with a mocked
IdentityService. They are thin delegators that map ValueError → HTTP 400.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.identity import router as r
from agentclaw.community.core.services.identity import IdentityFileContent


def _ctx(user_id="u-1"):
    ctx = MagicMock()
    ctx.user_id = user_id
    return ctx


@pytest.mark.asyncio
async def test_get_entity_identity_file_delegates():
    svc = MagicMock()
    svc.get_entity_file = AsyncMock(return_value="RESP")
    out = await r.get_entity_identity_file(
        "staff", "u-1", "RULES.md", user_id=None, ctx=_ctx(), identity_service=svc,
    )
    assert out == "RESP"
    svc.get_entity_file.assert_awaited_once_with("staff", "u-1", "RULES.md", "u-1")


@pytest.mark.asyncio
async def test_get_entity_identity_file_value_error_maps_to_400():
    svc = MagicMock()
    svc.get_entity_file = AsyncMock(side_effect=ValueError("bad type"))
    with pytest.raises(HTTPException) as ei:
        await r.get_entity_identity_file(
            "staff", "u-1", "NOPE.md", user_id="u-1", ctx=_ctx(), identity_service=svc,
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_get_entity_identity_file_rejects_spoofed_operator():
    svc = MagicMock()
    svc.get_entity_file = AsyncMock(return_value="RESP")

    with pytest.raises(HTTPException) as ei:
        await r.get_entity_identity_file(
            "staff", "target", "RULES.md",
            user_id="spoofed-user", ctx=_ctx("authenticated-user"),
            identity_service=svc,
        )

    assert ei.value.status_code == 403
    svc.get_entity_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_entity_identity_file_delegates_and_maps_error():
    svc = MagicMock()
    svc.update_entity_file = AsyncMock(return_value="OK")
    body = IdentityFileContent(content="# c")
    out = await r.update_entity_identity_file(
        "staff", "u-1", "RULES.md", body, user_id=None, ctx=_ctx(), identity_service=svc,
    )
    assert out == "OK"
    svc.update_entity_file.assert_awaited_once_with("staff", "u-1", "RULES.md", "# c", "u-1")

    svc.update_entity_file = AsyncMock(side_effect=ValueError("bad"))
    with pytest.raises(HTTPException) as ei:
        await r.update_entity_identity_file(
            "bad", "u-1", "RULES.md", body, user_id=None, ctx=_ctx(), identity_service=svc,
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_list_entity_identity_files_delegates_and_maps_error():
    svc = MagicMock()
    svc.list_entity_files = AsyncMock(return_value="LIST")
    out = await r.list_entity_identity_files(
        "staff", "u-1", user_id=None, ctx=_ctx(), identity_service=svc,
    )
    assert out == "LIST"

    svc.list_entity_files = AsyncMock(side_effect=ValueError("bad entity"))
    with pytest.raises(HTTPException) as ei:
        await r.list_entity_identity_files(
            "bad", "u-1", user_id=None, ctx=_ctx(), identity_service=svc,
        )
    assert ei.value.status_code == 400

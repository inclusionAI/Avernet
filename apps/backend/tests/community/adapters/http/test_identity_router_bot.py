"""Direct-call coverage for the bot-level identity router handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.identity import router as r
from agentclaw.community.core.devices.services.device_context import (
    DeviceConnectionUnavailableError,
)


def _ctx(user_id="u-1"):
    ctx = MagicMock()
    ctx.user_id = user_id
    ctx.nick_name = user_id
    return ctx


@pytest.mark.asyncio
async def test_get_bot_identity_file_delegates():
    svc = MagicMock()
    svc.get_bot_file = AsyncMock(return_value="FILE")
    result = await r.get_bot_identity_file(
        "staff",
        "u-1",
        "bot-1",
        "RULES.md",
        user_id="u-1",
        publish_id=None,
        engine_type=None,
        ctx=_ctx(),
        identity_service=svc,
    )
    assert result == "FILE"
    svc.get_bot_file.assert_awaited_once_with(
        "staff",
        "u-1",
        "bot-1",
        "RULES.md",
        "u-1",
        publish_id=None,
        engine_type=None,
    )


@pytest.mark.asyncio
async def test_get_bot_identity_file_value_error_maps_to_400():
    svc = MagicMock()
    svc.get_bot_file = AsyncMock(side_effect=ValueError("bad file"))
    with pytest.raises(HTTPException) as e:
        await r.get_bot_identity_file(
            "staff",
            "u-1",
            "bot-1",
            "RULES.md",
            user_id="u-1",
            publish_id=None,
            engine_type=None,
            ctx=_ctx(),
            identity_service=svc,
        )
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_get_bot_identity_file_device_unavailable_maps_to_503():
    """Transient device unavailable maps to a retryable 503."""
    svc = MagicMock()
    svc.get_bot_file = AsyncMock(
        side_effect=DeviceConnectionUnavailableError("upstream device unavailable")
    )
    with pytest.raises(HTTPException) as e:
        await r.get_bot_identity_file(
            "staff",
            "u-1",
            "bot-1",
            "RULES.md",
            user_id="u-1",
            publish_id=None,
            engine_type=None,
            ctx=_ctx(),
            identity_service=svc,
        )
    assert e.value.status_code == 503
    assert e.value.detail == "Device temporarily unavailable"

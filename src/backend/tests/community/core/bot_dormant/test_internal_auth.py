"""Verify verify_dormant_internal_token correctly accepts/rejects.

Note: this exercises the Depends in isolation against the resolved
``DormantInternalToken`` wrapper. The yaml→wrapper resolution itself
(``@<secret>`` → Mist lookup, etc.) is covered separately by
``test_bot_dormant_module_token_resolver``.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from agentclaw.community.di.config import DormantInternalToken
from agentclaw.community.adapters.http.bot_dormant.auth import (
    verify_dormant_internal_token,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_accepts_matching_bearer_token():
    token_cfg = DormantInternalToken(value="abc123")
    # Should not raise
    await verify_dormant_internal_token(
        authorization="Bearer abc123", token_cfg=token_cfg
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rejects_wrong_token():
    token_cfg = DormantInternalToken(value="abc123")
    with pytest.raises(HTTPException) as exc:
        await verify_dormant_internal_token(
            authorization="Bearer wrong-token", token_cfg=token_cfg
        )
    assert exc.value.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rejects_missing_bearer_prefix():
    token_cfg = DormantInternalToken(value="abc123")
    with pytest.raises(HTTPException) as exc:
        await verify_dormant_internal_token(
            authorization="abc123", token_cfg=token_cfg
        )
    assert exc.value.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rejects_when_no_token_configured():
    """Even with a Bearer prefix, empty resolved token = 401."""
    token_cfg = DormantInternalToken(value="")
    with pytest.raises(HTTPException) as exc:
        await verify_dormant_internal_token(
            authorization="Bearer any-token", token_cfg=token_cfg
        )
    assert exc.value.status_code == 401

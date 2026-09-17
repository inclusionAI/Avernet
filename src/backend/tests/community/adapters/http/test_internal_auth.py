"""verify_internal_api_token accepts only the configured token.

The Depends is exercised in isolation against a resolved ``InternalApiToken``;
the secret-name → wrapper resolution is covered by
``tests/community/di/test_internal_api_token.py``.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.internal_auth import verify_internal_api_token
from agentclaw.community.di.config import InternalApiToken


@pytest.mark.unit
@pytest.mark.asyncio
async def test_accepts_matching_bearer_token():
    await verify_internal_api_token(
        authorization="Bearer abc123",
        token_cfg=InternalApiToken(value="abc123"),
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authorization", "configured"),
    [
        ("Bearer wrong-token", "abc123"),
        ("abc123", "abc123"),  # no Bearer prefix
        ("bearer abc123", "abc123"),  # prefix is case-sensitive
        ("Bearer abc123 ", "abc123"),  # trailing whitespace is part of the token
        ("Bearer ", "abc123"),
        (None, "abc123"),  # header absent entirely
        # An empty resolved token means the routes are closed, so even a
        # well-formed header is refused rather than matched against "".
        ("Bearer any-token", ""),
        ("Bearer ", ""),
        (None, ""),
    ],
)
async def test_rejects_everything_else(authorization, configured):
    with pytest.raises(HTTPException) as exc:
        await verify_internal_api_token(
            authorization=authorization,
            token_cfg=InternalApiToken(value=configured),
        )

    assert exc.value.status_code == 401

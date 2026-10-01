"""verify_skill_center_internal_token accepts only the configured token.

Exercises the Depends in isolation against the resolved
``SkillCenterInternalToken`` wrapper; the secret-name → wrapper resolution is
covered by ``tests/community/di/test_skill_center_internal_token.py``.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.skill_center.internal_auth import (
    verify_skill_center_internal_token,
)
from agentclaw.community.di.config import SkillCenterInternalToken


@pytest.mark.unit
@pytest.mark.asyncio
async def test_accepts_matching_bearer_token():
    await verify_skill_center_internal_token(
        authorization="Bearer abc123",
        token_cfg=SkillCenterInternalToken(value="abc123"),
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authorization", "configured"),
    [
        ("Bearer wrong-token", "abc123"),
        ("abc123", "abc123"),  # no Bearer prefix
        ("Bearer ", "abc123"),
        # Empty resolved token = the endpoints are off, so even a well-formed
        # header is refused rather than matched against "".
        ("Bearer any-token", ""),
        ("Bearer ", ""),
    ],
)
async def test_rejects_everything_else(authorization: str, configured: str):
    with pytest.raises(HTTPException) as exc:
        await verify_skill_center_internal_token(
            authorization=authorization,
            token_cfg=SkillCenterInternalToken(value=configured),
        )

    assert exc.value.status_code == 401

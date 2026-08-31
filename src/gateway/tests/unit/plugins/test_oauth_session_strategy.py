"""Unit tests for the ``oauth_session`` user strategy (bcs_session JWT → UserPrincipal)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from gateway.community.plugins.authn.oauth_session import OauthSessionStrategy
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import CredentialBundle, UserPrincipal

_TEST_SECRET = "test-bcs-session-secret-32-bytes!!"


def _creds(token: str | None) -> CredentialBundle:
    cookies = {"bcs_session": token} if token else {}
    return CredentialBundle(headers={}, cookies=cookies, query={})


def _token(*, sub: str = "user-123", src: str = "google") -> str:
    now = datetime.now(tz=UTC)
    claims = {
        "sub": sub,
        "src": src,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    return jwt.encode(claims, _TEST_SECRET, algorithm="HS256")


@pytest.mark.asyncio
async def test_absent_cookie_returns_none() -> None:
    strategy = OauthSessionStrategy(jwt_secret=_TEST_SECRET)
    assert await strategy.build(_creds(None)) is None


@pytest.mark.asyncio
async def test_valid_cookie_builds_user_principal() -> None:
    strategy = OauthSessionStrategy(jwt_secret=_TEST_SECRET)
    result = await strategy.build(_creds(_token()))

    assert isinstance(result, UserPrincipal)
    assert result.subject.id == "user-123"
    assert result.subject.username == "user-123"
    assert result.subject.display_name is None


@pytest.mark.asyncio
async def test_invalid_cookie_raises_auth_error() -> None:
    strategy = OauthSessionStrategy(jwt_secret=_TEST_SECRET)
    bad_token = jwt.encode(
        {"sub": "user-123", "src": "google", "iat": 1, "exp": 9999999999},
        "wrong-secret",
        algorithm="HS256",
    )

    with pytest.raises(AuthError):
        await strategy.build(_creds(bad_token))

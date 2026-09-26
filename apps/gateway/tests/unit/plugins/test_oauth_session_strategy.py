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


def _token(*, sub: str = "user-123", src: str = "google", name: str = "alice") -> str:
    now = datetime.now(tz=UTC)
    claims = {
        "sub": sub,
        "src": src,
        "name": name,
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
    assert result.subject.username == "alice"
    assert result.subject.display_name is None


class _MissingSecretResolver:
    def get_secret(self, name: str):
        return None


class _EmptySecretResolver:
    class _Secret:
        secret_value = "   "

    def get_secret(self, name: str):
        return self._Secret()


@pytest.mark.asyncio
async def test_missing_secret_resolver_raises_auth_error() -> None:
    strategy = OauthSessionStrategy(secret_resolver=_MissingSecretResolver())

    with pytest.raises(AuthError):
        await strategy.build(_creds(_token()))


@pytest.mark.asyncio
async def test_empty_secret_value_raises_auth_error() -> None:
    strategy = OauthSessionStrategy(secret_resolver=_EmptySecretResolver())

    with pytest.raises(AuthError):
        await strategy.build(_creds(_token()))


@pytest.mark.asyncio
async def test_valid_cookie_without_name_falls_back_to_subject_id() -> None:
    strategy = OauthSessionStrategy(jwt_secret=_TEST_SECRET)
    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "user-123",
            "src": "google",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        _TEST_SECRET,
        algorithm="HS256",
    )

    result = await strategy.build(_creds(token))

    assert isinstance(result, UserPrincipal)
    assert result.subject.id == "user-123"
    assert result.subject.username == "user-123"


@pytest.mark.asyncio
async def test_missing_sub_claim_raises_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy = OauthSessionStrategy(jwt_secret=_TEST_SECRET)
    token = _token()

    monkeypatch.setattr(
        jwt, "decode", lambda *args, **kwargs: {"src": "google", "iat": 1, "exp": 2}
    )

    with pytest.raises(AuthError):
        await strategy.build(_creds(token))


@pytest.mark.asyncio
async def test_expired_cookie_raises_expired_login_error() -> None:
    strategy = OauthSessionStrategy(jwt_secret=_TEST_SECRET)
    now = datetime.now(tz=UTC)
    expired_token = jwt.encode(
        {
            "sub": "user-123",
            "src": "google",
            "iat": int((now - timedelta(minutes=10)).timestamp()),
            "exp": int((now - timedelta(minutes=5)).timestamp()),
        },
        _TEST_SECRET,
        algorithm="HS256",
    )

    with pytest.raises(AuthError, match="bcs session cookie has expired"):
        await strategy.build(_creds(expired_token))


@pytest.mark.asyncio
async def test_invalid_cookie_raises_auth_error() -> None:
    strategy = OauthSessionStrategy(jwt_secret=_TEST_SECRET)
    bad_token = jwt.encode(
        {"sub": "user-123", "src": "google", "iat": 1, "exp": 9999999999},
        "wrong-secret-for-test-only-32-bytes!!",
        algorithm="HS256",
    )

    with pytest.raises(AuthError):
        await strategy.build(_creds(bad_token))

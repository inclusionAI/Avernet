"""Unit tests for the ``google`` user strategy (Google access token → UserPrincipal).

Uses :class:`httpx.MockTransport` so no real network call is made against
Google's userinfo endpoint.
"""

from __future__ import annotations

import httpx
import pytest

from gateway.community.plugins.authn.google_token import GoogleUserStrategy
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import CredentialBundle, UserPrincipal


def _userinfo_handler(body: object, status: int = 200) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body if isinstance(body, dict) else {})

    return httpx.MockTransport(_handler)


def _creds(token: str | None) -> CredentialBundle:
    headers = {"x-google-token": token} if token else {}
    return CredentialBundle(headers=headers, cookies={}, query={})


_GOOGLE_BODY = {
    "sub": "g-123",
    "email": "alice@example.com",
    "name": "Alice",
}


async def test_applicable_token_resolves_user_principal() -> None:
    transport = _userinfo_handler(_GOOGLE_BODY)
    strat = GoogleUserStrategy(
        token_header="x-google-token",
        default_tenant="t-default",
        transport=transport,
    )
    result = await strat.build(_creds("google-access-token"))
    assert isinstance(result, UserPrincipal)
    assert result.tenant == "t-default"
    assert result.subject.id == "g-123"
    assert result.subject.username == "alice@example.com"
    assert result.subject.display_name == "Alice"


async def test_absent_token_returns_none() -> None:
    strat = GoogleUserStrategy(
        token_header="x-google-token",
        default_tenant="t-default",
        transport=_userinfo_handler(_GOOGLE_BODY),
    )
    assert await strat.build(_creds(None)) is None


async def test_non_200_raises_auth_error() -> None:
    strat = GoogleUserStrategy(
        token_header="x-google-token",
        default_tenant="t-default",
        transport=_userinfo_handler({}, status=401),
    )
    with pytest.raises(AuthError):
        await strat.build(_creds("bad"))


async def test_malformed_json_raises_auth_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"not-json", headers={"content-type": "text"}
        )

    strat = GoogleUserStrategy(
        token_header="x-google-token",
        default_tenant="t-default",
        transport=httpx.MockTransport(_handler),
    )
    with pytest.raises(AuthError):
        await strat.build(_creds("google-access-token"))


async def test_missing_sub_raises_auth_error() -> None:
    strat = GoogleUserStrategy(
        token_header="x-google-token",
        default_tenant="t-default",
        transport=_userinfo_handler({"email": "no-sub@example.com"}),
    )
    with pytest.raises(AuthError):
        await strat.build(_creds("google-access-token"))


async def test_request_to_userinfo_carries_bearer_authorization() -> None:
    received: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        received.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=_GOOGLE_BODY)

    strat = GoogleUserStrategy(
        token_header="x-google-token",
        default_tenant="t-default",
        transport=httpx.MockTransport(_handler),
    )
    await strat.build(_creds("google-access-token"))
    assert received == ["Bearer google-access-token"]


async def test_email_falls_back_to_sub_when_missing() -> None:
    strat = GoogleUserStrategy(
        token_header="x-google-token",
        default_tenant="t-default",
        transport=_userinfo_handler({"sub": "g-9"}),
    )
    result = await strat.build(_creds("tok"))
    assert isinstance(result, UserPrincipal)
    assert result.subject.username == "g-9"

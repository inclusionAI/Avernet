"""Unit tests for the google user strategy (verifies a Google access token).

The strategy calls Google's userinfo endpoint via ``httpx``; tests inject an
:class:`httpx.MockTransport` so no network is involved.
"""

from __future__ import annotations

import httpx
import pytest

from gateway.community.plugins.authn.google_token import GoogleUserStrategy
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    CredentialBundle,
    PrincipalType,
    UserPrincipal,
)

_TOKEN_HEADER = "x-user-token"


def _transport(good_body: dict[str, str] | None = None) -> httpx.MockTransport:
    """A mock transport returning a verified user for the ``good`` token."""

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        token = auth[len("Bearer ") :] if auth.startswith("Bearer ") else ""
        if token == "good" and good_body is not None:
            return httpx.Response(200, json=good_body)
        if token == "broken":
            # 200 but body missing required fields.
            return httpx.Response(200, json={"name": "No Sub Here"})
        return httpx.Response(401, text="invalid token")

    return httpx.MockTransport(handler)


def _creds(token: str | None) -> CredentialBundle:
    headers: dict[str, str] = {}
    if token is not None:
        headers[_TOKEN_HEADER] = token
    return CredentialBundle(headers=headers, cookies={}, query={})


def _strategy(transport: httpx.MockTransport | None = None) -> GoogleUserStrategy:
    return GoogleUserStrategy(
        token_header=_TOKEN_HEADER,
        default_tenant="tenant-default",
        transport=transport,
    )


async def test_declares_user_type() -> None:
    assert _strategy().principal_type is PrincipalType.USER


async def test_returns_none_when_no_token_presented() -> None:
    transport = _transport(good_body={"sub": "sub-1", "email": "alice@example.com"})
    assert await _strategy(transport).build(_creds(None)) is None


async def test_builds_user_principal_from_verified_token() -> None:
    transport = _transport(
        good_body={"sub": "sub-1", "name": "Alice", "email": "alice@example.com"}
    )
    principal = await _strategy(transport).build(_creds("good"))
    assert isinstance(principal, UserPrincipal)
    assert principal.tenant == "tenant-default"
    assert principal.subject.id == "sub-1"
    assert principal.subject.username == "alice@example.com"
    assert principal.subject.display_name == "Alice"


async def test_invalid_token_raises_autherror() -> None:
    transport = _transport(good_body={"sub": "sub-1"})
    with pytest.raises(AuthError):
        await _strategy(transport).build(_creds("bad-token"))


async def test_unparseable_userinfo_raises_autherror() -> None:
    transport = _transport(good_body={"sub": "sub-1"})
    with pytest.raises(AuthError):
        await _strategy(transport).build(_creds("broken"))

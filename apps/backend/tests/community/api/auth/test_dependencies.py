"""Tests for core.auth.dependencies (post Rule 14)."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.adapters.http.auth.dependencies import (
    _build_auth_context,
    get_current_staff_id,
    get_current_user,
    get_device_connection_user,
    require_operator,
)
from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.core.errors import (
    Forbidden,
    LoginRedirectRequired,
    Unauthorized,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext


class FakeQueryParams(dict):
    """Minimal QueryParams-like object for unit tests."""

    pass


class FakeHeaders(dict):
    """FastAPI's request.headers iterates as ``(k, v)`` pairs of strings —
    a plain dict satisfies that."""

    pass


class FakeRequest:
    """Minimal Request-like object for unit tests."""

    def __init__(self, cookies=None, headers=None, query_params=None, base_url=""):
        self.cookies = cookies or {}
        self._headers = FakeHeaders(headers or {})
        self.query_params = FakeQueryParams(query_params or {})
        self.base_url = base_url

    @property
    def headers(self):
        return self._headers


def _iam_token(sno: str) -> str:
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sno": sno}).encode()).decode().rstrip("=")
    )
    return f"header.{payload}.signature"


class FakeSecretResolver:
    def __init__(self, value: str | None):
        self.value = value

    def get_secret(self, _name: str):
        if self.value is None:
            return None
        return SimpleNamespace(secret_value=self.value)


# ============================================================
# _build_auth_context — snapshots the request
# ============================================================


def test_build_auth_context_includes_cookies_headers_query_baseurl():
    req = FakeRequest(
        cookies={"staff_id": "1"},
        headers={"X-Foo": "bar"},
        query_params={"user_id": "u"},
        base_url="http://host/",
    )
    ctx = _build_auth_context(req)
    assert isinstance(ctx, AuthRequestContext)
    assert ctx.cookies == {"staff_id": "1"}
    assert ctx.headers == {"X-Foo": "bar"}
    assert ctx.query_params == {"user_id": "u"}
    assert ctx.base_url == "http://host/"


# ============================================================
# get_current_user — delegates to AuthPlugin
# ============================================================


@pytest.mark.asyncio
async def test_get_current_user_delegates_to_plugin():
    fake_user = AuthenticatedIdentity(id="1", operatorName="u", outUserNo="1")
    plugin = AsyncMock()
    plugin.resolve_user_from_request = AsyncMock(return_value=fake_user)
    req = FakeRequest(cookies={"staff_id": "1"})

    user = await get_current_user(req, auth_plugin=plugin)

    # Plugin's AuthenticatedIdentity is converted to the adapter's
    # AuthenticatedUser at the boundary. Field-for-field copy.
    from agentclaw.community.adapters.http.auth.models import AuthenticatedUser

    assert isinstance(user, AuthenticatedUser)
    assert user.id == fake_user.id
    assert user.staffId == fake_user.staffId
    assert user.operatorName == fake_user.operatorName

    # Plugin received the snapshot built from the request.
    plugin.resolve_user_from_request.assert_awaited_once()
    ctx = plugin.resolve_user_from_request.await_args.args[0]
    assert isinstance(ctx, AuthRequestContext)
    assert ctx.cookies == {"staff_id": "1"}


@pytest.mark.asyncio
async def test_get_current_user_preserves_plugin_domain_errors():
    """If the plugin raises a precise DomainError (e.g. Unauthorized in
    local-mode missing-identity), the dep does NOT collapse it to
    LoginRedirectRequired — the precise error propagates."""
    plugin = AsyncMock()
    plugin.resolve_user_from_request = AsyncMock(
        side_effect=Unauthorized("Local mode: user identity required."),
    )
    req = FakeRequest()
    with pytest.raises(Unauthorized) as ei:
        await get_current_user(req, auth_plugin=plugin)
    assert "Local mode" in ei.value.detail


@pytest.mark.asyncio
async def test_get_current_user_collapses_transport_errors_to_redirect():
    """A plugin transport/parse failure (RuntimeError, network etc.)
    collapses to LoginRedirectRequired — same wire behavior as today."""
    plugin = AsyncMock()
    plugin.resolve_user_from_request = AsyncMock(
        side_effect=RuntimeError("network died"),
    )
    req = FakeRequest(cookies={"some": "cookie"})

    with pytest.raises(LoginRedirectRequired) as ei:
        await get_current_user(req, auth_plugin=plugin)
    assert ei.value.detail == "missing login cookie"
    assert isinstance(ei.value.__cause__, RuntimeError)


# ============================================================
# Other deps unchanged
# ============================================================


def test_get_current_staff_id_missing_header_raises_unauthorized():
    with pytest.raises(Unauthorized) as ei:
        get_current_staff_id(x_staff_id=None)
    assert ei.value.detail == "missing login context"


@pytest.mark.asyncio
async def test_require_operator_denied_raises_forbidden():
    user = AuthenticatedIdentity(id="1", operatorName="u", outUserNo="1")
    plugin = MagicMock()
    plugin.is_operator_allowed = lambda u: False
    with pytest.raises(Forbidden) as ei:
        await require_operator(user=user, auth_plugin=plugin)
    assert "权限不足" in ei.value.detail


# ============================================================
# get_device_connection_user — internal assertion or Cookie fallback
# ============================================================


@pytest.mark.asyncio
async def test_device_connection_user_accepts_service_bearer_and_iam_token(
    monkeypatch,
):
    import agentclaw.community.adapters.http.auth.dependencies as deps

    monkeypatch.setattr(
        deps,
        "_block",
        lambda _name: {"access_secret_name": "dima-secret"},
    )
    plugin = AsyncMock()
    req = FakeRequest(
        headers={
            "authorization": "Bearer connection-secret",
            "x-iam-token": _iam_token("136677"),
        }
    )

    user = await get_device_connection_user(
        req,
        auth_plugin=plugin,
        secret_resolver=FakeSecretResolver("connection-secret"),
    )

    assert user.staffId == "136677"
    plugin.resolve_user_from_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_device_connection_user_rejects_bad_bearer_without_cookie_fallback(
    monkeypatch,
):
    import agentclaw.community.adapters.http.auth.dependencies as deps

    monkeypatch.setattr(
        deps,
        "_block",
        lambda _name: {"access_secret_name": "dima-secret"},
    )
    plugin = AsyncMock()
    req = FakeRequest(
        cookies={"IAM_TOKEN": "browser-cookie"},
        headers={
            "authorization": "Bearer wrong",
            "x-iam-token": _iam_token("136677"),
        },
    )

    with pytest.raises(Unauthorized):
        await get_device_connection_user(
            req,
            auth_plugin=plugin,
            secret_resolver=FakeSecretResolver("connection-secret"),
        )
    plugin.resolve_user_from_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_device_connection_user_without_iam_token_uses_existing_auth():
    fake_user = AuthenticatedIdentity(id="1", operatorName="u", outUserNo="1")
    plugin = AsyncMock()
    plugin.resolve_user_from_request = AsyncMock(return_value=fake_user)
    req = FakeRequest(cookies={"staff_id": "1"})

    user = await get_device_connection_user(
        req,
        auth_plugin=plugin,
        secret_resolver=FakeSecretResolver(None),
    )

    assert user.staffId == "1"
    plugin.resolve_user_from_request.assert_awaited_once()

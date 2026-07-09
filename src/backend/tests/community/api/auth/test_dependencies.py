"""Tests for core.auth.dependencies (post Rule 14)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from agentclaw.community.adapters.http.auth.dependencies import (
    _build_auth_context,
    get_current_staff_id,
    get_current_user,
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

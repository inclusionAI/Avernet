"""Unit tests for the community OidcAuthPlugin (BCS unified-auth delegation).

The class name is retained for historical reasons; the impl delegates identity
resolution to BCS ``GET /auth/user``. Two seams keep tests offline:
``_userinfo_resolver`` returns a canned ``UserInfoResponse`` dict (mapping /
operator / entity-access), and ``_parse_response`` is exercised directly with
``httpx.Response`` objects (HTTP status → error mapping). ``_transport`` covers
the network-error → RuntimeError wrap.
"""
from __future__ import annotations

import httpx
import pytest

from agentclaw.community.core.errors import Forbidden, Unauthorized
from agentclaw.community.di import config_community as cfg
from agentclaw.community.plugin_api.auth import AuthenticatedIdentity, AuthRequestContext
from agentclaw.community.plugins.community.auth import OidcAuthPlugin


def _plugin(**over):
    base = dict(
        base_url="http://bcs.test:21000",
        user_path="/auth/user",
        timeout=5.0,
        operator_subjects=frozenset({"admin-1"}),
    )
    base.update(over)
    return OidcAuthPlugin(cfg.BcsAuthConfig(**base))


def _ctx(cookies: dict | None = None):
    return AuthRequestContext(cookies=cookies or {})


@pytest.mark.asyncio
async def test_resolve_maps_userinfo():
    p = _plugin()
    p._userinfo_resolver = lambda _hdr: {
        "user_id": "u-1", "name": "Alice", "provider": "github", "avatar": None,
    }
    identity = await p.resolve_user_from_request(_ctx({"bcs_session": "tok"}))
    assert isinstance(identity, AuthenticatedIdentity)
    assert identity.id == "u-1"
    assert identity.staffId == "u-1"      # canonical handle = BCS user_id
    assert identity.operatorName == "u-1"  # BCS has no separate account name
    assert identity.nickName == "Alice"
    assert identity.tenantId is None


@pytest.mark.asyncio
async def test_resolve_forwards_all_cookies():
    p = _plugin()
    seen = {}
    def _resolver(hdr):
        seen["hdr"] = hdr
        return {"user_id": "u-1", "name": None}
    p._userinfo_resolver = _resolver
    await p.resolve_user_from_request(_ctx({"bcs_session": "tok", "x": "y"}))
    assert "bcs_session=tok" in seen["hdr"]
    assert "x=y" in seen["hdr"]


@pytest.mark.asyncio
async def test_get_login_user_passes_cookie_string_through():
    p = _plugin()
    seen = {}
    def _resolver(hdr):
        seen["hdr"] = hdr
        return {"user_id": "u-9", "name": None}
    p._userinfo_resolver = _resolver
    identity = await p.get_login_user("bcs_session=abc")
    assert identity.staffId == "u-9"
    assert seen["hdr"] == "bcs_session=abc"


@pytest.mark.asyncio
async def test_missing_cookie_unauthorized():
    p = _plugin()
    with pytest.raises(Unauthorized):
        await p.resolve_user_from_request(_ctx())
    with pytest.raises(Unauthorized):
        await p.get_login_user("")


@pytest.mark.asyncio
async def test_missing_user_id_unauthorized():
    p = _plugin()
    p._userinfo_resolver = lambda _hdr: {"user_id": "", "name": "x"}
    with pytest.raises(Unauthorized):
        await p.resolve_user_from_request(_ctx({"bcs_session": "tok"}))


def test_is_operator_allowed():
    p = _plugin()
    assert p.is_operator_allowed("admin-1") is True
    assert p.is_operator_allowed("u-1") is False


@pytest.mark.asyncio
async def test_authorize_entity_access_defaults_to_self():
    p = _plugin()
    p._userinfo_resolver = lambda _hdr: {"user_id": "u-1", "name": None}
    eid, etype = await p.authorize_entity_access(_ctx({"bcs_session": "t"}), None, None)
    assert eid == "u-1"
    assert etype == "staff"


@pytest.mark.asyncio
async def test_authorize_entity_access_forbids_other_user():
    p = _plugin()
    p._userinfo_resolver = lambda _hdr: {"user_id": "u-1", "name": None}
    with pytest.raises(Forbidden):
        await p.authorize_entity_access(_ctx({"bcs_session": "t"}), "other", "staff")


@pytest.mark.asyncio
async def test_authorize_entity_access_requires_auth():
    p = _plugin()
    with pytest.raises(Unauthorized):
        await p.authorize_entity_access(_ctx(), None, None)


# -- HTTP status → error mapping (pure _parse_response) --------------------


def test_parse_response_200_returns_json():
    p = _plugin()
    body = {"user_id": "u-7", "name": "Bob"}
    assert p._parse_response(httpx.Response(200, json=body)) == body


def test_parse_response_401_unauthorized():
    p = _plugin()
    with pytest.raises(Unauthorized):
        p._parse_response(httpx.Response(401, json={"error": "not authenticated"}))


def test_parse_response_404_runtime_error():
    p = _plugin()
    with pytest.raises(RuntimeError):
        p._parse_response(httpx.Response(404, json={"error": "not found"}))


def test_parse_response_500_runtime_error():
    p = _plugin()
    with pytest.raises(RuntimeError):
        p._parse_response(httpx.Response(500))


@pytest.mark.asyncio
async def test_live_http_path_sends_cookie_and_maps_response():
    """Drive the real AsyncClient path via MockTransport: verify the request
    URL + Cookie header and that a 200 body maps to an identity."""
    seen = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["cookie"] = request.headers.get("Cookie")
        return httpx.Response(200, json={"user_id": "u-42", "name": "Carol"})

    p = _plugin()
    p._transport = httpx.MockTransport(_handler)
    identity = await p.resolve_user_from_request(_ctx({"bcs_session": "tok"}))

    assert seen["url"] == "http://bcs.test:21000/auth/user"
    assert seen["cookie"] == "bcs_session=tok"
    assert identity.staffId == "u-42"
    assert identity.nickName == "Carol"


@pytest.mark.asyncio
async def test_network_error_wrapped_as_runtime_error():
    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")
    p = _plugin()
    p._transport = httpx.MockTransport(_boom)
    with pytest.raises(RuntimeError):
        await p.resolve_user_from_request(_ctx({"bcs_session": "t"}))

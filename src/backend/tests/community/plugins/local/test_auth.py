"""Tests for LocalAuth local plugin."""
import pytest
from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.plugin_api.auth import AuthPlugin
from agentclaw.community.plugins.local.auth import LocalAuth


def test_local_auth_conforms_to_protocol():
    auth = LocalAuth()
    assert isinstance(auth, AuthPlugin)


@pytest.mark.asyncio
async def test_get_login_user_returns_user():
    auth = LocalAuth()
    user = await auth.get_login_user(cookie="staff_id=12345; nick_name=test")
    assert user.staffId == "12345"
    assert user.nickName == "test"


@pytest.mark.asyncio
async def test_get_login_user_with_minimal_cookie():
    auth = LocalAuth()
    user = await auth.get_login_user(cookie="staff_id=99")
    assert user.staffId == "99"
    assert user.nickName == "99"


@pytest.mark.asyncio
async def test_get_login_user_empty_cookie_raises():
    from fastapi import HTTPException
    auth = LocalAuth()
    with pytest.raises(HTTPException) as exc_info:
        await auth.get_login_user(cookie="")
    assert exc_info.value.status_code == 401


def test_is_operator_allowed_always_true():
    auth = LocalAuth()
    user = AuthenticatedIdentity(id="1", operatorName="anyone", outUserNo="1")
    assert auth.is_operator_allowed(user) is True


# ============================================================
# resolve_user_from_request (Rule 14 / site #1)
# ============================================================

import pytest
from agentclaw.community.core.errors import Unauthorized
from agentclaw.community.plugin_api.auth import AuthRequestContext
from agentclaw.community.plugins.local.auth import LocalAuth


class TestResolveUserFromRequest:
    @pytest.mark.asyncio
    async def test_cookie_staff_id(self):
        ctx = AuthRequestContext(cookies={"staff_id": "12345", "nick_name": "test"})
        user = await LocalAuth().resolve_user_from_request(ctx)
        assert user.staffId == "12345"
        assert user.nickName == "test"

    @pytest.mark.asyncio
    async def test_cookie_entity_id_fallback(self):
        ctx = AuthRequestContext(cookies={"entity_id": "67890"})
        user = await LocalAuth().resolve_user_from_request(ctx)
        assert user.staffId == "67890"

    @pytest.mark.asyncio
    async def test_header_x_user_id_fallback(self):
        ctx = AuthRequestContext(headers={"x-user-id": "h123"})
        user = await LocalAuth().resolve_user_from_request(ctx)
        assert user.staffId == "h123"

    @pytest.mark.asyncio
    async def test_query_user_id_fallback(self):
        ctx = AuthRequestContext(query_params={"user_id": "q456"})
        user = await LocalAuth().resolve_user_from_request(ctx)
        assert user.staffId == "q456"

    @pytest.mark.asyncio
    async def test_raises_unauthorized_when_no_identity(self):
        ctx = AuthRequestContext()
        with pytest.raises(Unauthorized) as ei:
            await LocalAuth().resolve_user_from_request(ctx)
        assert "Local mode" in ei.value.detail

    @pytest.mark.asyncio
    async def test_url_decodes_nick_name(self):
        ctx = AuthRequestContext(
            cookies={"staff_id": "1", "nick_name": "%E5%BC%80%E5%8F%91%E8%80%85"},
        )
        user = await LocalAuth().resolve_user_from_request(ctx)
        assert user.nickName == "开发者"

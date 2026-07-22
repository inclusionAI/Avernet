"""Unit tests for BareAuthPlugin and AuthenticatedUser model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.spi.auth import AuthenticatedUser, AuthError

# ── AuthenticatedUser model ───────────────────────────────────────────────────


class TestAuthenticatedUser:
    def test_basic_construction(self) -> None:
        user = AuthenticatedUser(id="u1", username="op1")
        assert user.id == "u1"
        assert user.username == "op1"

    def test_required_fields_must_be_provided(self) -> None:
        # id and username are mandatory (no `| None`, no default).
        with pytest.raises(ValidationError):
            AuthenticatedUser(id="u1")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            AuthenticatedUser(username="op1")  # type: ignore[call-arg]

    def test_optional_fields_default_none(self) -> None:
        user = AuthenticatedUser(id="u1", username="op1")
        assert user.display_name is None
        assert user.full_name is None
        assert user.email is None
        assert user.phone is None
        assert user.tenant_id is None

    def test_all_fields(self) -> None:
        user = AuthenticatedUser(
            id="u1",
            username="domain_admin",
            display_name="Ada",
            full_name="Ada Lovelace",
            email="ada@example.com",
            phone="+10000000000",
            tenant_id="tenant-a",
        )
        assert user.display_name == "Ada"
        assert user.full_name == "Ada Lovelace"
        assert user.email == "ada@example.com"
        assert user.phone == "+10000000000"
        assert user.tenant_id == "tenant-a"

    def test_json_roundtrip(self) -> None:
        user = AuthenticatedUser(id="u1", username="op1", tenant_id="t1")
        data = user.model_dump()
        assert data["id"] == "u1"
        assert data["tenant_id"] == "t1"
        restored = AuthenticatedUser.model_validate(data)
        assert restored == user


# ── AuthError ────────────────────────────────────────────────────────────────


class TestAuthError:
    def test_is_exception(self) -> None:
        err = AuthError("auth failed")
        assert isinstance(err, Exception)
        assert str(err) == "auth failed"

    def test_can_be_raised(self) -> None:
        with pytest.raises(AuthError, match="denied"):
            raise AuthError("access denied")


# ── BareAuthPlugin ────────────────────────────────────────────────────────────


class TestBareAuthPlugin:
    def test_default_user_values(self) -> None:
        plugin = BareAuthPlugin()
        user = plugin._default_user
        assert user.id == "bare-user-001"
        assert user.username == "bare_operator"
        assert user.display_name == "Bare User"

    @pytest.mark.asyncio
    async def test_get_login_user_returns_default(self) -> None:
        plugin = BareAuthPlugin()
        user = await plugin.get_login_user()
        assert user.id == "bare-user-001"

    @pytest.mark.asyncio
    async def test_get_login_user_ignores_cookie_referer(self) -> None:
        plugin = BareAuthPlugin()
        user1 = await plugin.get_login_user(cookie="abc", referer="http://x")
        user2 = await plugin.get_login_user()
        assert user1 is user2 or user1.id == user2.id

    @pytest.mark.asyncio
    async def test_custom_user_injection(self) -> None:
        custom = AuthenticatedUser(id="custom-001", username="custom_op")
        plugin = BareAuthPlugin(default_user=custom)
        user = await plugin.get_login_user()
        assert user.id == "custom-001"
        assert user.username == "custom_op"

    def test_is_allowed_always_true(self) -> None:
        plugin = BareAuthPlugin()
        user = AuthenticatedUser(id="any", username="any")
        assert plugin.is_allowed(user) is True

    def test_is_allowed_with_none_user(self) -> None:
        plugin = BareAuthPlugin()
        assert plugin.is_allowed(None) is True  # type: ignore[arg-type]

    def test_check_permission_always_true(self) -> None:
        plugin = BareAuthPlugin()
        assert plugin.check_permission("user1", "perm:read") is True
        assert plugin.check_permission("user1", "perm:write", "/api/test", "{}") is True

    def test_check_permission_empty_args(self) -> None:
        plugin = BareAuthPlugin()
        assert plugin.check_permission("", "") is True

    @pytest.mark.asyncio
    async def test_multiple_instances_independent(self) -> None:
        p1 = BareAuthPlugin()
        p2 = BareAuthPlugin(default_user=AuthenticatedUser(id="other", username="op"))
        u1 = await p1.get_login_user()
        u2 = await p2.get_login_user()
        assert u1.id == "bare-user-001"
        assert u2.id == "other"

"""Unit tests for BareAuthPlugin and AuthUser model."""

from __future__ import annotations

import pytest

from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.spi.auth import AuthError, AuthUser

# ── AuthUser model ───────────────────────────────────────────────────────────


class TestAuthUser:
    def test_basic_construction(self) -> None:
        user = AuthUser(
            id="u1",
            operatorName="op1",
            staffId="s001",
        )
        assert user.id == "u1"
        assert user.operatorName == "op1"
        assert user.staffId == "s001"

    def test_staffId_alias_populates_outUserNo(self) -> None:
        user = AuthUser(id="u1", operatorName="op1", staffId="s100")
        assert user.outUserNo == "s100"
        assert user.staffId == "s100"

    def test_outUserNo_direct_field(self) -> None:
        user = AuthUser(id="u1", operatorName="op1", outUserNo="d100")
        assert user.staffId == "d100"
        assert user.outUserNo == "d100"

    def test_tenantId_alias(self) -> None:
        user = AuthUser(
            id="u1",
            operatorName="op1",
            staffId="s001",
            tenantId="t100",
        )
        assert user.tntInstId == "t100"
        assert user.tenantId == "t100"

    def test_tntInstId_direct_field(self) -> None:
        user = AuthUser(
            id="u1",
            operatorName="op1",
            staffId="s001",
            tntInstId="direct-tenant",
        )
        assert user.tenantId == "direct-tenant"

    def test_optional_fields_default_none(self) -> None:
        user = AuthUser(id="u1", operatorName="op1", staffId="s001")
        assert user.mobileNumber is None
        assert user.nickName is None
        assert user.realName is None
        assert user.tntInstId is None
        assert user.tenantId is None

    def test_all_fields(self) -> None:
        user = AuthUser(
            id="u1",
            mobileNumber="13800138000",
            nickName="花名",
            operatorName="domain_admin",
            staffId="s001",
            realName="张三",
            tenantId="tenant-a",
        )
        assert user.mobileNumber == "13800138000"
        assert user.nickName == "花名"
        assert user.operatorName == "domain_admin"
        assert user.realName == "张三"

    def test_json_roundtrip(self) -> None:
        user = AuthUser(
            id="u1",
            operatorName="op1",
            staffId="s001",
            tenantId="t1",
        )
        data = user.model_dump(by_alias=True)
        assert data["staffId"] == "s001"
        assert data["tenantId"] == "t1"
        restored = AuthUser.model_validate(data)
        assert restored.staffId == "s001"
        assert restored.tenantId == "t1"

    def test_populate_by_name(self) -> None:
        """Both alias and field name should work for population."""
        user = AuthUser(
            id="u1",
            operatorName="op1",
            outUserNo="by-field",
        )
        assert user.staffId == "by-field"


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
        assert user.operatorName == "bare_operator"
        assert user.staffId == "000001"
        assert user.nickName == "BareUser"
        assert user.realName == "Bare User"

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
        custom = AuthUser(
            id="custom-001",
            operatorName="custom_op",
            staffId="custom-staff",
        )
        plugin = BareAuthPlugin(default_user=custom)
        user = await plugin.get_login_user()
        assert user.id == "custom-001"
        assert user.operatorName == "custom_op"

    def test_is_allowed_always_true(self) -> None:
        plugin = BareAuthPlugin()
        user = AuthUser(id="any", operatorName="any", staffId="s")
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
        p2 = BareAuthPlugin(
            default_user=AuthUser(id="other", operatorName="op", staffId="s")
        )
        u1 = await p1.get_login_user()
        u2 = await p2.get_login_user()
        assert u1.id == "bare-user-001"
        assert u2.id == "other"

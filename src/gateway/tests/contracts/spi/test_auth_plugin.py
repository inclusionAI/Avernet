import pytest

from gateway.community.plugins.auth.bare._plugin import BareAuthPlugin
from gateway.community.spi.auth import AuthPlugin, AuthUser


class AuthPluginContract:
    plugin: AuthPlugin

    @pytest.mark.asyncio
    async def test_get_login_user_returns_auth_user(self) -> None:
        user = await self.plugin.get_login_user()
        assert isinstance(user, AuthUser)
        assert user.id

    @pytest.mark.asyncio
    async def test_get_login_user_with_cookie(self) -> None:
        user = await self.plugin.get_login_user(cookie="test-cookie")
        assert isinstance(user, AuthUser)

    def test_is_allowed_returns_bool(self) -> None:
        user = AuthUser(id="u1", operatorName="op1", staffId="001")
        result = self.plugin.is_allowed(user)
        assert isinstance(result, bool)

    def test_check_permission_returns_bool(self) -> None:
        result = self.plugin.check_permission("001", "perm_a,perm_b")
        assert isinstance(result, bool)

    def test_check_permission_with_context(self) -> None:
        result = self.plugin.check_permission(
            "001",
            "perm_a",
            request_url="/api/test",
            request_map="test_map",
        )
        assert isinstance(result, bool)


class TestBareAuthPlugin(AuthPluginContract):
    def setup_method(self) -> None:
        self.plugin = BareAuthPlugin()

    def test_default_user_has_expected_fields(self) -> None:
        user = self.plugin._default_user
        assert user.id == "bare-user-001"
        assert user.operatorName == "bare_operator"
        assert user.staffId == "000001"

    @pytest.mark.asyncio
    async def test_is_allowed_always_true(self) -> None:
        user = AuthUser(id="any", operatorName="any", staffId="999")
        assert self.plugin.is_allowed(user) is True

    def test_check_permission_always_true(self) -> None:
        assert self.plugin.check_permission("any_user", "any_permission") is True

    @pytest.mark.asyncio
    async def test_custom_default_user(self) -> None:
        custom_user = AuthUser(id="custom", operatorName="custom", staffId="123")
        plugin = BareAuthPlugin(default_user=custom_user)
        user = await plugin.get_login_user()
        assert user.id == "custom"

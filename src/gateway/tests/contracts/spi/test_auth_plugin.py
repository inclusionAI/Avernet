import pytest

from gateway.community.plugins.auth.stub._plugin import StubAuthPlugin
from gateway.community.spi.auth import AuthenticatedUser, AuthPlugin


class AuthPluginContract:
    plugin: AuthPlugin

    @pytest.mark.asyncio
    async def test_get_login_user_returns_auth_user(self) -> None:
        user = await self.plugin.get_login_user()
        assert isinstance(user, AuthenticatedUser)
        assert user.id

    @pytest.mark.asyncio
    async def test_get_login_user_with_cookie(self) -> None:
        user = await self.plugin.get_login_user(cookie="test-cookie")
        assert isinstance(user, AuthenticatedUser)

    def test_is_allowed_returns_bool(self) -> None:
        user = AuthenticatedUser(id="u1", username="op1")
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


class TestStubAuthPlugin(AuthPluginContract):
    def setup_method(self) -> None:
        self.plugin = StubAuthPlugin()

    def test_default_user_has_expected_fields(self) -> None:
        user = self.plugin._default_user
        assert user.id == "bare-user-001"
        assert user.username == "bare_operator"
        assert user.display_name == "Bare User"

    @pytest.mark.asyncio
    async def test_is_allowed_always_true(self) -> None:
        user = AuthenticatedUser(id="any", username="any")
        assert self.plugin.is_allowed(user) is True

    def test_check_permission_always_true(self) -> None:
        assert self.plugin.check_permission("any_user", "any_permission") is True

    @pytest.mark.asyncio
    async def test_custom_default_user(self) -> None:
        custom_user = AuthenticatedUser(id="custom", username="custom")
        plugin = StubAuthPlugin(default_user=custom_user)
        user = await plugin.get_login_user()
        assert user.id == "custom"

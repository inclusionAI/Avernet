"""Unit tests for plugins/auth/stub/_stub_auth.py — StubAuthPlugin."""

import pytest

from secbaas.plugins.auth.stub import StubAuthPlugin
from secbaas.spi.auth import AuthUser


class TestStubAuthPlugin:
    def setup_method(self):
        self.plugin = StubAuthPlugin()

    @pytest.mark.asyncio
    async def test_get_login_user_returns_default_user(self):
        user = await self.plugin.get_login_user()
        assert isinstance(user, AuthUser)
        assert user.staffId == "000001"
        assert user.nickName == "StubUser"
        assert user.operatorName == "stub_operator"

    @pytest.mark.asyncio
    async def test_get_login_user_returns_custom_user(self):
        custom_user = AuthUser(id="custom", operatorName="cust", staffId="999")
        plugin = StubAuthPlugin(default_user=custom_user)
        user = await plugin.get_login_user()
        assert user.staffId == "999"

    @pytest.mark.asyncio
    async def test_get_login_user_ignores_cookie_and_referer(self):
        user = await self.plugin.get_login_user(cookie="any", referer="any")
        assert user.staffId == "000001"

    def test_is_allowed_always_returns_true(self):
        user = AuthUser(id="u", operatorName="unknown", staffId="1")
        assert self.plugin.is_allowed(user) is True

    def test_check_permission_always_returns_true(self):
        assert self.plugin.check_permission("any", "any") is True

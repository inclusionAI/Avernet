"""Unit tests for core/service/auth_service/_auth_service.py — AuthService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.service.auth_service import AuthService
from secbaas.community.spi.auth import AuthError, AuthPlugin, AuthUser


class TestAuthService:
    def setup_method(self):
        self._mock_plugin = MagicMock(spec=AuthPlugin)
        self._mock_plugin.get_login_user = AsyncMock(
            return_value=AuthUser(id="u1", operatorName="op1", staffId="001")
        )
        self._mock_plugin.check_permission = MagicMock(return_value=True)
        self._mock_plugin.is_allowed = MagicMock(return_value=True)
        self.service = AuthService(plugin=self._mock_plugin)

    def test_raises_on_none_plugin(self):
        with pytest.raises(ValueError, match="plugin is required"):
            AuthService(plugin=None)

    @pytest.mark.asyncio
    async def test_authenticate_request_returns_auth_user(self):
        result = await self.service.authenticate_request(
            cookie="s=1", referer="https://x.com/"
        )
        self._mock_plugin.get_login_user.assert_called_once_with(
            cookie="s=1", referer="https://x.com/"
        )
        assert result.staffId == "001"

    @pytest.mark.asyncio
    async def test_authenticate_request_raises_auth_error_on_failure(self):
        self._mock_plugin.get_login_user = AsyncMock(
            side_effect=RuntimeError("API down")
        )
        svc = AuthService(plugin=self._mock_plugin)
        with pytest.raises(AuthError, match="Authentication failed"):
            await svc.authenticate_request(cookie="bad")

    def test_check_user_permission_delegates_to_plugin(self):
        user = AuthUser(id="u", operatorName="op", staffId="001")
        result = self.service.check_user_permission(user, "admin")

        self._mock_plugin.check_permission.assert_called_once_with(
            user_id="001", permission_codes="admin"
        )
        assert result is True

    def test_is_operator_delegates_to_plugin(self):
        user = AuthUser(id="u", operatorName="op", staffId="001")
        result = self.service.is_operator(user)

        self._mock_plugin.is_allowed.assert_called_once_with(user)
        assert result is True

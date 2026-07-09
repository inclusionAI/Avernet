"""Unit tests for plugins/auth/oauth/_plugin.py."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.plugins.auth.oauth import OAuthPlugin
from secbaas.spi.auth import AuthUser


class TestOAuthPlugin:
    @pytest.mark.asyncio
    async def test_get_login_user_delegates_to_client(self):
        mock_client = MagicMock()
        expected = AuthUser(id="u-1", operatorName="u-1", staffId="u-1", nickName="A")
        mock_client.get_login_user = AsyncMock(return_value=expected)
        plugin = OAuthPlugin(oauth_client=mock_client)

        user = await plugin.get_login_user(cookie="bcs_session=jwt", referer="r")

        assert user is expected
        mock_client.get_login_user.assert_awaited_once_with(
            cookie="bcs_session=jwt", referer="r"
        )

    def test_is_allowed_always_true(self):
        plugin = OAuthPlugin(oauth_client=MagicMock())
        user = AuthUser(id="u-1", operatorName="u-1", staffId="u-1")
        assert plugin.is_allowed(user) is True

    def test_check_permission_always_true(self):
        plugin = OAuthPlugin(oauth_client=MagicMock())
        assert plugin.check_permission("u-1", "admin") is True
        assert (
            plugin.check_permission(
                "u-1", "admin", request_url="/api", request_map="{}"
            )
            is True
        )

    def test_default_client_constructed_when_none(self, monkeypatch):
        # Avoid touching real config: stub ConfigLoader.load for OAuthClient.
        import secbaas.plugins.auth.oauth._oauth_client as client_mod

        fake_cfg = MagicMock()
        fake_cfg.user_config = {}
        monkeypatch.setattr(client_mod.ConfigLoader, "load", lambda: fake_cfg)

        plugin = OAuthPlugin()
        assert plugin._oauth_client is not None

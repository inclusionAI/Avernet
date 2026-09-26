"""Identity concern — test / singlebox binding (LOCAL stubs)."""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth import AuthPlugin
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.token_exchange import TokenExchangePlugin


logger = get_logger()


class TestIdentityModule(Module):
    """test / singlebox: LOCAL stubs (trust header/cookie, mock tokens)."""

    @singleton
    @provider
    def auth(self) -> AuthPlugin:
        from agentclaw.community.plugins.local.auth import LocalAuth

        logger.info("AuthPlugin: LocalAuth (test)")
        return LocalAuth()

    @singleton
    @provider
    def passport(self) -> PassportPlugin:
        from agentclaw.community.plugins.local.passport import LocalPassportPlugin

        return LocalPassportPlugin()

    @singleton
    @provider
    def auth_relationship(self) -> AuthRelationshipPlugin:
        from agentclaw.community.plugins.local.auth_relationship import (
            LocalAuthRelationshipPlugin,
        )

        logger.info("AuthRelationshipPlugin: LocalAuthRelationshipPlugin (test)")
        return LocalAuthRelationshipPlugin()

    @singleton
    @provider
    def token_exchange(self) -> TokenExchangePlugin:
        from agentclaw.community.plugins.local.token_exchange import (
            LocalTokenExchangePlugin,
        )

        logger.info("TokenExchangePlugin: LocalTokenExchangePlugin (test)")
        return LocalTokenExchangePlugin()

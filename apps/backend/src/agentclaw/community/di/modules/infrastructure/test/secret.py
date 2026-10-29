"""Secret concern — test/singlebox binding with explicit local overrides."""
from __future__ import annotations

import os

from injector import Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.secret_resolver import SecretResolver


logger = get_logger()


class TestSecretModule(Module):
    """test / singlebox: local stub (returns ``None``)."""

    @singleton
    @provider
    def secret_resolver(self) -> SecretResolver:
        from agentclaw.community.plugins.local.secret_resolver import LocalSecretResolver

        logger.info("SecretResolver: LocalSecretResolver (test)")
        return LocalSecretResolver(
            gateway_principal_signing_key=os.environ.get(
                "AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE", ""
            )
        )

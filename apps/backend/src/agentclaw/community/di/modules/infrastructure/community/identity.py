"""Identity concern — community binding.

Capability: auth family (Auth / Passport / AuthRelationship / TokenExchange).
B4 binds the community identity family to real community implementations.
Imports only ``plugins.community`` (never ``plugins.prod``) so the community
column stays import-disjoint from corp.
"""
from __future__ import annotations

from injector import Binder, Module, provider, singleton

from agentclaw.community.di import config_community as cfg
from agentclaw.community.plugin_api.auth import AuthPlugin
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.token_exchange import TokenExchangePlugin


class CommunityIdentityModule(Module):
    """community: BCS unified auth + self-issued passport + no-op relationship + passthrough."""

    @singleton
    @provider
    def bcs_auth_config(self) -> cfg.BcsAuthConfig:
        """BCS unified-auth config — community-only (corp/test never resolve it).

        Reads the ``bcs`` user_config block (from application-community.yaml);
        falls back to dataclass defaults when absent. ``operator_subjects``
        arrives as a YAML list and is frozen here. Reuses ``config_module._block``
        so the single sofa ``user_config`` reader stays in one place.
        """
        from agentclaw.community.di.modules.config_module import _block

        block = _block("bcs")
        defaults = cfg.BcsAuthConfig()
        operators = block.get("operator_subjects") or []
        return cfg.BcsAuthConfig(
            base_url=block.get("base_url", defaults.base_url),
            user_path=block.get("user_path", defaults.user_path),
            timeout=block.get("timeout", defaults.timeout),
            operator_subjects=frozenset(operators),
        )

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.auth import OidcAuthPlugin
        from agentclaw.community.plugins.community.auth_relationship import (
            CommunityAuthRelationshipPlugin,
        )
        from agentclaw.community.plugins.community.passport import (
            SelfIssuedPassportPlugin,
        )
        from agentclaw.community.plugins.community.token_exchange import (
            PassthroughTokenExchangePlugin,
        )

        # ``OidcAuthPlugin.__init__`` is ``@inject``-decorated and now takes
        # ``BcsAuthConfig`` (provided above), so ``bind`` can construct it.
        binder.bind(AuthPlugin, to=OidcAuthPlugin, scope=singleton)
        binder.bind(
            PassportPlugin, to=SelfIssuedPassportPlugin, scope=singleton
        )
        binder.bind(
            AuthRelationshipPlugin,
            to=CommunityAuthRelationshipPlugin,
            scope=singleton,
        )
        binder.bind(
            TokenExchangePlugin,
            to=PassthroughTokenExchangePlugin,
            scope=singleton,
        )

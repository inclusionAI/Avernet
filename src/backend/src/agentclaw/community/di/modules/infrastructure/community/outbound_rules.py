"""Outbound-rule concern — community binding (no egress mutation)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.outbound_rules import OutboundRuleProvider


class CommunityOutboundRulesModule(Module):
    """community: CommunityOutboundRuleProvider (empty rule, no egress mutation)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.outbound_rules import (
            CommunityOutboundRuleProvider,
        )

        binder.bind(
            OutboundRuleProvider,
            to=CommunityOutboundRuleProvider,
            scope=singleton,
        )

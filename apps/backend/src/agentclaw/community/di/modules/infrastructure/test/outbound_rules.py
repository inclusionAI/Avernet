"""Outbound-rule concern — test / singlebox binding.

Binds the **prod** ``ProdOutboundRuleProvider`` (not a Noop double): the builder
is pure logic with no external service to stub, and with the test column's
``LocalSecretResolver`` (every secret → ``None``) it degrades exactly like the
pre-seam singlebox path did — empty/placeholder header values, BaaS
LocalPaasService ignores them. This preserves the singlebox behavior the
existing tests assert and exercises the real builder. (Mirrors the ModelAPI
precedent where the corp impl is reused in the test column for want of a
meaningful double.) The community column instead binds an empty-rule impl.
"""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.outbound_rules import OutboundRuleProvider


class TestOutboundRulesModule(Module):
    """test / singlebox: ProdOutboundRuleProvider degraded by LocalSecretResolver."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.corp.plugins.prod.outbound_rules import ProdOutboundRuleProvider

        binder.bind(
            OutboundRuleProvider,
            to=ProdOutboundRuleProvider,
            scope=singleton,
        )

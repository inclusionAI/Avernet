"""Outbound-rule concern — community binding.

Binds the ``CommunityOutboundRuleProvider``, which builds its
``OutBoundOperationRule`` from the ``outbound_rules`` block of
``user_config``. An empty ``header_rules`` list (the default) means no egress
mutation — the same behavior as before this was configurable.
"""
from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.di import config_community as cfg
from agentclaw.community.plugin_api.outbound_rules import OutboundRuleProvider


class CommunityOutboundRulesModule(Module):
    """community: CommunityOutboundRuleProvider (config-driven egress rules)."""

    @singleton
    @provider
    def outbound_rules_config(self) -> cfg.OutboundRulesConfig:
        from agentclaw.community.di.modules.config_module import _block

        block = _block("outbound_rules")
        raw_rules = block.get("header_rules") or []

        entries: list[cfg.OutboundRuleEntryConfig] = []
        for item in raw_rules:
            if not isinstance(item, dict):
                continue
            entries.append(
                cfg.OutboundRuleEntryConfig(
                    domains=tuple(item.get("domains") or ()),
                    action=item.get("action", "set"),
                    header_name=item.get("header_name", ""),
                    value=item.get("value", ""),
                    placeholder=item.get("placeholder"),
                    separator=item.get("separator"),
                )
            )
        return cfg.OutboundRulesConfig(header_rules=tuple(entries))

    @singleton
    @provider
    @inject
    def outbound_rule_provider(
        self, config: cfg.OutboundRulesConfig
    ) -> OutboundRuleProvider:
        from agentclaw.community.plugins.community.outbound_rules import (
            CommunityOutboundRuleProvider,
        )

        return CommunityOutboundRuleProvider(config)

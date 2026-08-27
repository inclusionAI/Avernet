"""Community ``OutboundRuleProvider`` — config-driven egress-header rules.

A real, deployable impl for the open-source build: outbound-header rules are
loaded from the ``outbound_rules`` block of ``user_config`` at construction
time and returned verbatim on every ``build_rule`` call. An empty rule list
(the default when no ``outbound_rules`` block is configured) means no egress
mutation — the same behavior as before this was configurable.

Depends on no corp secret/domain machinery. Not a ``MockSeam`` subclass — this
is a real impl bound directly by ``CommunityOutboundRulesModule``.
"""

from __future__ import annotations

from typing import Callable

from agentclaw.community.di.config_community import OutboundRulesConfig
from agentclaw.community.kernel.device_dto import (
    HeaderOperationRule,
    OutBoundOperationRule,
)
from agentclaw.community.plugin_api.outbound_rules import OutboundRuleProvider


class CommunityOutboundRuleProvider(OutboundRuleProvider):
    """Config-driven egress-header rules loaded at construction time."""

    def __init__(self, config: OutboundRulesConfig) -> None:
        self._rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=list(entry.domains),
                    action=entry.action,
                    header_name=entry.header_name,
                    value=entry.value,
                    placeholder=entry.placeholder,
                    separator=entry.separator,
                )
                for entry in config.header_rules
            ]
        )

    def build_rule(
        self,
        *,
        bolt_id: str = "",
        device_id: str = "",
        owner_id: str = "",
        agent_pass_token: str = "",
        agent_code: str = "",
        bot_type_resolver: Callable[[str, str], str | None] | None = None,
        extra_properties: dict[str, object] | None = None,
    ) -> OutBoundOperationRule:
        return self._rule

    def build_agentpass_rule(
        self,
        *,
        agent_pass_token: str = "",
    ) -> OutBoundOperationRule | None:
        return None

    def build_caller_rule(
        self,
        *,
        caller_token: str,
    ) -> OutBoundOperationRule | None:
        return None

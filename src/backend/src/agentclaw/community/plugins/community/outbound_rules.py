"""Community ``OutboundRuleProvider`` — no egress mutation.

A real, deployable impl for the open-source build: a community runtime has no
AntGroup gateways to route to and no Mist secrets to inject, so it applies no
outbound-header rules (an empty rule the device runtime treats as a no-op).

Depends on no corp secret/domain machinery. Not a ``MockSeam`` subclass — this
is a real impl bound directly by ``CommunityOutboundRulesModule``.
"""
from __future__ import annotations

from typing import Callable

from agentclaw.community.kernel.device_dto import OutBoundOperationRule
from agentclaw.community.plugin_api.outbound_rules import OutboundRuleProvider


class CommunityOutboundRuleProvider(OutboundRuleProvider):
    """No egress mutation: every rule is empty."""

    def build_rule(
        self,
        *,
        bolt_id: str = "",
        device_id: str = "",
        owner_id: str = "",
        agent_pass_token: str = "",
        agent_code: str = "",
        bot_type_resolver: "Callable[[str, str], str | None] | None" = None,
    ) -> OutBoundOperationRule:
        return OutBoundOperationRule()

    def build_agentpass_rule(
        self,
        *,
        agent_pass_token: str = "",
    ) -> "OutBoundOperationRule | None":
        return None

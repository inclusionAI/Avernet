"""OutboundRuleProvider — bot egress-header rule construction.

A bot device/sandbox can have outbound-header rules applied to its egress
traffic (inject auth tokens, route headers to internal gateways, etc.). The
*content* of those rules is environment-specific: the corp runtime injects
AntGroup gateway domains + secrets, while a community runtime injects nothing.

This Protocol moves rule construction behind a capability so the neutral layers
(``core``/``api``) depend on the Protocol and the kernel ``OutBoundOperationRule``
DTO instead of on any vendor domain list or secret backend. The vendor-specific
builder body lives only in ``plugins.prod``.

Each impl carries its own policy:
- ``plugins.prod.outbound_rules`` — the corp builder (gateway domains + secrets).
- ``plugins.community.outbound_rules.CommunityOutboundRuleProvider`` — no egress
  mutation (empty rule).
- ``plugins.local.outbound_rules.NoopOutboundRuleProvider`` — no egress mutation.
"""
from __future__ import annotations

from typing import Callable, Protocol

from agentclaw.community.kernel.device_dto import OutBoundOperationRule
from agentclaw.community.plugin_api.base import Plugin


class OutboundRuleProvider(Plugin, Protocol):
    """Builds the outbound-header rule set for a bot device/sandbox."""

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
        """The full outbound-header rule set for a bot.

        ``bot_type_resolver(bolt_id, owner_id) -> bot_type | None`` lets the impl
        pick a bot-type-specific policy; impls that need no such split ignore it.
        Returns an empty rule where the runtime applies no egress mutation.
        """
        ...

    def build_agentpass_rule(
        self,
        *,
        agent_pass_token: str = "",
    ) -> "OutBoundOperationRule | None":
        """An identity-authorization-only outbound rule (the teclaw path), or
        ``None`` when no token / no egress mutation applies."""
        ...

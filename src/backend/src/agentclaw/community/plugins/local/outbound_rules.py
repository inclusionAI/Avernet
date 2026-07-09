"""Local ``OutboundRuleProvider`` — no egress mutation offline.

``build_rule`` returns an empty rule and ``build_agentpass_rule`` returns
``None`` ⇒ no outbound-header mutation, matching the pre-seam local behavior
(``LocalSecretResolver`` returned ``None`` so every rule degraded to empty).
"""
from __future__ import annotations

from typing import Callable

from agentclaw.community.kernel.device_dto import OutBoundOperationRule
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.outbound_rules import OutboundRuleProvider
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.NOOP, rationale="no egress mutation offline")
class NoopOutboundRuleProvider(MockSeam, OutboundRuleProvider):
    """Test/offline double: no outbound-header mutation."""

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

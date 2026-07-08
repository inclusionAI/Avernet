"""Rule 25 conformance — OutboundRuleProvider.

Consumer under test: the BaaS create-bot path's outbound-rule serialization —
it takes the provider's ``OutBoundOperationRule`` and emits the
``{"header_operation_rules": [...]}`` JSON the BaaS endpoint expects. We exercise
that consumer against the deployable impls and assert the Protocol contract holds
end-to-end:

- community ``CommunityOutboundRuleProvider`` → empty rule / no agentpass rule
  (a community runtime injects no AntGroup egress headers).
- ``NoopOutboundRuleProvider`` → empty rule.

B11 (3.2): the ``world``-resolved half (the corp ``ProdOutboundRuleProvider``
serializing to the BaaS wire shape) moved to
``tests/corp/contracts/test_outbound_rules.py`` — the corp-free ``test`` column now
binds the community empty-rule provider, so that assertion is corp-resident.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.kernel.device_dto import OutBoundOperationRule
from agentclaw.community.plugins.community.outbound_rules import CommunityOutboundRuleProvider
from agentclaw.community.plugins.local.outbound_rules import NoopOutboundRuleProvider


def _serialize(rule: OutBoundOperationRule) -> dict[str, Any]:
    """Mirror the BaaS create-bot payload's outbound-rule serialization."""
    return {
        "header_operation_rules": [
            {
                "domains": r.domains,
                "action": r.action,
                "header_name": r.header_name,
                "value": r.value,
                "placeholder": r.placeholder,
            }
            for r in rule.header_operation_rules
        ]
    }


def test_community_provider_yields_no_egress_mutation():
    provider = CommunityOutboundRuleProvider()
    assert _serialize(provider.build_rule(bolt_id="b", owner_id="o")) == {
        "header_operation_rules": []
    }
    assert provider.build_agentpass_rule(agent_pass_token="tok") is None


def test_local_provider_yields_no_egress_mutation():
    provider = NoopOutboundRuleProvider()
    assert provider.build_rule(bolt_id="b", owner_id="o").header_operation_rules == ()
    assert provider.build_agentpass_rule(agent_pass_token="tok") is None

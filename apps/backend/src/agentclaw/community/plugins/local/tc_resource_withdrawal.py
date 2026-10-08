"""Explicit test fake. Never selected by deployable production/local profiles."""

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.tc_resource_withdrawal import (
    ResourceWithdrawalEvent,
    ResourceWithdrawalPublisherPlugin,
    WithdrawalReceipt,
)
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="test-only durable intake simulation, not real ECB",
)
class LocalResourceWithdrawalPublisher(MockSeam, ResourceWithdrawalPublisherPlugin):
    def publish(self, event: ResourceWithdrawalEvent) -> WithdrawalReceipt:
        return WithdrawalReceipt(event_id=event.event_id, status="pending")

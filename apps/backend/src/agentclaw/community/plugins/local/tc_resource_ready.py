"""Recording local implementation of the TC resource-ready Plugin API."""

from __future__ import annotations

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.tc_resource_ready import (
    TcResourceReadyEvent,
    TcResourceReadyPublisherPlugin,
)
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="records resource-ready events without calling an external ECB",
)
class LocalTcResourceReadyPublisher(MockSeam, TcResourceReadyPublisherPlugin):
    async def publish(self, event: TcResourceReadyEvent) -> None:
        return None

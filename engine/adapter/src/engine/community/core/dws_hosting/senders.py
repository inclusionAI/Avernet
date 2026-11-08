"""Route immutable delivery purposes through explicitly injected senders."""

from engine.community.kernel.dws_delivery import Delivery, DeliveryReceipt
from engine.community.plugin_api.dws_delivery import HostingSender


class HostingDeliveryRouter:
    def __init__(self, replies: HostingSender, owner_notices: HostingSender):
        self.replies, self.owner_notices = replies, owner_notices

    def _sender(self, delivery: Delivery) -> HostingSender:
        return self.replies if delivery.purpose == "reply" else self.owner_notices

    async def preflight(self, delivery: Delivery) -> None:
        await self._sender(delivery).preflight(delivery)

    async def send(self, delivery: Delivery) -> DeliveryReceipt:
        return await self._sender(delivery).send(delivery)

    async def inspect(self, delivery: Delivery, task_id: str) -> DeliveryReceipt:
        return await self._sender(delivery).inspect(delivery, task_id)

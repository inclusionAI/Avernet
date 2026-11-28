"""Control-plane-only platform delivery Plugin API; never exposed to a Worker."""

from typing import Any, Protocol

from avernet_genome.models import BotRef


class ManualDelivery(Protocol):
    """Platform Plugin API. Only trusted control requests may use this port.

    apply is idempotent by immutable authorization ID, including lost replies.
    get returns None only for an authoritative 404. Other failures propagate.
    Neither operation is exposed through the strategy SDK or Job dispatch.
    """

    async def apply(self, bot: BotRef, authorization: dict[str, Any]) -> dict[str, Any]: ...
    async def get(self, bot: BotRef, authorization_id: str) -> dict[str, Any] | None: ...

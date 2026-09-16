"""Service API for mutating current published runtime ignore rules."""

from typing import Any, Protocol
from agentclaw.community.kernel.publish_ignore import (
    PublishIgnoreCommand,
    PublishIgnoreError,
)

__all__ = ["PublishIgnoreCommand", "PublishIgnoreError", "PublishIgnoreServiceProtocol"]


class PublishIgnoreServiceProtocol(Protocol):
    async def change(
        self,
        command: PublishIgnoreCommand,
        operator_id: str,
        *,
        is_admin: bool,
    ) -> dict[str, Any]: ...

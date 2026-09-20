"""Service API for mutating current published runtime ignore rules."""

from typing import Any, Protocol
from agentclaw.community.kernel.publish_ignore import (
    PublishIgnoreCommand,
    PublishIgnoreError,
    PublishIgnoreQuery,
)

__all__ = ["PublishIgnoreCommand", "PublishIgnoreError", "PublishIgnoreQuery", "PublishIgnoreServiceProtocol"]


class PublishIgnoreServiceProtocol(Protocol):
    async def query(
        self, query: PublishIgnoreQuery, operator_id: str, *, is_admin: bool,
    ) -> dict[str, Any]: ...

    async def change(
        self,
        command: PublishIgnoreCommand,
        operator_id: str,
        *,
        is_admin: bool,
    ) -> dict[str, Any]: ...

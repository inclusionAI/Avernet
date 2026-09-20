"""Runtime mutation plugin contract: current instance snapshots, no file access."""

from typing import Any, Protocol, runtime_checkable
from agentclaw.community.kernel.publish_ignore import (
    PublishIgnoreCommand,
    PublishIgnoreBinding,
    PublishIgnoreQuery,
)


@runtime_checkable
class PublishIgnoreRuntime(Protocol):
    async def query(
        self, binding: PublishIgnoreBinding, target: str,
        query: PublishIgnoreQuery, operator_id: str,
    ) -> dict[str, Any]: ...

    async def targets(self, binding: PublishIgnoreBinding) -> list[str]: ...

    async def change(
        self,
        binding: PublishIgnoreBinding,
        target: str,
        command: PublishIgnoreCommand,
        operator_id: str,
    ) -> dict[str, Any]: ...

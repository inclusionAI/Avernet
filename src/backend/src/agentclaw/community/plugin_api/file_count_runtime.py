"""Runtime boundary for fixed-instance file counts."""
from typing import Any, Protocol, runtime_checkable
from agentclaw.community.kernel.file_count import FileCountBinding, FileCountQuery


@runtime_checkable
class FileCountRuntime(Protocol):
    async def targets(self, binding: FileCountBinding) -> list[str]: ...

    async def query(
        self, binding: FileCountBinding, target: str,
        query: FileCountQuery, operator_id: str,
    ) -> dict[str, Any]: ...

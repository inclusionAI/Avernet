"""Owning contract for authorized current-instance directory counting."""
from typing import Any, Protocol, runtime_checkable
from agentclaw.community.kernel.file_count import FileCountQuery


@runtime_checkable
class FileCountServiceProtocol(Protocol):
    async def query(
        self, query: FileCountQuery, operator_id: str, *, is_admin: bool,
    ) -> dict[str, Any]: ...

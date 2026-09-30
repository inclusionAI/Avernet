"""Service API for next-build rules, independent of running instances."""

from typing import Any, Protocol, runtime_checkable
from agentclaw.community.kernel.build_ignore import BuildIgnoreCommand, BuildIgnoreQuery


@runtime_checkable
class BuildIgnoreServiceProtocol(Protocol):
    async def query(
        self, query: BuildIgnoreQuery, operator_id: str, *, is_admin: bool
    ) -> dict[str, Any]: ...

    async def change(
        self, command: BuildIgnoreCommand, operator_id: str, *, is_admin: bool
    ) -> dict[str, Any]: ...

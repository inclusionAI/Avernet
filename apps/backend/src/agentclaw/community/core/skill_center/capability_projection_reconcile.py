"""Service boundary shared by Manifest Apply and capability recovery."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Protocol

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    RuntimeProjectionResult,
)


CapabilityWriteScope = Callable[[], AbstractContextManager[None]]


class CapabilityProjectionReconcileProtocol(Protocol):
    @property
    def defer_enabled(self) -> bool: ...

    def deferred_apply(
        self, *, bot_id: str, entity_id: str, owner_id: str
    ) -> AbstractAsyncContextManager[CapabilityWriteScope]: ...

    async def finish_apply(
        self, *, bot_id: str, entity_id: str, initialize: bool
    ) -> tuple[str | None, RuntimeProjectionResult]: ...

    async def reconcile(
        self, *, bot_id: str, entity_id: str
    ) -> RuntimeProjectionResult: ...

    def observe(
        self, *, bot_id: str, entity_id: str, revision: str
    ) -> RuntimeProjectionResult: ...

"""Service API and persistence ports for recoverable Owner handoff."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class HandoffError(RuntimeError):
    """Safe, non-secret code suitable for an operation or delivery response."""


@dataclass(frozen=True)
class OwnerHandoffPolicy:
    enabled: bool = False
    recovery_enabled: bool = False
    administrators: frozenset[str] = frozenset()


class OwnerHandoffRepositoryProtocol(Protocol):
    def begin(
        self,
        *,
        bot_pk: int,
        actor_id: str,
        new_owner_id: str,
        new_owner_name: str,
        request_key: str,
        administrators: frozenset[str],
    ) -> dict[str, Any]: ...
    def get(self, operation_id: str) -> dict[str, Any]: ...
    def claim(self, operation_id: str, worker: str) -> dict[str, Any] | None: ...
    def advance(
        self,
        operation_id: str,
        worker: str,
        phase: str,
        *,
        release: bool = True,
        **facts: Any,
    ) -> None: ...
    def finish(self, operation_id: str, worker: str) -> None: ...
    def cancel(
        self, operation_id: str, actor_id: str, administrators: frozenset[str]
    ) -> None: ...
    def recoverable(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class OwnerHandoffServiceProtocol(Protocol):
    def create(
        self, *, bot_pk: int, actor_id: str, new_owner_id: str, request_key: str
    ) -> dict[str, Any]: ...
    def get(self, *, operation_id: str, actor_id: str) -> dict[str, Any]: ...
    def retry(self, *, operation_id: str, actor_id: str) -> dict[str, Any]: ...
    def cancel(self, *, operation_id: str, actor_id: str) -> dict[str, Any]: ...
    def authorization(self, *, operation_id: str, actor_id: str) -> dict[str, Any]: ...
    def step(self, operation_id: str) -> bool: ...
    def reconcile(self) -> None: ...

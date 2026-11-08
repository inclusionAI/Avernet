"""Existing-table recycle journal and activation claims; no provider calls."""

from __future__ import annotations

from abc import abstractmethod
from contextlib import AbstractContextManager
from typing import Protocol


class BotRecycleRepositoryProtocol(Protocol):
    """Owner/tenant/env-scoped transitions, conditional on operation and phase.

    finish commits Bot, binding and audit together. Failed writes propagate.
    """

    @abstractmethod
    def read(self, bot_id: str, owner_id: str) -> dict: ...
    @abstractmethod
    def ensure(self, payload: dict, task_id: int) -> dict: ...
    @abstractmethod
    def update(self, payload: dict, phases: set | frozenset, **changes) -> dict: ...
    @abstractmethod
    def finish(self, payload: dict, finalizer: str) -> None: ...
    @abstractmethod
    def claim_activation(self, bot_id: str, owner_id: str) -> tuple[str, bool]: ...
    @abstractmethod
    def activation_rollback(
        self, bot_id: str, owner_id: str
    ) -> AbstractContextManager[bool]: ...

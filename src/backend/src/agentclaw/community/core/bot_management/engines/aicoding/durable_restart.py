"""Shared durable restart-task execution primitive.

Domains keep their own task type, payload and continuation.  This module owns
only the common queue contract: payload validation, sync/async continuation
execution and terminal error reporting.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Optional

from agentclaw.community.core.task_queue.types import Complete, Fail, TaskOutcome


class DurableRestartTaskHandler:
    """Run one restart continuation with the same durable-task semantics."""

    def __init__(
        self,
        *,
        task_type: str,
        required_fields: tuple[str, ...],
        continuation: Callable[[dict[str, Any]], Any],
    ) -> None:
        self._task_type = task_type
        self._required_fields = required_fields
        self._continuation = continuation

    @property
    def task_type(self) -> str:
        return self._task_type

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        if not isinstance(payload, dict) or not all(
            isinstance(payload.get(key), str) for key in self._required_fields
        ):
            return Fail(f"invalid restart task payload: task_type={self._task_type}")
        try:
            result = self._continuation(payload)
            if inspect.isawaitable(result):
                asyncio.run(result)
        except Exception as exc:
            # The domain continuation owns the original lifecycle and its
            # fail-closed semantics.  Do not invent a second retry path here.
            return Fail(f"restart failed: task_type={self._task_type}, error={type(exc).__name__}")
        return Complete()

"""Durable caller-container restart task."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from agentclaw.community.core.bot_management.engines.aicoding.durable_restart import DurableRestartTaskHandler
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.kernel.lifecycle import LifecycleBase

RESTART_TASK = "expert_chat.caller_restart"


def enqueue_caller_restart(queue: TaskQueueService, *, user_id: str, bot_id: str,
                           owner_id: str) -> None:
    queue.enqueue(
        RESTART_TASK,
        {"user_id": user_id, "bot_id": bot_id, "owner_id": owner_id},
        deadline_seconds=3600,
        idempotency_key=f"caller-restart:{user_id}:{bot_id}:{owner_id}",
    )


class CallerRestartTaskHandler(DurableRestartTaskHandler):
    def __init__(self, *, restart: Callable[..., Awaitable[dict[str, Any]]]):
        super().__init__(
            task_type=RESTART_TASK,
            required_fields=("user_id", "bot_id", "owner_id"),
            continuation=lambda payload: restart(
                user_id=payload["user_id"], bot_id=payload["bot_id"],
                owner_id=payload["owner_id"], force_upgrade=True,
            ),
        )


class CallerRestartTaskLifecycle(LifecycleBase):
    def __init__(self, *, registry: HandlerRegistry,
                 restart: Callable[..., Awaitable[dict[str, Any]]]):
        self._registry = registry
        self._restart = restart

    async def bootstrap(self) -> None:
        self._registry.register(
            CallerRestartTaskHandler(restart=self._restart),
            wake_on_enqueue=True,
        )

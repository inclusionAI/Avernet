"""Durable caller-container restart task."""
from __future__ import annotations

from typing import Callable, TYPE_CHECKING

from agentclaw.community.core.task_queue.durable_restart import DurableRestartTaskHandler
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.kernel.lifecycle import LifecycleBase

if TYPE_CHECKING:
    from agentclaw.community.core.expert_chat.services.expert_chat_instance_service import ExpertChatInstanceService

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
    def __init__(self, *, service_provider: Callable[[], "ExpertChatInstanceService"]):
        super().__init__(
            task_type=RESTART_TASK,
            required_fields=("user_id", "bot_id", "owner_id"),
            continuation=lambda payload: service_provider().get_caller_connection(
                user_id=payload["user_id"], bot_id=payload["bot_id"],
                owner_id=payload["owner_id"], force_upgrade=True,
                _durable_worker=True,
            ),
        )


class CallerRestartTaskLifecycle(LifecycleBase):
    def __init__(self, *, registry: HandlerRegistry,
                 service_provider: Callable[[], "ExpertChatInstanceService"]):
        self._registry = registry
        self._service_provider = service_provider

    async def bootstrap(self) -> None:
        self._registry.register(
            CallerRestartTaskHandler(service_provider=self._service_provider),
            wake_on_enqueue=True,
        )

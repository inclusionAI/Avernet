"""Durable ordinary-Bot restart task.

The HTTP restart adapter only submits this task for coding-engine Bots.  The
handler then re-enters the unchanged synchronous lifecycle, including the
fail-closed backup gate and the existing destroy/create hand-off.  This keeps
backup work out of the request without creating a second restart implementation.
"""
from __future__ import annotations

from typing import Callable, Optional, TYPE_CHECKING

from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.core.task_queue.types import Complete, Fail, TaskOutcome
from agentclaw.community.kernel.lifecycle import LifecycleBase

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.bot_service import BotService

RESTART_TASK = "bot_management.restart"
RESTART_TASK_DEADLINE_SECONDS = 3600


def enqueue_restart(task_queue_service: TaskQueueService, *, bot_id: str, user_id: str,
                    nick_name: str | None = None, extra_configs: dict | None = None) -> None:
    payload = {"bot_id": bot_id, "user_id": user_id}
    if nick_name is not None:
        payload["nick_name"] = nick_name
    if extra_configs is not None:
        payload["extra_configs"] = extra_configs
    task_queue_service.enqueue(
        RESTART_TASK, payload, deadline_seconds=RESTART_TASK_DEADLINE_SECONDS,
        idempotency_key=f"bot-restart:{bot_id}:{user_id}",
    )


class BotRestartTaskHandler:
    def __init__(self, *, bot_service_provider: Callable[[], "BotService"]) -> None:
        self._bot_service_provider = bot_service_provider

    @property
    def task_type(self) -> str:
        return RESTART_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        if not isinstance(payload, dict) or not isinstance(payload.get("bot_id"), str) \
                or not isinstance(payload.get("user_id"), str):
            return Fail("invalid bot restart task payload")
        try:
            self._bot_service_provider().restart_bot(
                bot_id=payload["bot_id"], user_id=payload["user_id"],
                nick_name=payload.get("nick_name"),
                extra_configs=payload.get("extra_configs"),
            )
        except Exception as exc:
            # The worker's normal exception path retries with its durable lease;
            # this explicit failure is reserved for deterministic lifecycle errors.
            return Fail(f"bot restart failed: {type(exc).__name__}")
        return Complete()


class BotRestartTaskLifecycle(LifecycleBase):
    def __init__(self, *, registry: HandlerRegistry,
                 bot_service_provider: Callable[[], "BotService"]) -> None:
        self._registry = registry
        self._bot_service_provider = bot_service_provider

    async def bootstrap(self) -> None:
        self._registry.register(
            BotRestartTaskHandler(bot_service_provider=self._bot_service_provider),
            wake_on_enqueue=True,
        )

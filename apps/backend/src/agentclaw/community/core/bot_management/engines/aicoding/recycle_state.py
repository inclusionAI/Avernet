"""Operation-scoped recycling state; never overload the Bot runtime status."""

from __future__ import annotations

from typing import Callable, Protocol

TASK_TYPE = "bot.recycle"
TASK_DEADLINE = 86400
RELEASE_TIMEOUT = 600
POLL_SECONDS = 3
RUNNING = frozenset({"QUEUED", "BACKING_UP", "RELEASING"})
BLOCKING = RUNNING | {"NEEDS_CONFIRMATION"}
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "NEEDS_CONFIRMATION"})


class RecycleSuperseded(RuntimeError):
    """This delivery no longer owns the operation/phase or captured target."""


class RecycleTargetChanged(RecycleSuperseded):
    """The captured target changed, not just a competing delivery."""


class RecycleUnavailable(RuntimeError):
    """No durable acceptance can be confirmed; callers may retry safely."""


def journal(bot: dict) -> dict:
    value = (bot.get("ext") or {}).get("recycle")
    return value if isinstance(value, dict) else {}


def assert_not_recycling(bot: dict) -> None:
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotInvalidLifecycleStateError,
    )

    record = journal(bot)
    if record.get("phase") in BLOCKING:
        raise BotInvalidLifecycleStateError(
            bot_id=bot["bot_id"],
            current_status="RECYCLE_" + record["phase"],
        )


class RecycleRuntime(Protocol):
    """Pinned-target backup/release capabilities assembled by the DI root."""

    def target(self, bot: dict) -> dict: ...
    def prepare(
        self, bot: dict, record: dict, save: Callable[..., dict]
    ) -> Callable[[], None] | None: ...
    def submit_release(self, target: dict, request_id: str, owner_id: str) -> dict: ...
    def observe_release(self, target: dict, release: dict | None) -> str: ...

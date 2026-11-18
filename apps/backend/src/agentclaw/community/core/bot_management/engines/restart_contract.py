"""Core-internal collaborators supplied to engine-owned lifecycle execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_dormant.bot_dormant_service_protocol import (
        BotDormantRecycleOperationProtocol,
    )
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.service_bot.repository.models import BotPublishRecord


@dataclass(frozen=True)
class RestartServices:
    repository: Any
    task_queue: Any
    get_bot: Callable[[str, str], dict]
    template_service: Any
    # Existing BotService instance, reused by the engine-owned restart executor.
    lifecycle: BotService


@dataclass(frozen=True)
class PublishedRestartServices:
    """Existing publish persistence supplied to an engine restart strategy.

    The strategy owns its metadata and failure policy. Shared orchestration
    supplies read, atomic ext mutation and status/ext CAS operations only.
    """

    get_record: Callable[[int], BotPublishRecord | None]
    mutate_ext: Callable[..., dict]
    update_status: Callable[..., None]


@dataclass(frozen=True)
class RestartAdmission:
    """Engine-owned admission; default engines add no task or response fields."""

    operation_id: str | None = None
    ext: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecycleServices:
    # Resolve the shared operation service lazily at the facade boundary.
    operations: Callable[[], BotDormantRecycleOperationProtocol]


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
    ext = bot.get("ext")
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except json.JSONDecodeError:
            ext = None
    value = ext.get("recycle") if isinstance(ext, dict) else None
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
    def observe_release(
        self, target: dict, release: dict | None, physical_targets: list[str] | None
    ) -> str: ...


class RecycleBackupBlocked(RuntimeError):
    """Engine-owned backup veto translated for the shared recycle worker."""

    def __init__(self, reason: str):
        self.code = "BACKUP_TIMEOUT" if reason == "timeout" else "BACKUP_FAILED"
        super().__init__(self.code)


@runtime_checkable
class BaasRecycleRuntime(Protocol):
    """Physical inventory, release and confirmation; no backup capability required."""

    def list_devices_by_bot_uuid(self, *, bot_uuid: str) -> list[dict[str, Any]]: ...
    def destroy_bot(self, bot_uuid: str, operator: str, request_id: str) -> dict[str, Any]: ...
    def is_physical_device_destroyed(self, *, provider_device_id: str) -> bool: ...

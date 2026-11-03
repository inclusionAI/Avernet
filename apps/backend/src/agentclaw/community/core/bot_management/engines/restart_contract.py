"""Core-internal collaborators supplied to engine-owned restart execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
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

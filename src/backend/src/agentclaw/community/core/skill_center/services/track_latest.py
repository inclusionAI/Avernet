"""Level-triggered Track Latest fanout and per-Bot convergence tasks."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable

from agentclaw.community.core.repository.protocols.track_latest import TrackLatestRepositoryProtocol
from agentclaw.community.core.skill_center.bot_capability_state_reader_protocol import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.bot_runtime_projector_protocol import (
    BotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.core.skill_center.materialization_contract import (
    PublishedMaterializedSkillVersion,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.track_latest_service_protocol import (
    TrackLatestServiceProtocol,
)
from agentclaw.community.core.skill_center.track_latest_contract import (
    latest_dependency_delta,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.types import Complete, Fail, Retry, TaskOutcome
from agentclaw.community.utils.env_utils import get_current_env


TRACK_LATEST_FANOUT_TASK = "skill_center.track_latest_fanout"
BOT_TRACK_LATEST_RECONCILE_TASK = "skill_center.bot_track_latest_reconcile"
TRACK_LATEST_DEADLINE_SECONDS = 30 * 60


class TrackLatestService(TrackLatestServiceProtocol):
    """Turn the stable PUBLISHED seam into one durable level-triggered fanout."""

    def __init__(self, tasks: TaskQueueService) -> None:
        self._tasks = tasks

    def version_published(
        self, version: PublishedMaterializedSkillVersion
    ) -> None:
        self._tasks.enqueue(
            TRACK_LATEST_FANOUT_TASK,
            {"skill_id": version.skill_id},
            deadline_seconds=TRACK_LATEST_DEADLINE_SECONDS,
            idempotency_key=f"track-latest-fanout:{version.skill_id}",
        )


class TrackLatestFanoutTaskHandler:
    def __init__(
        self,
        *,
        candidates: TrackLatestRepositoryProtocol,
        tasks: TaskQueueService,
        env_provider: Callable[[], str] = get_current_env,
    ) -> None:
        self._candidates = candidates
        self._tasks = tasks
        self._env_provider = env_provider

    @property
    def task_type(self) -> str:
        return TRACK_LATEST_FANOUT_TASK

    def handle(self, payload: dict) -> TaskOutcome:
        try:
            skill_id = _positive_int(payload.get("skill_id"), "skill_id")
        except ValueError as exc:
            return Fail(str(exc))
        try:
            candidates = self._candidates.list_candidates(
                env=self._env_provider(), skill_id=skill_id
            )
            for candidate in candidates:
                self._tasks.enqueue(
                    BOT_TRACK_LATEST_RECONCILE_TASK,
                    {
                        "owner_id": candidate.owner_id,
                        "bot_id": candidate.bot_id,
                        "skill_id": skill_id,
                    },
                    deadline_seconds=TRACK_LATEST_DEADLINE_SECONDS,
                    idempotency_key=_bot_task_key(
                        owner_id=candidate.owner_id,
                        bot_id=candidate.bot_id,
                        skill_id=skill_id,
                    ),
                )
        except Exception as exc:
            return Retry(repr(exc))
        return Complete()


class BotTrackLatestReconcileTaskHandler:
    def __init__(
        self,
        *,
        reader: BotCapabilityStateReaderProtocol,
        projector: BotRuntimeProjectorProtocol,
        latest: TrackLatestRepositoryProtocol,
        env_provider: Callable[[], str] = get_current_env,
    ) -> None:
        self._reader = reader
        self._projector = projector
        self._latest = latest
        self._env_provider = env_provider

    @property
    def task_type(self) -> str:
        return BOT_TRACK_LATEST_RECONCILE_TASK

    def handle(self, payload: dict) -> TaskOutcome:
        try:
            owner_id = _nonempty(payload.get("owner_id"), "owner_id")
            bot_id = _nonempty(payload.get("bot_id"), "bot_id")
            skill_id = _positive_int(payload.get("skill_id"), "skill_id")
        except ValueError as exc:
            return Fail(str(exc))
        try:
            return asyncio.run(
                self._reconcile(
                    owner_id=owner_id,
                    bot_id=bot_id,
                    skill_id=skill_id,
                )
            )
        except Exception as exc:
            return Retry(repr(exc))

    async def _reconcile(
        self, *, owner_id: str, bot_id: str, skill_id: int
    ) -> TaskOutcome:
        try:
            assets = self._reader.active_skill_assets(
                bot_id=bot_id, owner_id=owner_id
            )
        except LocalSkillNotFoundError:
            return Complete()
        if not any(asset.skill_id == skill_id for asset in assets):
            return Complete()
        delta = latest_dependency_delta(
            self._latest.list_published_versions(
                env=self._env_provider(), skill_id=skill_id
            )
        )
        before = await self._projector.snapshot_skill_mappings(
            bot_id=bot_id, owner_id=owner_id
        )
        await self._projector.project(
            bot_id=bot_id,
            owner_id=owner_id,
            scope=ProjectionScope(
                skills=True,
                mcp=True,
                claimed_mcp=delta.claimed_mcp,
                released_mcp=delta.released_mcp,
            ),
        )
        after = await self._projector.snapshot_skill_mappings(
            bot_id=bot_id, owner_id=owner_id
        )
        if before != after:
            return Retry("Track Latest desired mapping changed during projection")
        return Complete()


def _bot_task_key(*, owner_id: str, bot_id: str, skill_id: int) -> str:
    digest = hashlib.sha256(f"{owner_id}\x1f{bot_id}".encode()).hexdigest()[:32]
    return f"bot-track-latest:{skill_id}:{digest}"


def _positive_int(value: object, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _nonempty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be a non-empty unpadded string")
    return value


__all__ = [
    "BOT_TRACK_LATEST_RECONCILE_TASK",
    "TRACK_LATEST_FANOUT_TASK",
    "BotTrackLatestReconcileTaskHandler",
    "TrackLatestFanoutTaskHandler",
    "TrackLatestService",
]

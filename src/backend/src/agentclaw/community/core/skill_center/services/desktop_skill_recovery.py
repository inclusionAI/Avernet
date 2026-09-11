"""One level-triggered recovery loop for every Desktop Skill entry point."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from enum import StrEnum

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skill_center.bot_runtime_projector_protocol import (
    BotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersionIdentity,
)
from agentclaw.community.core.skill_center.center_content_distribution import (
    CenterContentDistribution,
    CenterContentPendingPackage,
    CenterContentUnavailablePackage,
)
from agentclaw.community.core.skill_center.desktop_skill_recovery_protocol import (
    DesktopSkillRecoveryServiceProtocol,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionResult,
    RuntimeProjectionIssue,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    retired_logical_skill_mappings,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayoutPhase,
    runtime_uses_pool_paths,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.types import (
    Complete,
    EnqueueResult,
    Fail,
    Reschedule,
    Retry,
    TaskOutcome,
)
from agentclaw.community.log import get_logger
from agentclaw.community.di.config import DesktopSkillRecoveryConfig
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.utils.env_utils import get_current_env


DESKTOP_SKILL_RECOVERY_TASK = "skill_center.desktop_skill_recovery"
DESKTOP_SKILL_RECOVERY_DEADLINE_SECONDS = 30 * 60
DESKTOP_SKILL_RECOVERY_DELAY_SECONDS = 5.0

_NORMAL_PENDING_CODES = frozenset(
    {
        "CENTER_CONTENT_PACKAGE_PENDING",
        "CENTER_CONTENT_DOWNLOAD_PENDING",
        "CENTER_CONTENT_DOWNLOAD_CAPACITY",
    }
)
_POOL_TRANSITION_CODE = "SKILLS_POOL_TRANSITION_OWNS_MAPPING"
_DEVICE_OFFLINE_CODE = "DESKTOP_DEVICE_OFFLINE"
_KEY_DIGEST_CHARS = 32
logger = get_logger()


class DesktopRecoveryDisposition(StrEnum):
    """Whether a Bot is eligible for Desktop Skill recovery work."""

    RUN = "RUN"
    WAIT_FOR_DEVICE = "WAIT_FOR_DEVICE"
    STOP = "STOP"


class RecoveryContinuation(StrEnum):
    """What the current durable recovery task should do next."""

    COMPLETE_WAITING_FOR_EVENT = "COMPLETE_WAITING_FOR_EVENT"
    RETRY_WITH_BACKOFF = "RETRY_WITH_BACKOFF"
    RESCHEDULE_FOR_PROGRESS = "RESCHEDULE_FOR_PROGRESS"
    COMPLETE_PERMANENT = "COMPLETE_PERMANENT"


_RUN_STATUSES = frozenset({"ACTIVE", "PENDING"})
_WAIT_STATUSES = frozenset({"OFFLINE"})
_STOP_STATUSES = frozenset({"FAILED", "RELEASING", "RELEASED"})


def _desktop_recovery_disposition(status: object) -> DesktopRecoveryDisposition:
    """Classify persisted Bot status without guessing unknown values."""

    normalized = str(status or "").upper()
    if normalized in _RUN_STATUSES:
        return DesktopRecoveryDisposition.RUN
    if normalized in _WAIT_STATUSES:
        return DesktopRecoveryDisposition.WAIT_FOR_DEVICE
    return DesktopRecoveryDisposition.STOP


def _is_unknown_recovery_status(status: object) -> bool:
    normalized = str(status or "").upper()
    return normalized not in _RUN_STATUSES | _WAIT_STATUSES | _STOP_STATUSES


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty unpadded string")
    return value


def build_desktop_skill_recovery_key(*, owner_id: str, bot_id: str) -> str:
    """Build the Bot-level key; queue env/app/task_type supply the other scope."""
    owner = _required_text(owner_id, "owner_id")
    bot = _required_text(bot_id, "bot_id")
    digest = hashlib.sha256(f"{owner}\x1f{bot}".encode()).hexdigest()[
        :_KEY_DIGEST_CHARS
    ]
    return f"desktop-skill-recovery:{digest}"


def is_normal_center_content_wait(code: str) -> bool:
    """Whether a retryable result is healthy content progress, not a fault."""
    return code in _NORMAL_PENDING_CODES


class DesktopSkillRecoveryService(DesktopSkillRecoveryServiceProtocol):
    """Common entry-point adapter: Desktop filter plus durable ensure."""

    def __init__(self, *, bots: BotRepository, tasks: TaskQueueService) -> None:
        self._bots = bots
        self._tasks = tasks

    def ensure(self, *, owner_id: str, bot_id: str) -> EnqueueResult | None:
        owner_id = _required_text(owner_id, "owner_id")
        bot_id = _required_text(bot_id, "bot_id")
        bot = self._bots.get_by_id_and_owner(bot_id, owner_id)
        if bot is None or bot.get("bot_type") != "desktop":
            return None
        disposition = _desktop_recovery_disposition(bot.get("status"))
        if disposition is not DesktopRecoveryDisposition.RUN:
            if _is_unknown_recovery_status(bot.get("status")):
                logger.warning(
                    "[DesktopSkillRecovery] skip unknown Bot status "
                    "owner_id=%s bot_id=%s status=%r",
                    owner_id,
                    bot_id,
                    bot.get("status"),
                )
            return None
        return self._tasks.enqueue(
            DESKTOP_SKILL_RECOVERY_TASK,
            {"owner_id": owner_id, "bot_id": bot_id},
            deadline_seconds=DESKTOP_SKILL_RECOVERY_DEADLINE_SECONDS,
            idempotency_key=build_desktop_skill_recovery_key(
                owner_id=owner_id, bot_id=bot_id
            ),
        )


class DesktopSkillRecoveryTaskHandler:
    """Prepare current exact packages, re-read, then project Skills only."""

    def __init__(
        self,
        *,
        bots: BotRepository,
        projector: BotRuntimeProjectorProtocol,
        distribution: CenterContentDistribution,
        layouts: SkillsPoolLayoutRepositoryProtocol,
        env_provider: Callable[[], str] = get_current_env,
    ) -> None:
        self._bots = bots
        self._projector = projector
        self._distribution = distribution
        self._layouts = layouts
        self._env_provider = env_provider

    @property
    def task_type(self) -> str:
        return DESKTOP_SKILL_RECOVERY_TASK

    def handle(self, payload: dict | None) -> TaskOutcome:
        try:
            if not isinstance(payload, dict) or set(payload) != {"owner_id", "bot_id"}:
                raise ValueError("payload must contain only owner_id and bot_id")
            owner_id = _required_text(payload.get("owner_id"), "owner_id")
            bot_id = _required_text(payload.get("bot_id"), "bot_id")
        except ValueError as error:
            return Fail(f"invalid Desktop Skill recovery payload: {error}")
        try:
            return asyncio.run(self._recover(owner_id=owner_id, bot_id=bot_id))
        except Exception as error:
            logger.exception(
                "[DesktopSkillRecovery] attempt failed owner_id=%s bot_id=%s",
                owner_id,
                bot_id,
            )
            return Retry(repr(error))

    async def _recover(self, *, owner_id: str, bot_id: str) -> TaskOutcome:
        bot = self._current_desktop(owner_id=owner_id, bot_id=bot_id)
        if bot is None:
            return Complete()
        if self._stop_for_status(bot, phase="start"):
            return Complete()
        if bot.get("binding_id") is None:
            return Retry("Desktop Bot has no current binding")
        if self._pool_transition_owns_mappings(bot):
            return Reschedule(DESKTOP_SKILL_RECOVERY_DELAY_SECONDS)

        scope = ProjectionScope(skills=True)
        plan = self._projector.resolve_plan(
            bot_id=bot_id,
            owner_id=owner_id,
            scope=scope,
        )
        prepare_states: dict[
            tuple[str, str], str | CenterContentUnavailablePackage
        ] = {}
        seen: set[tuple[str, str]] = set()
        for mapping in plan.projection.skill_mappings:
            if mapping.corpus != "center":
                continue
            if mapping.skill_uuid is None or mapping.sc_version_number is None:
                raise ValueError("Center mapping has no exact identity")
            key = (mapping.skill_uuid, mapping.sc_version_number)
            if key in seen:
                continue
            seen.add(key)
            try:
                prepared = self._distribution.prepare(
                    CanonicalCenterVersionIdentity(*key)
                )
            except Exception:
                logger.exception(
                    "[DesktopSkillRecovery] package prepare failed "
                    "owner_id=%s bot_id=%s skill_uuid=%s sc_version_number=%s",
                    owner_id,
                    bot_id,
                    mapping.skill_uuid,
                    mapping.sc_version_number,
                )
                prepare_states[key] = "retryable_error"
                continue
            if isinstance(prepared, CenterContentPendingPackage):
                prepare_states[key] = "pending"
            elif isinstance(prepared, CenterContentUnavailablePackage):
                prepare_states[key] = (
                    "retryable_error" if prepared.retryable else prepared
                )

        # Heavy package preparation may overlap a deactivate, version publish,
        # rebind, or Pool transition. Discard the old plan and validate all
        # mutable targeting facts before the only Runtime write.
        latest_bot = self._current_desktop(owner_id=owner_id, bot_id=bot_id)
        if latest_bot is None:
            return Complete()
        if self._stop_for_status(latest_bot, phase="before_apply"):
            return Complete()
        if latest_bot.get("binding_id") is None:
            return Retry("Desktop Bot has no current binding after prepare")
        if self._pool_transition_owns_mappings(latest_bot):
            return Reschedule(DESKTOP_SKILL_RECOVERY_DELAY_SECONDS)
        latest_plan = self._projector.resolve_plan(
            bot_id=bot_id,
            owner_id=owner_id,
            scope=scope,
        )
        latest_center_keys: set[tuple[str, str]] = set()
        permanent_by_name: dict[str, CenterContentUnavailablePackage] = {}
        for mapping in latest_plan.projection.skill_mappings:
            if mapping.corpus != "center":
                continue
            if mapping.skill_uuid is None or mapping.sc_version_number is None:
                raise ValueError("Center mapping has no exact identity")
            key = (mapping.skill_uuid, mapping.sc_version_number)
            latest_center_keys.add(key)
            prepared = prepare_states.get(key)
            if (
                isinstance(prepared, CenterContentUnavailablePackage)
                and not prepared.retryable
            ):
                permanent_by_name[mapping.link_name] = prepared
        retired_mappings = tuple(
            retired_logical_skill_mappings(
                list(plan.projection.skill_mappings),
                list(latest_plan.projection.skill_mappings),
            )
        )
        projection = await self._projector.apply_plan(
            plan=latest_plan,
            retired_mappings=retired_mappings,
            scope=scope,
        )
        projection = self._preserve_permanent_prepare_issues(
            projection,
            permanent_by_name=permanent_by_name,
            all_latest_center_permanent=(
                bool(latest_center_keys)
                and len(permanent_by_name) == len(latest_center_keys)
            ),
        )
        return self._outcome(
            projection,
            prepare_pending=any(
                prepare_states.get(key) == "pending" for key in latest_center_keys
            ),
            prepare_retryable_error=any(
                prepare_states.get(key) == "retryable_error"
                for key in latest_center_keys
            ),
        )

    @staticmethod
    def _preserve_permanent_prepare_issues(
        projection: RuntimeProjectionResult,
        *,
        permanent_by_name: dict[str, CenterContentUnavailablePackage],
        all_latest_center_permanent: bool,
    ) -> RuntimeProjectionResult:
        """Keep exact prepare failures from degrading into synthetic waits."""
        if not permanent_by_name:
            return projection
        logger.warning(
            "[DesktopSkillRecovery] permanent package preparation issues "
            "codes=%s names=%s",
            sorted({package.code for package in permanent_by_name.values()}),
            sorted(permanent_by_name),
        )
        retained = tuple(
            issue
            for issue in projection.issues
            if not (
                issue.retryable
                and is_normal_center_content_wait(issue.code)
                and (
                    issue.name in permanent_by_name
                    or (issue.name is None and all_latest_center_permanent)
                )
            )
        )
        existing = {(issue.name, issue.code) for issue in retained}
        issues = retained + tuple(
            RuntimeProjectionIssue(
                resource_type="SKILL",
                code=package.code,
                reason="Exact Center package preparation cannot be completed automatically",
                status=RuntimeProjectionStatus.DEGRADED,
                retryable=False,
                name=name,
                corpus="CENTER",
                logical_location=f"active-skills/{name}",
                suggested_action="请联系管理员检查不可变 Skill 包及其描述文件。",
            )
            for name, package in permanent_by_name.items()
            if (name, package.code) not in existing
        )
        return RuntimeProjectionResult(
            status=RuntimeProjectionStatus.DEGRADED,
            components={**projection.components, "skills": RuntimeProjectionStatus.DEGRADED},
            issues=issues,
            reason=projection.reason,
        )

    def _current_desktop(self, *, owner_id: str, bot_id: str) -> dict | None:
        bot = self._bots.get_by_id_and_owner(bot_id, owner_id)
        if bot is None or bot.get("bot_type") != "desktop":
            return None
        if bot.get("env") != self._env_provider():
            return None
        return bot

    def _pool_transition_owns_mappings(self, bot: dict) -> bool:
        state = self._layouts.get(
            BotSkillLayoutScope(
                env=str(bot["env"]),
                entity_id=str(bot.get("entity_id") or bot["owner_id"]),
                bot_id=str(bot["bot_id"]),
            )
        )
        return (
            state is not None
            and runtime_uses_pool_paths(state)
            and state.phase is not SkillLayoutPhase.POOL_ACTIVE
        )

    @staticmethod
    def _stop_for_status(bot: dict, *, phase: str) -> bool:
        disposition = _desktop_recovery_disposition(bot.get("status"))
        if disposition is DesktopRecoveryDisposition.RUN:
            return False
        if disposition is DesktopRecoveryDisposition.WAIT_FOR_DEVICE:
            logger.info(
                "[DesktopSkillRecovery] waiting for Desktop reconnect "
                "owner_id=%s bot_id=%s phase=%s",
                bot.get("owner_id"),
                bot.get("bot_id"),
                phase,
            )
        elif _is_unknown_recovery_status(bot.get("status")):
            logger.warning(
                "[DesktopSkillRecovery] stop unknown Bot status "
                "owner_id=%s bot_id=%s status=%r phase=%s",
                bot.get("owner_id"),
                bot.get("bot_id"),
                bot.get("status"),
                phase,
            )
        return True

    @staticmethod
    def _outcome(
        projection: RuntimeProjectionResult,
        *,
        prepare_pending: bool,
        prepare_retryable_error: bool,
    ) -> TaskOutcome:
        retryable = tuple(issue for issue in projection.issues if issue.retryable)
        if any(issue.code == _DEVICE_OFFLINE_CODE for issue in retryable):
            continuation = RecoveryContinuation.COMPLETE_WAITING_FOR_EVENT
        elif any(issue.code == _POOL_TRANSITION_CODE for issue in retryable):
            continuation = RecoveryContinuation.RESCHEDULE_FOR_PROGRESS
        else:
            abnormal = tuple(
                issue
                for issue in retryable
                if not is_normal_center_content_wait(issue.code)
            )
            if prepare_retryable_error or abnormal:
                continuation = RecoveryContinuation.RETRY_WITH_BACKOFF
            elif prepare_pending or any(
                is_normal_center_content_wait(issue.code) for issue in retryable
            ):
                continuation = RecoveryContinuation.RESCHEDULE_FOR_PROGRESS
            else:
                continuation = RecoveryContinuation.COMPLETE_PERMANENT

        if continuation is RecoveryContinuation.COMPLETE_WAITING_FOR_EVENT:
            return Complete()
        if continuation is RecoveryContinuation.RESCHEDULE_FOR_PROGRESS:
            return Reschedule(DESKTOP_SKILL_RECOVERY_DELAY_SECONDS)
        if continuation is RecoveryContinuation.RETRY_WITH_BACKOFF:
            abnormal_codes = sorted(
                {
                    issue.code
                    for issue in retryable
                    if not is_normal_center_content_wait(issue.code)
                }
            )
            return Retry(
                "Desktop Skill recovery transient failure"
                + (f": {','.join(abnormal_codes)}" if abnormal_codes else "")
            )
        if (
            projection.status is RuntimeProjectionStatus.PENDING
            and not projection.issues
        ):
            return Retry("Desktop Skill recovery returned unexplained PENDING")
        if projection.status is RuntimeProjectionStatus.DEGRADED:
            logger.warning(
                "[DesktopSkillRecovery] stopped automatic retries for permanent "
                "issues codes=%s",
                sorted({issue.code for issue in projection.issues}),
            )
        return Complete()


class DesktopSkillRecoverySweeper(LifecycleBase):
    """Page live, bound Desktop Bots and only ensure their common task."""

    def __init__(
        self,
        *,
        bots: BotRepository,
        recovery: DesktopSkillRecoveryServiceProtocol,
        config: DesktopSkillRecoveryConfig,
    ) -> None:
        self._bots = bots
        self._recovery = recovery
        self._config = config
        self._running = False
        self._task: asyncio.Task | None = None

    async def startup(self) -> None:
        if not self._config.enabled:
            logger.info("[DesktopSkillRecovery] sweep disabled")
            return
        self._running = True
        await asyncio.to_thread(self.sweep_once)
        self._task = asyncio.create_task(self._loop())

    async def shutdown(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._config.sweep_interval_seconds)
                if self._running:
                    await asyncio.to_thread(self.sweep_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[DesktopSkillRecovery] sweep round failed")

    def sweep_once(self) -> tuple[int, int]:
        """Return ``(eligible, ensured)`` for deterministic tests/monitoring."""
        started_at = time.monotonic()
        scanned = 0
        eligible = 0
        ensured = 0
        skipped_offline = 0
        skipped_terminal = 0
        skipped_unknown_status = 0
        task_created = 0
        task_joined_existing = 0
        ensure_failed = 0
        page = 1
        while True:
            total, bots = self._bots.search_bots(
                bot_type="desktop",
                page=page,
                page_size=self._config.sweep_page_size,
            )
            for bot in bots:
                scanned += 1
                owner_id = bot.get("owner_id")
                bot_id = bot.get("bot_id")
                if (
                    bot.get("binding_id") is None
                    or not isinstance(owner_id, str)
                    or not isinstance(bot_id, str)
                ):
                    continue
                disposition = _desktop_recovery_disposition(bot.get("status"))
                if disposition is DesktopRecoveryDisposition.WAIT_FOR_DEVICE:
                    skipped_offline += 1
                    continue
                if disposition is DesktopRecoveryDisposition.STOP:
                    if _is_unknown_recovery_status(bot.get("status")):
                        skipped_unknown_status += 1
                        logger.warning(
                            "[DesktopSkillRecovery] sweep skipped unknown Bot "
                            "status owner_id=%s bot_id=%s status=%r",
                            owner_id,
                            bot_id,
                            bot.get("status"),
                        )
                    else:
                        skipped_terminal += 1
                    continue
                eligible += 1
                try:
                    result = self._recovery.ensure(
                        owner_id=owner_id,
                        bot_id=bot_id,
                    )
                    if result is not None:
                        ensured += 1
                        if bool(getattr(result, "created", False)):
                            task_created += 1
                        else:
                            task_joined_existing += 1
                except Exception:
                    ensure_failed += 1
                    logger.exception(
                        "[DesktopSkillRecovery] sweep ensure failed "
                        "owner_id=%s bot_id=%s",
                        owner_id,
                        bot_id,
                    )
            if not bots or page * self._config.sweep_page_size >= total:
                break
            page += 1
        logger.info(
            "[DesktopSkillRecovery] sweep completed scanned=%s eligible=%s "
            "skipped_offline=%s skipped_terminal=%s "
            "skipped_unknown_status=%s task_created=%s "
            "task_joined_existing=%s ensure_failed=%s duration_ms=%s",
            scanned,
            eligible,
            skipped_offline,
            skipped_terminal,
            skipped_unknown_status,
            task_created,
            task_joined_existing,
            ensure_failed,
            round((time.monotonic() - started_at) * 1000, 3),
        )
        return eligible, ensured


__all__ = [
    "DESKTOP_SKILL_RECOVERY_DEADLINE_SECONDS",
    "DESKTOP_SKILL_RECOVERY_DELAY_SECONDS",
    "DESKTOP_SKILL_RECOVERY_TASK",
    "DesktopSkillRecoveryService",
    "DesktopSkillRecoverySweeper",
    "DesktopSkillRecoveryTaskHandler",
    "build_desktop_skill_recovery_key",
    "is_normal_center_content_wait",
]

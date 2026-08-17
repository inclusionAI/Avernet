"""Bot-level mutual exclusion between local Skill edits and layout rollback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time

from injector import inject

from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolLayoutRepositoryProtocol
from agentclaw.community.core.skills_pool.participation import (
    SkillLayoutParticipation,
    SkillLayoutParticipationResolver,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayoutPhase,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import (
    CacheLockInfrastructureError,
    CachePlugin,
)


logger = get_logger()


class SkillsPoolEditPausedError(RuntimeError):
    """Raised when a local Skill mutation cannot safely start."""


class SkillsPoolEditBusyError(SkillsPoolEditPausedError):
    """Another ordinary Local Skill mutation owns the Bot lock."""


class SkillsPoolEditRollbackError(SkillsPoolEditPausedError):
    """A Pool-to-Legacy rollback owns the Bot filesystem."""


class SkillsPoolEditLockUnavailableError(SkillsPoolEditPausedError):
    """The distributed lock service is unavailable; fail closed."""


@dataclass(frozen=True, slots=True)
class SkillsPoolEditLease:
    key: str
    token: str | None


class SkillsPoolEditGuard:
    """Serialize local mutations with Pool→Legacy filesystem reconstruction."""

    _LOCK_TTL_SECONDS = 600
    _ROLLBACK_PHASES = {
        SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING,
        SkillLayoutPhase.LEGACY_ROLLBACK_COMMITTED,
    }

    @inject
    def __init__(
        self,
        *,
        cache: CachePlugin,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        participation_resolver: SkillLayoutParticipationResolver,
    ) -> None:
        self._cache = cache
        self._layouts = layout_repository
        self._participation_resolver = participation_resolver

    @staticmethod
    def _key(*, scope: BotSkillLayoutScope) -> str:
        return f"skills-pool:local-edit:{scope.env}:{scope.entity_id}:{scope.bot_id}"

    def acquire_for_edit(
        self,
        *,
        scope: BotSkillLayoutScope,
    ) -> SkillsPoolEditLease:
        participation = self._participation_resolver.resolve(scope=scope)
        if not participation.participates_in_pool_layout:
            return SkillsPoolEditLease(key="", token=None)

        if self._is_rollback_phase(scope):
            self._log_rejection(
                scope=scope, participation=participation, reason="rollback_phase"
            )
            raise SkillsPoolEditRollbackError(
                "Skills are temporarily read-only during layout rollback"
            )

        try:
            lease = self._acquire_for_edit(scope=scope)
        except CacheLockInfrastructureError as exc:
            self._log_rejection(
                scope=scope, participation=participation, reason="cache_unavailable"
            )
            raise SkillsPoolEditLockUnavailableError(
                "Skill updates are temporarily unavailable because the lock service is unavailable"
            ) from exc
        if lease is None:
            if self._is_rollback_phase(scope):
                self._log_rejection(
                    scope=scope, participation=participation, reason="rollback_phase"
                )
                raise SkillsPoolEditRollbackError(
                    "Skills are temporarily read-only during layout rollback"
                )
            self._log_rejection(
                scope=scope, participation=participation, reason="lock_busy"
            )
            raise SkillsPoolEditBusyError(
                "Another skill update is already in progress; please retry shortly"
            )
        if self._is_rollback_phase(scope):
            self.release(lease)
            self._log_rejection(
                scope=scope, participation=participation, reason="rollback_phase"
            )
            raise SkillsPoolEditRollbackError(
                "Skills are temporarily read-only during layout rollback"
            )
        return lease

    async def acquire_for_edit_wait(
        self, *, scope: BotSkillLayoutScope, timeout_seconds: float = 30.0
    ) -> SkillsPoolEditLease:
        """Wait for another ordinary Local Skill edit, never bypass its lock.

        Layout rollback remains an immediate pause; only a held edit lease is
        retried.  This gives concurrent same-name uploads one serialized
        authoritative read rather than a lock-contention race.
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                return self.acquire_for_edit(scope=scope)
            except SkillsPoolEditBusyError:
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.01)
            except SkillsPoolEditPausedError:
                # Rollback and cache failures must never be transformed into
                # a 30-second ordinary-edit wait.
                if self._is_rollback_phase(scope):
                    raise
                raise

    def acquire_for_rollback(
        self,
        *,
        scope: BotSkillLayoutScope,
    ) -> SkillsPoolEditLease | None:
        key = self._key(scope=scope)
        # Rollback has historically treated cache unavailability as a
        # retryable ``EDIT_BUSY`` outcome.  Preserve that operator contract;
        # only product-side edits need the more precise user-facing outcome.
        token = self._cache.acquire_lock(key, ttl=self._LOCK_TTL_SECONDS)
        if token is None:
            return None
        return SkillsPoolEditLease(key=key, token=token)

    def _acquire_for_edit(
        self, *, scope: BotSkillLayoutScope
    ) -> SkillsPoolEditLease | None:
        key = self._key(scope=scope)
        token = self._cache.acquire_lock_strict(key, ttl=self._LOCK_TTL_SECONDS)
        if token is None:
            return None
        return SkillsPoolEditLease(key=key, token=token)

    def release(self, lease: SkillsPoolEditLease) -> bool:
        if lease.token is None:
            return True
        return self._cache.release_lock(lease.key, lease.token)

    def _is_rollback_phase(self, scope: BotSkillLayoutScope) -> bool:
        return self._layouts.get(scope).phase in self._ROLLBACK_PHASES

    def _log_rejection(
        self,
        *,
        scope: BotSkillLayoutScope,
        participation: SkillLayoutParticipation,
        reason: str,
    ) -> None:
        logger.warning(
            "[skills_pool.edit_guard] edit rejected env=%s entity_id=%s "
            "bot_id=%s layout_participation=%s reason=%s",
            scope.env,
            scope.entity_id,
            scope.bot_id,
            participation.label,
            reason,
        )


__all__ = [
    "SkillsPoolEditGuard",
    "SkillsPoolEditBusyError",
    "SkillsPoolEditLockUnavailableError",
    "SkillsPoolEditLease",
    "SkillsPoolEditPausedError",
    "SkillsPoolEditRollbackError",
]

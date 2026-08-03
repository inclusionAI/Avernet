"""Bot-level mutual exclusion between local Skill edits and layout rollback."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import time

from injector import inject

from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayoutPhase,
)
from agentclaw.community.plugin_api.cache import CachePlugin


class SkillsPoolEditPausedError(RuntimeError):
    """Raised when a local Skill mutation cannot safely start."""


@dataclass(frozen=True, slots=True)
class SkillsPoolEditLease:
    key: str
    token: str


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
    ) -> None:
        self._cache = cache
        self._layouts = layout_repository

    @staticmethod
    def _key(*, scope: BotSkillLayoutScope) -> str:
        return (
            "skills-pool:local-edit:"
            f"{scope.env}:{scope.entity_id}:{scope.bot_id}"
        )

    def acquire_for_edit(
        self,
        *,
        scope: BotSkillLayoutScope,
    ) -> SkillsPoolEditLease:
        lease = self.acquire_for_rollback(scope=scope)
        if lease is None:
            raise SkillsPoolEditPausedError(
                "Skills are temporarily read-only while layout work is running"
            )
        if self._layouts.get(scope).phase in self._ROLLBACK_PHASES:
            self.release(lease)
            raise SkillsPoolEditPausedError(
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
            except SkillsPoolEditPausedError:
                if self._layouts.get(scope).phase in self._ROLLBACK_PHASES:
                    raise
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.01)

    def acquire_for_rollback(
        self,
        *,
        scope: BotSkillLayoutScope,
    ) -> SkillsPoolEditLease | None:
        key = self._key(scope=scope)
        token = self._cache.acquire_lock(key, ttl=self._LOCK_TTL_SECONDS)
        if token is None:
            return None
        return SkillsPoolEditLease(key=key, token=token)

    def release(self, lease: SkillsPoolEditLease) -> bool:
        return self._cache.release_lock(lease.key, lease.token)


__all__ = [
    "SkillsPoolEditGuard",
    "SkillsPoolEditLease",
    "SkillsPoolEditPausedError",
]

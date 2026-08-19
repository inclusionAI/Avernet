"""Layout-neutral Bot capability mutation fence.

This guard protects the desired-state UoW, runtime projection and rollback as
one critical section for *every* Bot.  It is intentionally separate from the
Skills Pool edit guard: a legacy Bot does not participate in Pool layout, but
it can still race a Direct activation against a SkillSet compensation.
"""

from __future__ import annotations

from dataclasses import dataclass

from injector import inject

from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.plugin_api.cache import (
    CacheLockInfrastructureError,
    CachePlugin,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class BotCapabilityMutationBusyError(RuntimeError):
    """Another capability mutation owns this Bot's desired-state fence."""


class BotCapabilityMutationLockUnavailableError(RuntimeError):
    """The distributed mutation fence cannot be acquired safely."""


@dataclass(frozen=True, slots=True)
class BotCapabilityMutationLease:
    key: str
    token: str


class BotCapabilityMutationGuard:
    """A reliable cross-layout mutation guard keyed by the real Bot scope."""

    _LOCK_TTL_SECONDS = 600

    @inject
    def __init__(self, cache: CachePlugin) -> None:
        self._cache = cache

    @staticmethod
    def _key(*, scope: BotSkillLayoutScope) -> str:
        return (
            "skill-capability:mutation:"
            f"{get_current_avernet_tenant()}:{scope.env}:{scope.entity_id}:{scope.bot_id}"
        )

    def acquire(self, *, scope: BotSkillLayoutScope) -> BotCapabilityMutationLease:
        key = self._key(scope=scope)
        try:
            token = self._cache.acquire_lock_strict(key, ttl=self._LOCK_TTL_SECONDS)
        except CacheLockInfrastructureError as exc:
            raise BotCapabilityMutationLockUnavailableError() from exc
        if token is None:
            raise BotCapabilityMutationBusyError()
        return BotCapabilityMutationLease(key=key, token=token)

    def release(self, lease: BotCapabilityMutationLease) -> bool:
        return self._cache.release_lock(lease.key, lease.token)


__all__ = [
    "BotCapabilityMutationBusyError",
    "BotCapabilityMutationGuard",
    "BotCapabilityMutationLease",
    "BotCapabilityMutationLockUnavailableError",
]

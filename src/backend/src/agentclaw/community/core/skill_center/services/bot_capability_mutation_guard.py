"""Layout-neutral Bot capability mutation fence.

This guard protects the desired-state UoW, runtime projection and rollback as
one critical section for *every* Bot.  It is intentionally separate from the
Skills Pool edit guard: a legacy Bot does not participate in Pool layout, but
it can still race a Direct activation against a SkillSet compensation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading

from injector import inject

from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.plugin_api.cache import (
    CacheLockInfrastructureError,
    CachePlugin,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


logger = logging.getLogger(__name__)


class BotCapabilityMutationBusyError(RuntimeError):
    """Another capability mutation owns this Bot's desired-state fence."""


class BotCapabilityMutationLockUnavailableError(RuntimeError):
    """The distributed mutation fence cannot be acquired safely."""


@dataclass(slots=True)
class BotCapabilityMutationLease:
    key: str
    token: str
    stop: threading.Event = field(default_factory=threading.Event, repr=False)
    lost: threading.Event = field(default_factory=threading.Event, repr=False)
    heartbeat: threading.Thread | None = field(default=None, repr=False)


class BotCapabilityMutationGuard:
    """A reliable cross-layout mutation guard keyed by the real Bot scope."""

    _LOCK_TTL_SECONDS = 600
    _HEARTBEAT_SECONDS = 120

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
        lease = BotCapabilityMutationLease(key=key, token=token)
        lease.heartbeat = threading.Thread(
            target=self._renew_until_released,
            args=(lease,),
            name="bot-capability-mutation-heartbeat",
            daemon=True,
        )
        lease.heartbeat.start()
        return lease

    def _renew_until_released(self, lease: BotCapabilityMutationLease) -> None:
        while not lease.stop.wait(self._HEARTBEAT_SECONDS):
            try:
                renewed = self._cache.renew_lock_strict(
                    lease.key,
                    lease.token,
                    ttl=self._LOCK_TTL_SECONDS,
                )
            except Exception:
                logger.exception("Bot capability mutation lock renewal failed")
                lease.lost.set()
                return
            if not renewed:
                logger.error("Bot capability mutation lock ownership was lost")
                lease.lost.set()
                return

    @staticmethod
    def ensure_valid(lease: BotCapabilityMutationLease) -> None:
        if lease.lost.is_set():
            raise BotCapabilityMutationLockUnavailableError()

    def release(self, lease: BotCapabilityMutationLease) -> bool:
        lease.stop.set()
        if lease.heartbeat is not None:
            lease.heartbeat.join(timeout=1)
        released = self._cache.release_lock(lease.key, lease.token)
        return released and not lease.lost.is_set()


__all__ = [
    "BotCapabilityMutationBusyError",
    "BotCapabilityMutationGuard",
    "BotCapabilityMutationLease",
    "BotCapabilityMutationLockUnavailableError",
]

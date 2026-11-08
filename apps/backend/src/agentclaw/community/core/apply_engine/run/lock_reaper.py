"""The stale-lock derivation: when a held apply lock stops meaning anything.

Split out of ``config_manifest_apply_service`` for size, and it is the seam
that gives way most cleanly: everything here reads the lock repository and the
TTL and nothing else — no manifest, no report, no orchestrator. The service
keeps the policy that *calls* these (refuse, reap and retry, derive FAILED);
this module answers only the two questions that policy asks.
"""

from typing import Protocol

from agentclaw.community.log import get_logger

logger = get_logger()

#: How long a lock may be held before another apply may take it.
#:
#: Also what bounds a report stranded at ``RUNNING``: a process killed mid-apply
#: never runs its ``finally``, so the row would poll forever. Past this age the
#: read derives ``FAILED`` instead. Derived at read time rather than swept, so
#: there is no second mechanism to keep alive.
#:
#: Generous, because it is a safety net rather than a timeout: an apply that
#: legitimately takes minutes (W5 fetching several sources) must not have its
#: lock stolen mid-write.
APPLY_LOCK_TTL_SECONDS = 30 * 60


class _LockRepository(Protocol):
    """The two reads and the one write this module needs.

    Structural rather than the repository's own Protocol: naming that one here
    would import the manifest package's contract module into a helper that has
    no other reason to know it.
    """

    def get(self, *, env: str, entity_id: str, bot_id: str): ...

    def get_if_stale(
        self, *, env: str, entity_id: str, bot_id: str, ttl_seconds: int
    ): ...

    def release(
        self, *, env: str, entity_id: str, bot_id: str, lock_token: str
    ) -> bool: ...


def is_abandoned(
    locks: _LockRepository, *, env: str, entity_id: str, bot_id: str
) -> bool:
    """True when no live lock backs a ``RUNNING`` report.

    Either the lock is gone (released without the terminal write landing) or
    it is older than the TTL, so no apply can still be working under it.
    """
    held = locks.get(env=env, entity_id=entity_id, bot_id=bot_id)
    if held is None:
        return True
    return (
        locks.get_if_stale(
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            ttl_seconds=APPLY_LOCK_TTL_SECONDS,
        )
        is not None
    )


def reap_stale_lock(
    locks: _LockRepository, *, env: str, entity_id: str, bot_id: str
) -> bool:
    """Drop a lock whose holder is long gone. Returns whether one was freed."""
    stale = locks.get_if_stale(
        env=env,
        entity_id=entity_id,
        bot_id=bot_id,
        ttl_seconds=APPLY_LOCK_TTL_SECONDS,
    )
    if stale is None:
        return False
    logger.warning(
        "[manifest_apply] reaping stale lock, env=%s, entity_id=%s, bot_id=%s",
        env,
        entity_id,
        bot_id,
    )
    return locks.release(
        env=env,
        entity_id=entity_id,
        bot_id=bot_id,
        lock_token=stale.lock_token,
    )


__all__ = ["APPLY_LOCK_TTL_SECONDS", "is_abandoned", "reap_stale_lock"]

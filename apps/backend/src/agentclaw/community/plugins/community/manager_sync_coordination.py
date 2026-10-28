"""RedisManagerSyncCoordinator — the strict shared in-flight boundary.

The PROD implementation of ``ManagerSyncCoordinator`` (Task 1's
contract; ``plugins/local`` holds the test-only reference fake).
Composes the **existing Redis infrastructure** — the same redis URL
the composition root already resolves for the cache — with the strict
lock algorithm's token/CAS discipline:

- No GET-then-DELETE anywhere: lease release, marker finish and
  marker clears are compare-and-delete EVAL scripts.
- Every operation is one atomic EVAL (or one MGET, for a read) over
  keys that share a single Redis Cluster slot; nothing here routes
  through ``CommunityCache`` (its get/set swallow infra failures and
  it has no atomic marker operations).

Key layout (see ``manager_sync_scripts`` for the script contracts):

::

    mgrsync:v1:{sha256(target_id)}:generation
    mgrsync:v1:{sha256(target_id)}:bot:{sha256(tenant, env, bot_pk)}:lease
    ...                                                              :inflight
    ...                                                              :owner_hold

- ``target_id`` comes from configuration and is **fixed** — never
  re-randomized per pod — so all deployments of one target hash onto
  the same coordination namespace. One slot per namespace is the
  deliberate shape (the generation sentinel must sit with the keys it
  guards); single-slot load is a deployment concern to measure, not a
  claim of infinite sharding.
- The per-Bot suffix hashes the canonical ``(tenant, env, bot_pk)``
  triple: the same Bot PK in another tenant or environment coordinates
  under different keys.
- **Generation sentinel** — operations never auto-create it; every
  script verifies it first, and a missing or mismatched sentinel
  refuses everything (``CoordinationUnavailable``): the namespace was
  lost and the sender must stop. Explicit ops-side initialization
  (Task 10) creates it.
- **In-flight record** — TTL-less, written by ``BEGIN``; it outlives
  every lease, so a mid-flight process death blocks the next claim
  until resolved. **Owner hold** — an independent, idempotent marker
  that coexists with (never overwrites) the in-flight record.

Every Redis-level failure raises ``CoordinationUnavailable`` — an
unknown outcome is never flattened into False/None. Errors from the
backend are logged; the exception carries only the stable reason code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

from agentclaw.community.log import get_logger
from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl
from agentclaw.community.plugin_api.manager_sync_coordination import (
    Attempt,
    ClaimResult,
    ClaimStatus,
    CoordinationLost,
    CoordinationUnavailable,
    Lease,
    ManagerSyncCoordinator,
)
from agentclaw.community.plugins.community import manager_sync_scripts as scripts

logger = get_logger()

#: Fixed key prefix for every manager-sync coordination key. The
#: ``{...}`` hash tag right after it binds a whole namespace (and all
#: its bots) to one Redis Cluster slot.
_KEY_NAMESPACE = "mgrsync:v1"


def namespace_hash_tag(target_id: str) -> str:
    """The Cluster hash tag for one target's coordination namespace."""
    return "{" + hashlib.sha256(target_id.encode("utf-8")).hexdigest() + "}"


def bot_key_suffix(key: SyncKey) -> str:
    """Hash of the canonical ``(tenant, env, bot_pk)`` triple."""
    canonical = "\x1f".join((key.avernet_tenant, key.env, str(key.bot_pk)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CoordinationKeys:
    """The four keys of one Bot under one coordination namespace."""

    generation: str
    lease: str
    inflight: str
    owner_hold: str


def coordination_keys(target_id: str, key: SyncKey) -> CoordinationKeys:
    """Very key this namespace uses for one Bot — all in one slot."""
    front = f"{_KEY_NAMESPACE}:{namespace_hash_tag(target_id)}"
    bot = f"{front}:bot:{bot_key_suffix(key)}"
    return CoordinationKeys(
        generation=f"{front}:generation",
        lease=f"{bot}:lease",
        inflight=f"{bot}:inflight",
        owner_hold=f"{bot}:owner_hold",
    )


def namespace_scan_pattern(target_id: str) -> str:
    """The glob covering exactly one coordination namespace.

    Cleanup and diagnostics use this; the pattern is unique per
    target, so a scan never touches another namespace's keys and a
    throwaway instance is never FLUSHDB-ed.
    """
    return f"{_KEY_NAMESPACE}:{namespace_hash_tag(target_id)}:*"


def _as_text(value: Any) -> str:
    """Decode a Redis bulk reply (bytes) or pass a str through."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return value  # narrow: callers pass str here


def _owner_hold_id(key: SyncKey) -> str:
    """The deterministic, independent owner-hold marker id."""
    return f"owner-hold:{key.avernet_tenant}:{key.env}:{key.bot_pk}"


@plugin_impl(
    mode=Mode.PROD,
    rationale="strict shared in-flight coordination over the existing Redis",
)
class RedisManagerSyncCoordinator(ManagerSyncCoordinator):
    """Serialize manager sync per key and guard Owner checks, on Redis.

    Constructed by the composition root (Task 6 wires the DI binding)
    with the fixed configuration identity — ``target_id`` from config
    and the ops-initialized ``generation`` — plus the same Redis URL
    the existing cache infrastructure resolves, or a prebuilt client
    (the composition root builds one ``redis.Redis`` on the deployment's
    shared Redis URL; tests own the clients they build).
    """

    def __init__(
        self,
        *,
        target_id: str,
        generation: str,
        redis_url: str = "",
        client: "redis.Redis | None" = None,
    ) -> None:
        if not target_id:
            raise ValueError("target_id is required (from config, not per-pod)")
        if not generation:
            raise ValueError(
                "generation is required: a namespace without an ops-initialized "
                "generation sentinel must stay stopped"
            )
        if client is not None:
            self._redis = client
        elif redis_url:
            import redis  # community dep; lazy so non-Redis boots skip it

            # ``from_url`` does not connect — the first command does.
            self._redis = redis.Redis.from_url(redis_url)
        else:
            raise ValueError("a redis client or redis_url is required")
        self._target_id = target_id
        self._generation = generation

    # -- internals ------------------------------------------------------

    def _eval(
        self,
        script: str,
        numkeys: int,
        keys: tuple[str, ...],
        argv: tuple[Any, ...],
    ) -> Any:
        try:
            return self._redis.eval(script, numkeys, *keys, *argv)
        except CoordinationUnavailable:
            raise
        except Exception as exc:
            logger.error(
                "[MANAGER-SYNC-COORD-FAILURE] redis backend unavailable: %s",
                exc,
                exc_info=True,
            )
            raise CoordinationUnavailable("coordination-backend-unavailable") from exc

    def _require_zero(self, result: Any, *, op: str) -> bool:
        """Classify a boolean script result (1 / 0 / -2 codes)."""
        if result == 1:
            return True
        if result == 0:
            return False
        if result == scripts.GENERATION_REFUSED:
            raise CoordinationUnavailable("generation-sentinel-missing-or-mismatch")
        raise CoordinationUnavailable(f"unexpected {op} script result: {result!r}")

    def _keys(self, key: SyncKey) -> CoordinationKeys:
        return coordination_keys(self._target_id, key)

    # -- ManagerSyncCoordinator -----------------------------------------

    def claim(self, key: SyncKey, *, holder: str, ttl_seconds: int) -> ClaimResult:
        """SET NX EX gated on generation validity and no markers."""
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be a positive whole second count")
        keys = self._keys(key)
        result = self._eval(
            scripts.CLAIM,
            4,
            (keys.lease, keys.inflight, keys.owner_hold, keys.generation),
            (holder, ttl_seconds, self._generation),
        )
        if result == 1:
            return ClaimResult(
                status=ClaimStatus.ACQUIRED, lease=Lease(key=key, holder=holder)
            )
        if result == 2:
            return ClaimResult(status=ClaimStatus.BUSY, lease=None)
        if result == 0:
            return ClaimResult(status=ClaimStatus.BLOCKED, lease=None)
        if result == scripts.GENERATION_REFUSED:
            raise CoordinationUnavailable("generation-sentinel-missing-or-mismatch")
        raise CoordinationUnavailable(f"unexpected claim script result: {result!r}")

    def renew(self, lease: Lease, *, ttl_seconds: int) -> bool:
        """Extend the lease TTL — only its current holder succeeds."""
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be a positive whole second count")
        keys = self._keys(lease.key)
        return self._require_zero(
            self._eval(
                scripts.RENEW,
                2,
                (keys.lease, keys.generation),
                (lease.holder, ttl_seconds, self._generation),
            ),
            op="renew",
        )

    def begin(self, lease: Lease, *, attempt_id: str, digest: str) -> Attempt:
        """Atomically check lease + no markers, then write the record."""
        keys = self._keys(lease.key)
        payload = json.dumps(
            {
                "attempt_id": attempt_id,
                "digest": digest,
                "holder": lease.holder,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        result = self._eval(
            scripts.BEGIN,
            4,
            (keys.lease, keys.inflight, keys.owner_hold, keys.generation),
            (lease.holder, payload, self._generation),
        )
        if result == 1:
            return Attempt(lease=lease, attempt_id=attempt_id, digest=digest)
        if result == -1:
            raise CoordinationLost("lease-lost")
        if result == 0:
            raise CoordinationLost("marker-present")
        if result == scripts.GENERATION_REFUSED:
            raise CoordinationUnavailable("generation-sentinel-missing-or-mismatch")
        raise CoordinationUnavailable(f"unexpected begin script result: {result!r}")

    def finish(self, attempt: Attempt) -> bool:
        """Compare-delete the serialized attempt; markers stay."""
        keys = self._keys(attempt.lease.key)
        payload = json.dumps(
            {
                "attempt_id": attempt.attempt_id,
                "digest": attempt.digest,
                "holder": attempt.lease.holder,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return self._require_zero(
            self._eval(
                scripts.FINISH,
                2,
                (keys.inflight, keys.generation),
                (payload, self._generation),
            ),
            op="finish",
        )

    def release(self, lease: Lease) -> bool:
        """Compare-delete the lease; markers are never touched."""
        keys = self._keys(lease.key)
        return self._require_zero(
            self._eval(
                scripts.RELEASE,
                2,
                (keys.lease, keys.generation),
                (lease.holder, self._generation),
            ),
            op="release",
        )

    def read_blocked(self, key: SyncKey) -> str | None:
        """In-flight attempt id, else owner-hold id, else None."""
        keys = self._keys(key)
        try:
            # All three keys share one slot: one MGET, one round trip.
            generation, inflight, owner_hold = self._redis.mget(
                keys.generation, keys.inflight, keys.owner_hold
            )
        except Exception as exc:
            logger.error(
                "[MANAGER-SYNC-COORD-FAILURE] redis backend unavailable: %s",
                exc,
                exc_info=True,
            )
            raise CoordinationUnavailable("coordination-backend-unavailable") from exc
        if generation is None or _as_text(generation) != self._generation:
            raise CoordinationUnavailable("generation-sentinel-missing-or-mismatch")
        if inflight is not None:
            try:
                record = json.loads(_as_text(inflight))
            except (ValueError, TypeError) as exc:
                raise CoordinationUnavailable("corrupt-inflight-record") from exc
            if not isinstance(record, dict) or not isinstance(
                record.get("attempt_id"), str
            ):
                raise CoordinationUnavailable("corrupt-inflight-record")
            return record["attempt_id"]
        if owner_hold is not None:
            return _as_text(owner_hold)
        return None

    def clear_confirmed(self, key: SyncKey, *, attempt_id: str) -> bool:
        """CAS-delete exactly one matching marker (either family)."""
        keys = self._keys(key)
        return self._require_zero(
            self._eval(
                scripts.CLEAR_CONFIRMED,
                3,
                (keys.inflight, keys.owner_hold, keys.generation),
                (attempt_id, self._generation),
            ),
            op="clear_confirmed",
        )

    def hold_owner_check(self, key: SyncKey) -> str:
        """Idempotently ensure the independent owner-hold marker."""
        keys = self._keys(key)
        result = self._eval(
            scripts.HOLD_OWNER_CHECK,
            2,
            (keys.owner_hold, keys.generation),
            (_owner_hold_id(key), self._generation),
        )
        if result == scripts.GENERATION_REFUSED:
            raise CoordinationUnavailable("generation-sentinel-missing-or-mismatch")
        if isinstance(result, (bytes, bytearray)):
            text = result.decode("utf-8")
        elif isinstance(result, str) and result:
            text = result
        else:
            raise CoordinationUnavailable(
                f"unexpected hold_owner_check script result: {result!r}"
            )
        return text

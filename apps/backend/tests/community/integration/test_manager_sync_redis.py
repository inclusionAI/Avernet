"""Real-Redis integration tests for the manager-sync coordination boundary.

The PROD ``RedisManagerSyncCoordinator`` run against a throwaway Redis
instance through ``MANAGER_SYNC_TEST_REDIS_URL`` (e.g.
``redis://127.0.0.1:6399/0``): this is where the Lua scripts' real
``EVAL`` semantics — atomic begin ordering, compare-and-delete, marker
coexistence, the generation-sentinel stop — are exercised. The unit
suite (``tests/community/plugins/community/test_manager_sync_coordination.py``)
covers the same interleavings against the in-memory reference fake and
the scripted-EVAL error classification.

**Release gates require this suite unskipped.** When the env var is
absent the suite skips with a loud reason — a skip is "gate not
satisfied", never "gate passed".

Discipline, per the Task 3 brief:

- **Never FLUSHDB.** Each test owns a fresh random namespace — the fixed
  key prefix plus a random ``{sha256(target_id)}`` tag — and the fixture
  deletes only that namespace via a prefix scan.
- Test leases use an explicitly shortened TTL (1s) where real
  server-clock expiry is the point under test; everywhere else the
  fixture reproduces the observable state of a lease expiry (lease key
  absent, markers untouched) directly, never sleeping a production TTL.
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
import redis as redis_lib

from agentclaw.community.core.bot_collaborator.manager_sync.projector import (
    build_snapshot,
)
from agentclaw.community.kernel.bot_manager_sync import SyncFacts, SyncKey
from agentclaw.community.plugin_api.manager_sync_coordination import (
    Attempt,
    ClaimStatus,
    CoordinationLost,
    CoordinationUnavailable,
    Lease,
)
from agentclaw.community.plugins.community.manager_sync_coordination import (
    RedisManagerSyncCoordinator,
    coordination_keys,
    namespace_scan_pattern,
)
from agentclaw.community.plugins.local.bcs_managers import (
    RecordingBcsManagersPlugin,
)

# The whole interleaving family from the unit suite re-runs here over
# the real fixture below, so every named interleaving is proven against
# actual EVAL, not only against the in-memory reference fake.
from tests.community.plugins.community.test_manager_sync_coordination import (  # noqa: F401  (pytest collects these names as tests)
    test_claim_matrix,
    test_clear_confirmed_clears_exactly_one_matching_marker,
    test_expired_lease_is_reclaimable,
    test_finishing_an_attempt_never_clears_owner_hold,
    test_finish_cas_includes_digest,
    test_inflight_survives_lease_expiry,
    test_late_finish_cannot_clear_new_state,
    test_owner_hold_is_idempotent_and_independent_of_inflight,
    test_release_compare_deletes_lease_not_markers,
    test_renew_only_by_holder_before_expiry,
)

_KEY = SyncKey("tenant-a", "dev", 7)
_OTHER_TENANT_KEY = SyncKey("tenant-b", "dev", 7)
_LEASE_TTL = 30


def _test_redis_url_or_skip() -> str:
    """The throwaway Redis instance for this suite, or an explicit skip.

    The release gate requires this suite to run **unskipped** — with the
    variable unset the gate is simply not satisfied.
    """
    url = os.environ.get("MANAGER_SYNC_TEST_REDIS_URL")
    if not url:
        pytest.skip(
            "MANAGER_SYNC_TEST_REDIS_URL is not set — release gates require this real-Redis suite unskipped"
        )
    return url


@dataclass(frozen=True)
class _Namespace:
    url: str
    target_id: str
    generation: str
    client: redis_lib.Redis


def _delete_namespace(namespace: _Namespace) -> None:
    """Remove only this run's random namespace — never anything else."""
    keys = list(
        namespace.client.scan_iter(match=namespace_scan_pattern(namespace.target_id))
    )
    if keys:
        namespace.client.delete(*keys)


@contextlib.contextmanager
def _fresh_namespace(url: str) -> Iterator[_Namespace]:
    """A random test namespace with an explicitly initialized sentinel."""
    target_id = f"it-mgrsync-{uuid.uuid4().hex[:12]}"
    generation = f"gen-{uuid.uuid4().hex[:12]}"
    client = redis_lib.Redis.from_url(url)
    # Ops-side explicit init only (Task 10 owns the real drain/init flow):
    # normal coordinator operation must never auto-create the sentinel.
    client.set(coordination_keys(target_id, _KEY).generation, generation)
    namespace = _Namespace(
        url=url, target_id=target_id, generation=generation, client=client
    )
    try:
        yield namespace
    finally:
        _delete_namespace(namespace)
        client.close()


def _make_coordinator(namespace: _Namespace) -> RedisManagerSyncCoordinator:
    """A coordinator over its own independent client, like a second pod."""
    return RedisManagerSyncCoordinator(
        target_id=namespace.target_id,
        generation=namespace.generation,
        client=redis_lib.Redis.from_url(namespace.url),
    )


async def _drive_send(
    coordinator,
    bcs: RecordingBcsManagersPlugin,
    key: SyncKey,
    *,
    holder: str,
    attempt_id: str,
    ttl_seconds: int = _LEASE_TTL,
):
    """Inline consumer spine: claim → begin → send → finish → release.

    The real consumer/dispatcher arrives in Task 5; this helper only
    sequences the coordinator contract so this suite can prove that a
    refused claim means zero deliveries to BCS.
    """
    snapshot = build_snapshot(SyncFacts(key, "bot", "entity", "owner", ()))
    claim = coordinator.claim(key, holder=holder, ttl_seconds=ttl_seconds)
    if claim.lease is None:
        return claim.status, None
    attempt = coordinator.begin(
        claim.lease, attempt_id=attempt_id, digest=snapshot.digest
    )
    change = await bcs.replace_managers(snapshot, timeout_seconds=5)
    coordinator.finish(attempt)
    coordinator.release(claim.lease)
    return claim.status, change


@pytest.fixture
def shared_coordination():
    """Two independent clients over one random namespace — (a, b, expire).

    The memory variant of this fixture lives in the unit suite; the two
    named fixture families must stay behaviorally identical.
    """
    url = _test_redis_url_or_skip()
    with _fresh_namespace(url) as namespace:
        a = _make_coordinator(namespace)
        b = _make_coordinator(namespace)

        def expire_lease(key: SyncKey = _KEY) -> None:
            # Leases expire by the Redis server clock, which a test cannot
            # advance; deleting exactly the lease key produces the same
            # observable state a TTL expiry leaves behind (lease absent,
            # markers untouched) without sleeping a production TTL.
            namespace.client.delete(coordination_keys(namespace.target_id, key).lease)

        yield a, b, expire_lease
        a._redis.close()
        b._redis.close()


@pytest.mark.asyncio
async def test_missing_generation_blocks_send():
    url = _test_redis_url_or_skip()
    with _fresh_namespace(url) as namespace:
        # The namespace's generation sentinel is gone (never initialized,
        # or flushed away): the whole namespace is "lost" and every
        # sender on it must stop.
        namespace.client.delete(coordination_keys(namespace.target_id, _KEY).generation)
        a = _make_coordinator(namespace)
        b = _make_coordinator(namespace)
        bcs = RecordingBcsManagersPlugin()

        with pytest.raises(CoordinationUnavailable):
            await _drive_send(a, bcs, _KEY, holder="a", attempt_id="attempt-a")
        with pytest.raises(CoordinationUnavailable):
            await _drive_send(
                b, bcs, _OTHER_TENANT_KEY, holder="b", attempt_id="attempt-b"
            )
        # Claim refusal meant refusal to send: zero BCS calls, on both
        # coordinators.
        assert bcs.calls == []
        # Refused operations never auto-rebuilt the sentinel...
        assert (
            namespace.client.get(
                coordination_keys(namespace.target_id, _KEY).generation
            )
            is None
        )
        # ...and reading the namespace state also refuses, never reports
        # a fake "nothing blocked".
        with pytest.raises(CoordinationUnavailable):
            a.read_blocked(_KEY)

        a._redis.close()
        b._redis.close()


def test_lease_expiry_by_server_clock_is_reclaimable():
    url = _test_redis_url_or_skip()
    with _fresh_namespace(url) as namespace:
        a = _make_coordinator(namespace)
        b = _make_coordinator(namespace)
        try:
            assert (
                a.claim(_KEY, holder="a", ttl_seconds=1).status is ClaimStatus.ACQUIRED
            )
            # A deliberately shortened test TTL plus the one real sleep in
            # this suite: Redis itself expires the lease key.
            time.sleep(1.3)
            assert (
                b.claim(_KEY, holder="b", ttl_seconds=_LEASE_TTL).status
                is ClaimStatus.ACQUIRED
            )
        finally:
            a._redis.close()
            b._redis.close()


def test_real_eval_refusals_and_cas_semantics():
    url = _test_redis_url_or_skip()
    with _fresh_namespace(url) as namespace:
        a = _make_coordinator(namespace)
        b = _make_coordinator(namespace)
        try:
            acquired = a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
            assert acquired.status is ClaimStatus.ACQUIRED
            attempt = a.begin(acquired.lease, attempt_id="attempt-a", digest="digest-a")
            # BEGIN's atomic ordering, for real: marker present → refuse.
            with pytest.raises(CoordinationLost):
                a.begin(acquired.lease, attempt_id="attempt-b", digest="digest-b")
            # Lease not held by this holder → refuse.
            with pytest.raises(CoordinationLost):
                b.begin(Lease(_KEY, "b"), attempt_id="attempt-b", digest="digest-b")
            # Wrong holder renew / release → False, state intact.
            assert b.renew(Lease(_KEY, "b"), ttl_seconds=_LEASE_TTL) is False
            assert b.release(Lease(_KEY, "b")) is False
            assert a.renew(acquired.lease, ttl_seconds=_LEASE_TTL) is True
            # finish is a CAS on the whole serialized attempt: a foreign
            # attempt (any of holder / attempt id / digest differing)
            # cannot delete the marker.
            foreign = Attempt(
                lease=Lease(_KEY, "b"), attempt_id="attempt-a", digest="digest-a"
            )
            assert b.finish(foreign) is False
            tampered = Attempt(
                lease=attempt.lease, attempt_id="attempt-a", digest="digest-x"
            )
            assert a.finish(tampered) is False
            # The marker is still there; only the holder's own exact
            # attempt can clear it.
            assert a.read_blocked(_KEY) == "attempt-a"
            assert a.finish(attempt) is True
            assert a.read_blocked(_KEY) is None
            # release compare-deletes the lease; a second release is False.
            assert a.release(acquired.lease) is True
            assert a.release(acquired.lease) is False
            assert b.read_blocked(_KEY) is None
        finally:
            a._redis.close()
            b._redis.close()


def test_generation_mismatch_stops_the_sender():
    url = _test_redis_url_or_skip()
    with _fresh_namespace(url) as namespace:
        rogue = RedisManagerSyncCoordinator(
            target_id=namespace.target_id,
            generation="stale-generation",
            client=redis_lib.Redis.from_url(namespace.url),
        )
        try:
            with pytest.raises(CoordinationUnavailable):
                rogue.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
            with pytest.raises(CoordinationUnavailable):
                rogue.hold_owner_check(_KEY)
            with pytest.raises(CoordinationUnavailable):
                rogue.read_blocked(_KEY)
            # The legitimate generation still works and no rogue
            # operation disturbed the namespace.
            honest = _make_coordinator(namespace)
            try:
                assert (
                    honest.claim(_KEY, holder="h", ttl_seconds=_LEASE_TTL).status
                    is ClaimStatus.ACQUIRED
                )
            finally:
                honest._redis.close()
        finally:
            rogue._redis.close()

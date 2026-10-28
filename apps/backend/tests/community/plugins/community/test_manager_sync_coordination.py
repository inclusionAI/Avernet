"""Unit tests for the strict manager-sync coordination boundary.

Two halves, per the Task 3 brief:

- **Interleavings** against the reference-semantics
  ``MemoryManagerSyncCoordinator`` through a ``shared_coordination``
  fixture: two coordinators over one shared backend state plus an
  injectable clock, so a "lease expiry" is a deterministic clock step
  — never a sleep.
- **Error classification** of the PROD ``RedisManagerSyncCoordinator``
  against a scripted EVAL client: each Lua script's return code maps
  to the contract's exception / result vocabulary, and every
  infrastructure failure raises ``CoordinationUnavailable``. Real
  ``EVAL`` semantics are verified by the integration suite
  (``tests/community/integration/test_manager_sync_redis.py``).

Key-layout facts (hash-tag clustering, tenant/env isolation) are
asserted here by construction; the integration suite re-checks them
against a live server where it matters.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest
import redis as redis_lib

from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.plugin_api.impl_registry import IMPL_REGISTRY, Mode
from agentclaw.community.plugin_api.manager_sync_coordination import (
    Attempt,
    ClaimStatus,
    CoordinationLost,
    CoordinationUnavailable,
    Lease,
    ManagerSyncCoordinator,
)
from agentclaw.community.plugins.community import manager_sync_scripts as scripts
from agentclaw.community.plugins.community.manager_sync_coordination import (
    RedisManagerSyncCoordinator,
    bot_key_suffix,
    coordination_keys,
    namespace_hash_tag,
    namespace_scan_pattern,
)
from agentclaw.community.plugins.local.manager_sync_coordination import (
    MemoryManagerSyncCoordinator,
)

_KEY = SyncKey("tenant-a", "dev", 7)
_OTHER_TENANT_KEY = SyncKey("tenant-b", "dev", 7)
_OTHER_ENV_KEY = SyncKey("tenant-a", "prod", 7)
_TARGET_ID = "unit-target"
_GENERATION = "unit-generation"
_LEASE_TTL = 30


# ---------------------------------------------------------------------------
# shared_coordination — memory variant (the integration suite redefines this
# same fixture over two real Redis clients; the named interleavings below run
# unmodified against both).
# ---------------------------------------------------------------------------


class _FakeClock:
    """Monotonic test clock the fixture can advance deterministically."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_shared_memory():
    """Two coordinators over ONE substrate state + a controllable clock."""
    clock = _FakeClock()
    a = MemoryManagerSyncCoordinator(clock=clock)
    b = MemoryManagerSyncCoordinator(clock=clock)
    # The fake keeps its substrate as instance state; rebind so both
    # "clients" see the same lease/marker store under the same mutex.
    b._lock = a._lock
    b._leases = a._leases
    b._inflight = a._inflight
    b._owner_holds = a._owner_holds

    def expire_lease(key: SyncKey = _KEY) -> None:
        clock.advance(_LEASE_TTL * 2)

    return a, b, expire_lease


@pytest.fixture
def shared_coordination():
    return _make_shared_memory()


def test_inflight_survives_lease_expiry(shared_coordination):
    # fixture returns (coordinator_a, coordinator_b, advance_lease_clock)
    a, b, expire_lease = shared_coordination
    key = SyncKey("tenant-a", "dev", 7)
    claim = a.claim(key, holder="a", ttl_seconds=30)
    assert claim.status is ClaimStatus.ACQUIRED
    attempt = a.begin(claim.lease, attempt_id="attempt-a", digest="digest-a")
    expire_lease()
    assert b.claim(key, holder="b", ttl_seconds=30).status is ClaimStatus.BLOCKED
    assert b.clear_confirmed(key, attempt_id="wrong-attempt") is False
    assert a.read_blocked(key) == attempt.attempt_id


def test_claim_matrix(shared_coordination):
    a, b, _ = shared_coordination
    acquired = a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    assert acquired.status is ClaimStatus.ACQUIRED
    assert acquired.lease == Lease(_KEY, "a")
    # Live lease held by another holder → BUSY.
    assert b.claim(_KEY, holder="b", ttl_seconds=_LEASE_TTL).status is ClaimStatus.BUSY
    # A marker outranks a live lease for a would-be claimer: BLOCKED.
    attempt = a.begin(acquired.lease, attempt_id="attempt-a", digest="digest-a")
    assert (
        b.claim(_KEY, holder="b", ttl_seconds=_LEASE_TTL).status is ClaimStatus.BLOCKED
    )
    # Resolve the marker → still BUSY (a's lease survives the marker).
    assert a.clear_confirmed(_KEY, attempt_id=attempt.attempt_id) is True
    assert b.claim(_KEY, holder="b", ttl_seconds=_LEASE_TTL).status is ClaimStatus.BUSY
    # Release → ACQUIRED.
    assert a.release(acquired.lease) is True
    assert (
        b.claim(_KEY, holder="b", ttl_seconds=_LEASE_TTL).status is ClaimStatus.ACQUIRED
    )


def test_expired_lease_is_reclaimable(shared_coordination):
    a, b, expire_lease = shared_coordination
    assert (
        a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL).status is ClaimStatus.ACQUIRED
    )
    expire_lease()
    # No markers: the expired lease no longer blocks a new claim.
    reclaimed = b.claim(_KEY, holder="b", ttl_seconds=_LEASE_TTL)
    assert reclaimed.status is ClaimStatus.ACQUIRED
    # The stale holder cannot renew or release the reclaimed lease.
    stale = Lease(_KEY, "a")
    assert a.renew(stale, ttl_seconds=_LEASE_TTL) is False
    assert a.release(stale) is False


def test_renew_only_by_holder_before_expiry(shared_coordination):
    a, b, _ = shared_coordination
    acquired = a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    assert b.renew(Lease(_KEY, "b"), ttl_seconds=_LEASE_TTL) is False
    assert a.renew(acquired.lease, ttl_seconds=_LEASE_TTL) is True


def test_release_compare_deletes_lease_not_markers(shared_coordination):
    a, b, _ = shared_coordination
    acquired = a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    attempt = a.begin(acquired.lease, attempt_id="attempt-a", digest="digest-a")
    assert b.hold_owner_check(_KEY) == "owner-hold:tenant-a:dev:7"
    assert b.release(Lease(_KEY, "b")) is False
    assert a.release(acquired.lease) is True
    # Both markers survive the holder's own release untouched.
    assert a.read_blocked(_KEY) == attempt.attempt_id


def test_owner_hold_is_idempotent_and_independent_of_inflight(shared_coordination):
    a, b, _ = shared_coordination
    acquired = a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    attempt = a.begin(acquired.lease, attempt_id="attempt-a", digest="digest-a")
    hold_id = b.hold_owner_check(_KEY)
    assert hold_id == "owner-hold:tenant-a:dev:7"
    # Idempotent: a second hold returns the same marker, never a new one.
    assert b.hold_owner_check(_KEY) == hold_id
    # The hold never overwrote the in-flight record.
    assert a.read_blocked(_KEY) == attempt.attempt_id
    # An owner hold arriving between another holder's claim and begin
    # refuses the begin: an owner's live check cannot be starved by
    # long-running sync traffic.
    other = b.claim(_OTHER_TENANT_KEY, holder="b", ttl_seconds=_LEASE_TTL)
    assert other.status is ClaimStatus.ACQUIRED
    assert a.hold_owner_check(_OTHER_TENANT_KEY) == "owner-hold:tenant-b:dev:7"
    with pytest.raises(CoordinationLost):
        b.begin(other.lease, attempt_id="attempt-b", digest="digest-b")


def test_finishing_an_attempt_never_clears_owner_hold(shared_coordination):
    a, b, _ = shared_coordination
    acquired = a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    attempt = a.begin(acquired.lease, attempt_id="attempt-a", digest="digest-a")
    hold_id = b.hold_owner_check(_KEY)
    assert a.finish(attempt) is True
    # The completed in-flight record is gone; the owner hold survives it.
    assert a.read_blocked(_KEY) == hold_id
    assert (
        a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL).status is ClaimStatus.BLOCKED
    )


def test_late_finish_cannot_clear_new_state(shared_coordination):
    a, _, _ = shared_coordination
    acquired = a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    first = a.begin(acquired.lease, attempt_id="attempt-a", digest="digest-a")
    # Controlled recovery removed the stale record; a new attempt began.
    assert a.clear_confirmed(_KEY, attempt_id="attempt-a") is True
    second = a.begin(acquired.lease, attempt_id="attempt-b", digest="digest-b")
    # The late worker's finish (old attempt) must not delete the new state.
    assert a.finish(first) is False
    assert a.read_blocked(_KEY) == second.attempt_id
    # Holder mismatch → False even with the right attempt id...
    foreign = Attempt(lease=Lease(_KEY, "b"), attempt_id="attempt-b", digest="digest-b")
    assert a.finish(foreign) is False
    # ...and digest mismatch → False even with the right holder (CAS on
    # the whole serialized attempt, the same compare the Redis impl uses).
    assert a.finish(second) is True
    assert a.read_blocked(_KEY) is None


def test_finish_cas_includes_digest(shared_coordination):
    a, _, _ = shared_coordination
    acquired = a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    attempt = a.begin(acquired.lease, attempt_id="attempt-a", digest="digest-a")
    tampered = Attempt(
        lease=attempt.lease, attempt_id="attempt-a", digest="digest-tampered"
    )
    assert a.finish(tampered) is False
    assert a.read_blocked(_KEY) == attempt.attempt_id


def test_clear_confirmed_clears_exactly_one_matching_marker(shared_coordination):
    a, b, _ = shared_coordination
    acquired = a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    attempt = a.begin(acquired.lease, attempt_id="attempt-a", digest="digest-a")
    hold_id = b.hold_owner_check(_KEY)
    # Both families coexist; clearing one leaves the other readable.
    assert b.clear_confirmed(_KEY, attempt_id=hold_id) is True
    assert a.read_blocked(_KEY) == attempt.attempt_id
    assert b.clear_confirmed(_KEY, attempt_id="attempt-a") is True
    assert a.read_blocked(_KEY) is None
    # Already removed / never existed → False, never an error.
    assert b.clear_confirmed(_KEY, attempt_id="attempt-a") is False


def test_memory_generation_missing_stops_every_operation():
    gone = MemoryManagerSyncCoordinator(generation=None)
    lease = Lease(_KEY, "a")
    attempt = Attempt(lease=lease, attempt_id="attempt-a", digest="digest-a")
    with pytest.raises(CoordinationUnavailable):
        gone.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    with pytest.raises(CoordinationUnavailable):
        gone.renew(lease, ttl_seconds=_LEASE_TTL)
    with pytest.raises(CoordinationUnavailable):
        gone.begin(lease, attempt_id="attempt-a", digest="digest-a")
    with pytest.raises(CoordinationUnavailable):
        gone.finish(attempt)
    with pytest.raises(CoordinationUnavailable):
        gone.release(lease)
    with pytest.raises(CoordinationUnavailable):
        gone.read_blocked(_KEY)
    with pytest.raises(CoordinationUnavailable):
        gone.clear_confirmed(_KEY, attempt_id="attempt-a")
    with pytest.raises(CoordinationUnavailable):
        gone.hold_owner_check(_KEY)


# ---------------------------------------------------------------------------
# Redis implementation — scripted-EVAL classification, no live server here.
# ---------------------------------------------------------------------------


class _ScriptedRedis:
    """EVAL/MGET stub: each Lua script maps to a scripted result.

    ``results[name]`` is the value ``eval`` returns (or the exception it
    raises); ``fail_with`` simulates an unreachable backend for every
    command. KEYS/ARGV are recorded so tests can assert wire arguments.
    """

    SCRIPT_NAMES = {
        scripts.CLAIM: "claim",
        scripts.BEGIN: "begin",
        scripts.FINISH: "finish",
        scripts.RENEW: "renew",
        scripts.RELEASE: "release",
        scripts.CLEAR_CONFIRMED: "clear_confirmed",
        scripts.HOLD_OWNER_CHECK: "hold_owner_check",
    }

    def __init__(self) -> None:
        self.results: dict[str, object] = {}
        self.eval_calls: list[tuple[str, int, tuple]] = []
        self.mget_result: list | None = None
        self.fail_with: Exception | None = None

    def eval(self, script, numkeys, *keys_and_args):
        if self.fail_with is not None:
            raise self.fail_with
        name = self.SCRIPT_NAMES[script]
        self.eval_calls.append((name, numkeys, keys_and_args))
        result = self.results[name]
        if isinstance(result, Exception):
            raise result
        return result

    def mget(self, *keys):
        if self.fail_with is not None:
            raise self.fail_with
        return self.mget_result


def _make_redis(client: _ScriptedRedis) -> RedisManagerSyncCoordinator:
    return RedisManagerSyncCoordinator(
        target_id=_TARGET_ID,
        generation=_GENERATION,
        client=client,
    )


def test_redis_claim_script_result_classification():
    client = _ScriptedRedis()
    a = _make_redis(client)
    keys = coordination_keys(_TARGET_ID, _KEY)
    client.results["claim"] = 1
    acquired = a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    assert acquired.status is ClaimStatus.ACQUIRED
    assert acquired.lease == Lease(_KEY, "a")
    client.results["claim"] = 2
    assert a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL).status is ClaimStatus.BUSY
    client.results["claim"] = 0
    assert (
        a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL).status is ClaimStatus.BLOCKED
    )
    client.results["claim"] = -2
    with pytest.raises(CoordinationUnavailable):
        a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    client.results["claim"] = 7  # unknown code is still an infra failure
    with pytest.raises(CoordinationUnavailable):
        a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    # KEYS/ARGV: lease, inflight, owner_hold, generation; holder, ttl, generation.
    client.results["claim"] = 1
    a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    name, numkeys, args = client.eval_calls[-1]
    assert name == "claim" and numkeys == 4
    assert args == (
        keys.lease,
        keys.inflight,
        keys.owner_hold,
        keys.generation,
        "a",
        _LEASE_TTL,
        _GENERATION,
    )


def test_redis_begin_script_result_classification():
    client = _ScriptedRedis()
    a = _make_redis(client)
    keys = coordination_keys(_TARGET_ID, _KEY)
    lease = Lease(_KEY, "a")
    client.results["begin"] = 1
    attempt = a.begin(lease, attempt_id="attempt-a", digest="digest-a")
    assert attempt == Attempt(lease=lease, attempt_id="attempt-a", digest="digest-a")
    client.results["begin"] = -1
    with pytest.raises(CoordinationLost):
        a.begin(lease, attempt_id="attempt-a", digest="digest-a")
    client.results["begin"] = 0
    with pytest.raises(CoordinationLost):
        a.begin(lease, attempt_id="attempt-a", digest="digest-a")
    client.results["begin"] = -2
    with pytest.raises(CoordinationUnavailable):
        a.begin(lease, attempt_id="attempt-a", digest="digest-a")
    client.results["begin"] = 5
    with pytest.raises(CoordinationUnavailable):
        a.begin(lease, attempt_id="attempt-a", digest="digest-a")
    # KEYS/ARGV: lease, inflight, owner_hold, generation; holder,
    # serialized attempt, generation.
    client.results["begin"] = 1
    a.begin(lease, attempt_id="attempt-a", digest="digest-a")
    name, numkeys, args = client.eval_calls[-1]
    assert name == "begin" and numkeys == 4
    assert args[:4] == (keys.lease, keys.inflight, keys.owner_hold, keys.generation)
    assert args[4] == "a"
    assert json.loads(args[5]) == {
        "attempt_id": "attempt-a",
        "digest": "digest-a",
        "holder": "a",
    }
    assert args[6] == _GENERATION


def test_redis_finish_script_result_classification():
    client = _ScriptedRedis()
    a = _make_redis(client)
    attempt = Attempt(lease=Lease(_KEY, "a"), attempt_id="attempt-a", digest="digest-a")
    client.results["finish"] = 1
    assert a.finish(attempt) is True
    client.results["finish"] = 0
    assert a.finish(attempt) is False
    client.results["finish"] = -2
    with pytest.raises(CoordinationUnavailable):
        a.finish(attempt)
    client.results["finish"] = 3
    with pytest.raises(CoordinationUnavailable):
        a.finish(attempt)


def test_redis_renew_and_release_script_result_classification():
    client = _ScriptedRedis()
    a = _make_redis(client)
    lease = Lease(_KEY, "a")
    keys = coordination_keys(_TARGET_ID, _KEY)
    client.results["renew"] = 1
    assert a.renew(lease, ttl_seconds=_LEASE_TTL) is True
    client.results["renew"] = 0
    assert a.renew(lease, ttl_seconds=_LEASE_TTL) is False
    client.results["renew"] = -2
    with pytest.raises(CoordinationUnavailable):
        a.renew(lease, ttl_seconds=_LEASE_TTL)
    client.results["release"] = 1
    assert a.release(lease) is True
    client.results["release"] = 0
    assert a.release(lease) is False
    client.results["release"] = -2
    with pytest.raises(CoordinationUnavailable):
        a.release(lease)
    # Release touches ONLY the lease (plus the sentinel) — markers are
    # never passed to it.
    client.results["release"] = 1
    a.release(lease)
    name, numkeys, args = client.eval_calls[-1]
    assert name == "release" and numkeys == 2
    assert args == (keys.lease, keys.generation, "a", _GENERATION)


def test_redis_clear_confirmed_script_result_classification():
    client = _ScriptedRedis()
    a = _make_redis(client)
    client.results["clear_confirmed"] = 1
    assert a.clear_confirmed(_KEY, attempt_id="attempt-a") is True
    client.results["clear_confirmed"] = 0
    assert a.clear_confirmed(_KEY, attempt_id="attempt-a") is False
    client.results["clear_confirmed"] = -2
    with pytest.raises(CoordinationUnavailable):
        a.clear_confirmed(_KEY, attempt_id="attempt-a")


def test_redis_hold_owner_check_script_result_classification():
    client = _ScriptedRedis()
    a = _make_redis(client)
    hold_id = "owner-hold:tenant-a:dev:7"
    client.results["hold_owner_check"] = hold_id.encode("utf-8")
    assert a.hold_owner_check(_KEY) == hold_id
    client.results["hold_owner_check"] = hold_id
    assert a.hold_owner_check(_KEY) == hold_id
    client.results["hold_owner_check"] = -2
    with pytest.raises(CoordinationUnavailable):
        a.hold_owner_check(_KEY)
    client.results["hold_owner_check"] = 0
    with pytest.raises(CoordinationUnavailable):
        a.hold_owner_check(_KEY)


def test_redis_read_blocked_precedence_and_generation_guard():
    client = _ScriptedRedis()
    a = _make_redis(client)
    inflight = json.dumps(
        {"attempt_id": "attempt-a", "digest": "digest-a", "holder": "a"}
    ).encode("utf-8")
    hold_id = b"owner-hold:tenant-a:dev:7"
    gen = _GENERATION.encode("utf-8")
    # In-flight id wins over the owner hold.
    client.mget_result = [gen, inflight, hold_id]
    assert a.read_blocked(_KEY) == "attempt-a"
    # No in-flight → the owner-hold marker id.
    client.mget_result = [gen, None, hold_id]
    assert a.read_blocked(_KEY) == "owner-hold:tenant-a:dev:7"
    # Neither marker → None (a successful read of a clean namespace).
    client.mget_result = [gen, None, None]
    assert a.read_blocked(_KEY) is None
    # Sentinel missing / mismatched → stop the sender, never None.
    client.mget_result = [None, None, None]
    with pytest.raises(CoordinationUnavailable):
        a.read_blocked(_KEY)
    client.mget_result = [b"other-generation", inflight, hold_id]
    with pytest.raises(CoordinationUnavailable):
        a.read_blocked(_KEY)
    # A corrupt in-flight record is an infra failure, not "no marker".
    client.mget_result = [gen, b"not-json", hold_id]
    with pytest.raises(CoordinationUnavailable):
        a.read_blocked(_KEY)
    # Backend unreachable → CoordinationUnavailable.
    client.fail_with = redis_lib.exceptions.ConnectionError("down")
    with pytest.raises(CoordinationUnavailable):
        a.read_blocked(_KEY)


def test_redis_infra_failures_raise_coordination_unavailable():
    client = _ScriptedRedis()
    client.fail_with = redis_lib.exceptions.ConnectionError("down")
    a = _make_redis(client)
    lease = Lease(_KEY, "a")
    attempt = Attempt(lease=lease, attempt_id="attempt-a", digest="digest-a")
    with pytest.raises(CoordinationUnavailable):
        a.claim(_KEY, holder="a", ttl_seconds=_LEASE_TTL)
    with pytest.raises(CoordinationUnavailable):
        a.renew(lease, ttl_seconds=_LEASE_TTL)
    with pytest.raises(CoordinationUnavailable):
        a.begin(lease, attempt_id="attempt-a", digest="digest-a")
    with pytest.raises(CoordinationUnavailable):
        a.finish(attempt)
    with pytest.raises(CoordinationUnavailable):
        a.release(lease)
    with pytest.raises(CoordinationUnavailable):
        a.clear_confirmed(_KEY, attempt_id="attempt-a")
    with pytest.raises(CoordinationUnavailable):
        a.hold_owner_check(_KEY)


def test_redis_constructor_requires_config_and_backend():
    with pytest.raises(ValueError):
        RedisManagerSyncCoordinator(
            target_id="", generation=_GENERATION, client=_ScriptedRedis()
        )
    with pytest.raises(ValueError):
        RedisManagerSyncCoordinator(
            target_id=_TARGET_ID, generation="", client=_ScriptedRedis()
        )
    with pytest.raises(ValueError):
        RedisManagerSyncCoordinator(target_id=_TARGET_ID, generation=_GENERATION)


# ---------------------------------------------------------------------------
# Key layout
# ---------------------------------------------------------------------------


def _tag_of(key: str) -> str:
    match = re.search(r"\{[0-9a-f]{64}\}", key)
    assert match is not None, f"no hash tag in key: {key}"
    return match.group(0)


def test_all_coordination_keys_share_one_hash_tag():
    expected_tag = "{" + hashlib.sha256(_TARGET_ID.encode("utf-8")).hexdigest() + "}"
    assert namespace_hash_tag(_TARGET_ID) == expected_tag
    for key in (_KEY, _OTHER_TENANT_KEY, _OTHER_ENV_KEY, SyncKey("t", "e", 999999)):
        keys = coordination_keys(_TARGET_ID, key)
        for name in ("generation", "lease", "inflight", "owner_hold"):
            assert _tag_of(getattr(keys, name)) == expected_tag, name
    # Same target + bot → deterministic keys (target_id comes from config,
    # never re-randomized per pod).
    assert coordination_keys(_TARGET_ID, _KEY) == coordination_keys(_TARGET_ID, _KEY)
    # The cleanup pattern covers the whole namespace and nothing else.
    assert namespace_scan_pattern(_TARGET_ID) == f"mgrsync:v1:{expected_tag}:*"


def test_same_bot_pk_across_tenant_and_env_gets_distinct_keys():
    base = coordination_keys(_TARGET_ID, _KEY)
    other_tenant = coordination_keys(_TARGET_ID, _OTHER_TENANT_KEY)
    other_env = coordination_keys(_TARGET_ID, _OTHER_ENV_KEY)
    assert base.lease != other_tenant.lease
    assert base.inflight != other_tenant.inflight
    assert base.owner_hold != other_tenant.owner_hold
    assert base.lease != other_env.lease
    assert base.inflight != other_env.inflight
    assert base.owner_hold != other_env.owner_hold
    canonical = "\x1f".join(("tenant-a", "dev", "7"))
    assert bot_key_suffix(_KEY) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_generation_sentinel_key_is_namespace_scoped():
    keys_a = coordination_keys(_TARGET_ID, _KEY)
    keys_b = coordination_keys(_TARGET_ID, _OTHER_TENANT_KEY)
    # One sentinel per namespace (target), not per bot.
    assert keys_a.generation == keys_b.generation
    assert (
        keys_a.generation == f"mgrsync:v1:{namespace_hash_tag(_TARGET_ID)}:generation"
    )


# ---------------------------------------------------------------------------
# Registration — the prod side of the Task 1 local/prod pair.
# ---------------------------------------------------------------------------


def test_registered_as_prod_impl_against_the_protocol():
    entries = [
        entry for entry in IMPL_REGISTRY if entry.cls is RedisManagerSyncCoordinator
    ]
    assert len(entries) == 1
    assert entries[0].mode is Mode.PROD
    assert entries[0].protocol is ManagerSyncCoordinator
    assert entries[0].flavor is None

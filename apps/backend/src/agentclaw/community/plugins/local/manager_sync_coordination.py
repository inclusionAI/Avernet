"""MemoryManagerSyncCoordinator — test-only in-memory coordination fake.

Simulates the strict coordination boundary (the later Redis
implementation) inside one instance's mutex. Declared test-only and
NOT a production multi-process implementation: two services may share
one instance to simulate contention, but there is no cross-process
state here. Use the composition root's prod plugin for anything real.

State model per ``SyncKey``, mirroring the Redis layout the contract
describes:

- one exclusive lease with a holder token and an expiry
  (SET-NX-EX style — re-claimable once expired);
- one TTL-less in-flight record written by ``begin`` under the lease —
  it outlives lease expiry by design;
- one independent owner-hold marker with its own id — coexisting with
  (never overwriting) an in-flight record and surviving lease expiry.

A namespace generation stands in for the Redis generation sentinel:
constructing the fake initializes it (here, construction *is* the
namespace lifecycle); pass ``generation=None`` to model a lost /
never-initialized namespace, which makes every operation raise
``CoordinationUnavailable`` — the same stop-the-sender behavior the
real deployment needs.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.manager_sync_coordination import (
    Attempt,
    ClaimResult,
    ClaimStatus,
    CoordinationLost,
    CoordinationUnavailable,
    Lease,
    ManagerSyncCoordinator,
)
from agentclaw.community.plugins.local._mock_seam import MockSeam


@dataclass
class _LeaseRecord:
    holder: str
    expires_at: float


@dataclass
class _InflightRecord:
    holder: str
    attempt_id: str
    digest: str


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="isolated manager projection/coordination, no network",
)
class MemoryManagerSyncCoordinator(MockSeam, ManagerSyncCoordinator):
    """In-memory lease/marker simulation under a single mutex."""

    def __init__(
        self,
        generation: str | None = "memory-generation",
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._generation = generation
        self._leases: dict[SyncKey, _LeaseRecord] = {}
        self._inflight: dict[SyncKey, _InflightRecord] = {}
        self._owner_holds: dict[SyncKey, str] = {}
        self._clock = clock or time.monotonic

    # -- helpers -------------------------------------------------------

    def _require_generation(self) -> None:
        if self._generation is None:
            raise CoordinationUnavailable("generation-sentinel-missing")

    def _has_marker(self, key: SyncKey) -> bool:
        return key in self._inflight or key in self._owner_holds

    def _lease_valid(self, lease: Lease) -> bool:
        record = self._leases.get(lease.key)
        if record is None or record.holder != lease.holder:
            return False
        if record.expires_at <= self._clock():
            self._leases.pop(lease.key, None)
            return False
        return True

    # -- ManagerSyncCoordinator ---------------------------------------

    def claim(self, key: SyncKey, *, holder: str, ttl_seconds: int) -> ClaimResult:
        with self._lock:
            self._require_generation()
            if self._has_marker(key):
                return ClaimResult(status=ClaimStatus.BLOCKED, lease=None)
            now = self._clock()
            existing = self._leases.get(key)
            if existing is not None and existing.expires_at > now:
                return ClaimResult(status=ClaimStatus.BUSY, lease=None)
            self._leases[key] = _LeaseRecord(holder=holder, expires_at=now + ttl_seconds)
            return ClaimResult(status=ClaimStatus.ACQUIRED, lease=Lease(key=key, holder=holder))

    def renew(self, lease: Lease, *, ttl_seconds: int) -> bool:
        with self._lock:
            self._require_generation()
            if not self._lease_valid(lease):
                return False
            self._leases[lease.key].expires_at = self._clock() + ttl_seconds
            return True

    def begin(self, lease: Lease, *, attempt_id: str, digest: str) -> Attempt:
        with self._lock:
            self._require_generation()
            if not self._lease_valid(lease):
                raise CoordinationLost(f"lease not held by {lease.holder!r}")
            if self._has_marker(lease.key):
                raise CoordinationLost(
                    f"marker present for bot_pk={lease.key.bot_pk}; cannot begin"
                )
            self._inflight[lease.key] = _InflightRecord(
                holder=lease.holder, attempt_id=attempt_id, digest=digest
            )
            return Attempt(lease=lease, attempt_id=attempt_id, digest=digest)

    def finish(self, attempt: Attempt) -> bool:
        with self._lock:
            self._require_generation()
            record = self._inflight.get(attempt.lease.key)
            if (
                record is None
                or record.attempt_id != attempt.attempt_id
                or record.holder != attempt.lease.holder
                or record.digest != attempt.digest
            ):
                return False
            del self._inflight[attempt.lease.key]
            return True

    def release(self, lease: Lease) -> bool:
        with self._lock:
            self._require_generation()
            record = self._leases.get(lease.key)
            if record is None or record.holder != lease.holder:
                return False
            del self._leases[lease.key]
            return True

    def read_blocked(self, key: SyncKey) -> str | None:
        with self._lock:
            self._require_generation()
            inflight = self._inflight.get(key)
            if inflight is not None:
                return inflight.attempt_id
            owner_hold = self._owner_holds.get(key)
            if owner_hold is not None:
                return owner_hold
            return None

    def clear_confirmed(self, key: SyncKey, *, attempt_id: str) -> bool:
        with self._lock:
            self._require_generation()
            inflight = self._inflight.get(key)
            if inflight is not None and inflight.attempt_id == attempt_id:
                del self._inflight[key]
                return True
            if self._owner_holds.get(key) == attempt_id:
                del self._owner_holds[key]
                return True
            return False

    def hold_owner_check(self, key: SyncKey) -> str:
        with self._lock:
            self._require_generation()
            existing = self._owner_holds.get(key)
            if existing is not None:
                return existing
            hold_id = f"owner-hold:{key.avernet_tenant}:{key.env}:{key.bot_pk}"
            self._owner_holds[key] = hold_id
            return hold_id
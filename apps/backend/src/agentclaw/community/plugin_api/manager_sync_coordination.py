"""ManagerSyncCoordinator — the strict shared coordination boundary.

Keeps two Deployments from double-syncing the same Bot and keeps an
Owner's live check from being starved by long-running sync traffic.
The contract is deliberately narrow and transport-free; the Redis
implementation (later task) composes it from atomic EVAL scripts, the
test-only in-memory simulation lives in ``plugins/local``.

Marker/lease model per ``SyncKey``:

- **Lease** — exclusive, SET-NX-EX style with a holder token; only a
  claim that finds no other lease and no marker acquires it. Expired
  leases may be re-claimed, which is why the markers below carry the
  real safety.
- **In-flight record** — written by ``begin`` under the lease, with no
  TTL: if the process dies mid-sync the record outlives every lease,
  so a later claim is BLOCKED until it is explicitly resolved.
- **Owner hold marker** — created by ``hold_owner_check`` as an
  *independent* marker ID; coexists with (never overwrites) an
  in-flight record, and is not deleted by a finishing sync.

``read_blocked`` returning ``None`` means "the read succeeded and
there is no marker"; an infrastructure failure must raise
``CoordinationUnavailable`` — never a False / None cop-out.
``clear_confirmed`` is a controlled recovery operation, not a
business entry point.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.plugin_api.base import Plugin


class CoordinationUnavailable(Exception):
    """The coordination substrate could not be read or written.

    Callers must treat the state as unknown and stop sync sends for
    the affected scope; never as a benign "not blocked" answer.
    """


class CoordinationLost(Exception):
    """The caller's lease or marker state was taken away mid-flight."""


class ClaimStatus(StrEnum):
    """Why a claim did (not) acquire the exclusive lease."""

    ACQUIRED = "acquired"
    BUSY = "busy"      # another holder's lease is still valid
    BLOCKED = "blocked"  # an in-flight or owner-hold marker says stop


@dataclass(frozen=True)
class Lease:
    """The exclusive right to sync one key, tied to a holder token."""

    key: SyncKey
    holder: str


@dataclass(frozen=True)
class Attempt:
    """An in-flight delivery attempt under a lease."""

    lease: Lease
    attempt_id: str
    digest: str


@dataclass(frozen=True)
class ClaimResult:
    """A claim verdict; only ACQUIRED carries a lease.

    ``lease=None`` is the legitimate "not acquired" state.
    """

    status: ClaimStatus
    lease: Lease | None = None


@runtime_checkable
class ManagerSyncCoordinator(Plugin, Protocol):
    """Serialize manager sync per key and guard Owner checks."""

    def claim(self, key: SyncKey, *, holder: str, ttl_seconds: int) -> ClaimResult:
        """Acquire the exclusive lease for ``key`` (SET-NX-EX)."""
        ...

    def renew(self, lease: Lease, *, ttl_seconds: int) -> bool:
        """Extend the lease TTL — only its current holder succeeds."""
        ...

    def begin(self, lease: Lease, *, attempt_id: str, digest: str) -> Attempt:
        """Open an attempt: atomically check lease + no markers, then
        write the TTL-less in-flight record."""
        ...

    def finish(self, attempt: Attempt) -> bool:
        """CAS-delete the in-flight record only when holder, attempt id
        and digest all match; a late worker must not touch newer state."""
        ...

    def release(self, lease: Lease) -> bool:
        """Compare-delete the lease; markers are never touched."""
        ...

    def read_blocked(self, key: SyncKey) -> str | None:
        """In-flight attempt id if present, else owner-hold marker id,
        else None. Raises CoordinationUnavailable on infra failure."""
        ...

    def clear_confirmed(self, key: SyncKey, *, attempt_id: str) -> bool:
        """Controlled recovery: CAS-delete exactly the one marker whose
        id matches. Not a business entry point."""
        ...

    def hold_owner_check(self, key: SyncKey) -> str:
        """Idempotently ensure the independent owner-hold marker and
        return its id; an existing in-flight record is left alone."""
        ...
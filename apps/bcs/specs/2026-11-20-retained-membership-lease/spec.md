---
status: adopted
date: 2026-11-20
owner: BCS distributed coordination
---

# Retained membership lease

## Scope and authority

This adopted amendment replaces the membership lifetime requirements in
[coordination §3.1](../2026-11-17-worker-coordination/spec.md) and
[Worker phase one](../2026-11-18-worker-runtime-phase1/spec.md).
The owning API is [LeaseStorePlugin](../../crates/plugin-api/bcs-store-api/src/lease.rs).
Owner epochs, immutable specs, session fences, checkpoint protection and local
shutdown/handoff timers keep their existing contracts. This is a pre-merge
consumer/provider change, not an integrated release or production deployment.

## Behavior

Each incarnation owns one record containing registration identity, mutable
heartbeat data, a short deadline, terminal state and one CAS version. A successful
read of an overdue live record authorizes an exact-version terminal CAS. Only
confirmed terminal state proves Dead. A concurrent heartbeat can win first;
the observer must reread after that conflict. Unknown writes/reads and absence
never prove Dead. Retirement can create terminal evidence before first registration.

Registration retries preserve the original payload identity and deadline.
Heartbeats require an existing nonterminal record and the exact version; they
never create missing records. Terminal state cannot become live while retained.
Reserve the final version for termination when ordinary update versions exhaust.
Workers losing registration return with a fresh incarnation and fresh ownership.

Successful first registration, updates and first termination establish a 30-day
storage TTL. Reads, existing-registration retries and repeated retirement do not
restart it. The short heartbeat deadline permits failure detection in seconds;
the storage TTL is garbage retention, not a 30-day failure-detection delay.
The supported replay/recovery horizon is finite: old evidence or requests outside
30 days of inactivity are unsupported. After collection, absence is Unknown.
There is no permanent-tombstone guarantee beyond that horizon.

A provider may assign GC TTL separately from CAS. Failure after a potentially
applied write is OutcomeUnknown, including TTL failure; never report complete
success or non-application. Observing a persistent record repairs its missing TTL.
A crash between creation and TTL assignment can leave an orphan until observed;
the membership index provides discovery, but no immediate orphan-collection SLA
is implied. No premature eviction, deletion, rollback or out-of-contract writer
is allowed in the retained window. A racing TTL refresh may conservatively retain
terminal evidence longer; it cannot change state or revive registration.

## Clock and cost

Providers may use internally synchronized client wall time for persisted deadlines;
server TIME and scripts are not required. Deployment must budget clock skew/steps,
heartbeat interval and network delay. A fast observer can terminate early and a
slow observer can detect late. The exact CAS still serializes heartbeat versus
termination. Consumers subtract the full request duration using monotonic time;
a zero remaining budget grants no local execution authority. Session fencing is
still required, including for work already in flight.

A native JSON mapping needs create-if-absent, consistent read, exact-version
whole-record replacement and storage expiry. A concrete provider must document
its request counts, retry bound and partial-write behavior. These guarantees are
independent of persistent DocumentCas records; their TTL policy does not change.
No latency or capacity improvement is claimed without measurements.

## Compatibility and verification

Capability `RetainedLease` replaces `StrictLease`, requiring a coordinated rebuild.
Use a fresh namespace for incompatible provider record formats. No automatic
migration of existing active workers is provided. A provider must pass the shared
lease registration/expiry/version-exhaustion suites plus native race, retention
and lost-reply tests. See [implementation plan](plan.md) and
[verification](verification.md). Full Singlebox checks run in GitHub CI, never
locally for this change.

# Distributed Worker runtime

This crate implements the provider-independent coordination policy from the
[Worker SPEC](../../../specs/2026-11-17-worker-coordination/spec.md). It consumes
repository ports and the peer-transport port; it does not issue storage commands,
import HTTP clients, or select an enterprise provider.

`WorkerRuntime::start` generates a fresh incarnation and registers its control
address. Confirmed local validity is measured from the request start; readiness
waits one full lease TTL after the successful registration response. The default
heartbeat/TTL/check intervals are 1 s / 3 s / 500 ms. HRW uses the specified
SHA-256 encoding and defaults to stable node names. Healthy shards do not move
when membership changes.

The host supervises `run(stop)` and calls `drain(grace)` from its shutdown hook.
`run` owns independent heartbeat and scan futures, so scanning cannot starve
renewal. It creates no detached tasks. Manual `tick` is also available for tests
and embedding, but its caller must independently drive `heartbeat` during long
scans. All coordination I/O is bounded by the configured request timeout.

## Processor integration

`WorkerProcessor` implements the generic phase-one execution loop and persisted
effect replay. The host injects a pure Runner, public repository providers,
projection service, directory and time; no workflow-specific algorithm belongs
to this crate. See the [processor contract](src/processor/README.md),
[Dispatch supervision](src/supervisor/README.md) and
[phase-one SPEC](../../../specs/2026-11-18-worker-runtime-phase1/spec.md).

`WorkerRuntime` remains the coordination/lifecycle component. The host injects
initial checkpoint construction and uses `WorkerProcessor::run(stop)` for the
complete execution loop. Dispatch supervision and admission repair/discovery
are separately scheduled, sharing the same scan budget instance.

1. Recovery scans enumerate pending sessions, including future deadlines, acquire
   the checkpoint, and install the scheduling fence before exposing it through
   `acquired_sessions`.
2. The processor reconciles/replays the saved outbox. Only then does it call
   `recovery_completed`, which watches from the saved consumed position.
3. `read_inputs` reads a shard feed; `begin_round` admits a single session round.
4. The processor checks `may_continue_round` before issuing further writes or
   effects. A confirmed checkpoint and its matching receipt are passed together
   to `checkpoint_confirmed`. Uncertain results remain `round_unknown` until the
   existing commit-resolution protocol establishes the outcome.
5. `effects_completed` and `scheduling_completed` acknowledge actual completion,
   never an attempted send. A new scheduling change first obtains the
   `scheduling_started` gate. Quiescent sessions can use `release_session` to
   unwatch and free local state without deleting persistent pending work.

A shard can be handed off while its source sessions finish their current rounds.
FO claims and session acquisitions are registered before I/O and retained when
results are unknown. Drain cannot report success while they remain unresolved.
At an exhausted deadline with unfinished work, the runtime stops renewals and
returns `DeadlineReached`; it does not claim retirement while an embedding
processor may still be executing. The successor relies on CAS-confirmed termination, session
fencing, and effect idempotency. A fully quiescent drain confirms retirement
before returning `Drained`.

## Control boundary

The application facade compares the caller's bound source and target incarnations.
The runtime checks `source_done` against the exact persisted handoff source.
`DistWorkerPeerPort` supplies the replaceable outgoing transport. Credentials and
endpoint policy belong to its adapter/composition, not this crate. Control routes
must bind the exact instance via authentication or a channel restricted to
mutually trusted Workers.

## Bounds and verification

Candidate enumeration is bounded by page size and page count. A tick checks at
most the configured shard budget and starts at most the configured acquisition
budget. Member-page cursors advance across ticks, including pages containing
orphan registrations; a bounded prefix cannot hide every newer member forever.
Foreign-owner liveness is memoized once per distinct instance per tick.
Local session and incoming-handoff capacities reject further admission rather
than growing without bound. A scan page is retained until its entries have been
visited; it is not replaced by a mutable offset. Startup validates the configured
session limit against the store's advertised local capacity (if supplied).

Recovery prepares a frozen initial checkpoint before issuing an acquisition.
Definite preparation/first-attempt failures enter bounded backoff without becoming
in-flight writes. Unknown attempts retain their original claim and input, even
if a later retry fails definitely. Confirmed acquisitions retry scheduling
observation or local release directly. A round reserves new-discovery capacity
alongside retry capacity, rotates retries, and isolates individual session errors.
Deferred `check_after` is a hint capped by the periodic check interval; local
handoff acceptance and `source_done` prioritize discovery, with the persistent
pending scan as fallback. Neither hint grants processing rights.

Drain uses a monotonic overall deadline and a separate timeout for each I/O.
Session transfers run with bounded concurrency (default 8), within the global
per-round operation budget, and retain exact per-session authorization checks.
Heartbeat continues while a batch runs. Confirmed ownership/transfer/cleanup
progress continues to the next round after yielding, without the periodic scan
sleep. Bounded scans also continue when unvisited ready shards remain; cursor
movement and unknown outcomes do not count as progress. Otherwise the loop waits
`check_interval`. Claims, incoming confirmations, outgoing requests and session
transfer/cleanup each retain a retry deadline, including cancellation, so healthy
work cannot accelerate unrelated failed requests. Cooling shards are filtered
before the scan budget, and accepted shards without due local work need no owner
read. The per-round budget and concurrency limits remain unchanged.

One candidate view serves each round that needs discovery. A definitely rejected
target cools down for `candidate_backoff`, keyed by shard and full instance.
Untried candidates precede previously rejected ones; retries prefer the oldest
eligible rejection, retaining HRW ordering for ties. Eligibility is checked again
on retry. No candidate bypasses its cooldown merely because it is the only one.
An unknown request keeps
its original ID, payload and target. A subsequent read-only rejection is not
proof that the earlier request was cancelled. The receiver likewise retains an
uncertain incoming handoff through a later rejection, until durable owner
advancement or same-request acceptance confirms the outcome. Concurrent retries
of the same incoming request share a gate; queued calls recheck entry identity
and cannot write after their entry has been removed or replaced. Transfers reserve
the local session before I/O, so concurrent local release cannot erase an
uncertain write. The per-shard transfer cursor rotates past slow/failed sessions.
Transferred and fenced sessions
share the quiescent unwatch/cache-release path; cleanup failures retain local
responsibility. Feeds close once no local Session or acquisition needs them; shard ownership alone does not retain a reader.

`heartbeat_interval + request_timeout` must be strictly less than `lease_ttl`,
with additional deployment margin for synchronized client clock skew/steps;
using checked arithmetic. `exit_margin` reserves final confirmation/retirement
time; no new handoff starts without a remaining request budget. `drain(0)` is
valid and returns `DeadlineReached` when a graceful drain cannot be confirmed.
With no eligible recipient, drain keeps checking until the work budget approaches
exhaustion (normally `grace - exit_margin`) and returns `DeadlineReached`; the
shutdown hook must allow for this wait and use the lease/fencing fallback.

Tests exercise stable HRW, warmup, subscriptions after recovery, graceful transfer,
unknown RPC outcomes, pending scheduling fences, delayed claim/shutdown races,
retirement, stale incarnation checks, and session lifecycle completion proofs.
They use the public in-memory conformance model and do not prove real-provider
lease timing, persistence, or failover. Production composition still requires a
retained membership provider, explicit Runner and the complete public provider composition.
The default page and resident Session bounds are both 128. Coordination-only
large-capacity tests configure their separate limits explicitly; they do not
establish phase-one production capacity.

The [lifecycle](tests/processor_lifecycle.rs),
[finalization](tests/processor_finalization.rs),
[snapshot policy](tests/processor_snapshots.rs),
[publication race](tests/processor_snapshot_race.rs), and
[quota lifecycle](tests/processor_quota_lifecycle.rs) integration tests compose the
public Worker and storage providers with the conformance model and SQLite. They
verify consumed control inputs, frozen archive retries, committed cancellation,
remote Unknown supervision after local termination, publication coverage during
trim, and preserved output followed by a healthy task after quota failure. The
publication race counts actual full snapshot Blob arguments separately as unique
content, fault retry bytes, and other idempotent writes; these are logical port
bytes, not database physical writes or a production capacity measurement.

Runtime snapshots expose active work and pending effects. Structured tracing
records membership, claim, handoff, deferred acquisition and unfinished-drain
transitions with instance/shard/request identities and machine-safe error codes.
No exporter-specific metric registry is introduced here.

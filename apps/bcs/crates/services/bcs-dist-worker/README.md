# Distributed Worker coordination

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

The coordination runtime does not implement a reducer or execute an outbox.
The host injects initialization through a closure around its selected Runner;
there is no dependency on a particular workflow representation.

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
processor may still be executing. The successor relies on strict expiry, session
fencing, and effect idempotency. A fully quiescent drain confirms retirement
before returning `Drained`.

## Control boundary

The application facade validates authenticated source and target incarnations.
The runtime checks `source_done` against the exact persisted handoff source.
`DistWorkerPeerPort` supplies the replaceable outgoing transport. Credentials and
endpoint policy belong to its adapter/composition, not this crate. Control routes
must not be enabled without exact authenticated instance binding.

## Bounds and verification

Candidate enumeration is bounded by page size and page count. A tick checks at
most the configured shard budget and starts at most the configured acquisition
budget. Foreign-owner liveness is memoized once per distinct instance per tick.
Local session and incoming-handoff capacities reject further admission rather
than growing without bound. A scan page is retained until its entries have been
visited; it is not replaced by a mutable offset.

Tests exercise stable HRW, warmup, subscriptions after recovery, graceful transfer,
unknown RPC outcomes, pending scheduling fences, delayed claim/shutdown races,
retirement, stale incarnation checks, and session lifecycle completion proofs.
They use the public in-memory conformance model and do not prove real-provider
lease timing, persistence, or failover. Production composition still requires a
strict-lease provider and the embedding processor/inbox implementations.

Runtime snapshots expose active work and pending effects. Structured tracing
records membership, claim, handoff, deferred acquisition and unfinished-drain
transitions with instance/shard/request identities and machine-safe error codes.
No exporter-specific metric registry is introduced here.

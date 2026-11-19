# Generic Worker processor

The [phase-one SPEC](../../../../../specs/2026-11-18-worker-runtime-phase1/spec.md)
owns the protocol. `WorkerProcessor` keeps business policy in a pure injected
Runner and persistence in repository providers. OCB may select storage adapters,
credentials and test composition; it does not implement this state machine.

## Execution and recovery

Each Session owns one local slot. Input classification, independent task queue
updates, pure Runner evaluation and whole-Decision validation produce one frozen
candidate. State, consumed position and outbox commit together. The original ID,
content digest and candidate survive timeout and cancellation until the provider
confirms the outcome. Only a confirmed commit permits effect replay or Inbox ack.

Effects replay one operation per visit with stable committed identities. Large
payloads are externalized to immutable content-addressed Blob references before
commit. At most one missing Dispatch definition, referenced action or Blob write
is advanced per visit; resolved definitions and the frozen Decision remain local
until the corresponding head changes. Unknown Blob writes confirm the same key.

Task finalization first commits `AwaitingResult` and its immutable result record.
After confirmed archive storage, a second commit publishes the terminal event and
snapshot. Publication completion is required before retiring the task or starting
the next task. Quota exhaustion, cancellation and task timeout use the same
independent finalizer and do not require the Runner to cooperate.

Raw Thinking is bounded temporary output: it advances raw sequence/consumed and
emits a Thinking event, but does not call Runner, charge durable-output usage or
modify display/snapshot progress. The default local rate is 32 events/second with
an equal token burst; excess temporary events are consumed without publication.

## Isolation and control

Evaluation and replay attempts are durable and bound to exact head/input/context.
Historical success is evidence, never current authorization. Deterministic unsafe
state becomes Blocked; a safely finalizable invalid Decision becomes Quarantined.
The independent terminator closes admission before covering accepted obligations
with real archive and terminal-publication evidence. There is no automatic resume.

`WorkerIsolationApplication` requires an injected authorizer. Recovery checks
scope, owner, head, revision, isolation and closure certificate, advances the gate
generation and confirms recovery. Raw corrupt-body diagnosis seals only the owner
header and preserves the body. Blocked or completed isolation releases local
capacity while retaining all durable evidence and remote obligations.

## Resource profile

Default processor concurrency is 4 with at most 128 resident Sessions. Ordinary
safe cuts yield a hot Session after 8 confirmed commits. A transient failure while
replaying a confirmed outbox also releases its local slot and defers reacquisition
by the runtime check interval. The checkpoint, effect IDs and pending schedule
remain durable; recovery replays the same outbox without rerunning the Runner.
This is local capacity management, with no isolation or synthetic effect/Inbox
acknowledgement. Unknown commits and frozen unconfirmed candidates retain their
slots. Cleanup intent survives cancellation until unwatch and local cache release
complete, including failure after the provider has already released its cache.
Empty readers are released. Startup checks the
Inbox's document/readers/watches/receipts limits and requires sufficient readers
for `min(owned shards, resident capacity)`, plus at least 960 KiB document reads.

Repair, discovery, historical enumeration, acquisition and Dispatch supervision
must share one `ScanBudgetPort` profile/identity: default pool 32, scan IO at most
8 and at most one quarter of the pool, 1,000 log items/second, 200 Session checks/
second, burst 128. Each scan class retains a quarter; only the fourth quarter is
shared. Acquisition cannot bypass this quota. These are limits, not measured
production capacity. The host drives repair/discovery even when no user input
arrives and must enforce the declared capacity/health envelope.

The physical checkpoint/outbox/Ref frames and finalization reserves use the
phase-one profile. Semantic Task quotas may be lower and remain frozen. Metadata
identities and bindings are bounded by JSON encoded bytes including escaping.
The maximum mixed-action and terminal candidates are serialized through real
Rust types in `capacity_tests.rs`, including retained deadline identities.

`observations()` exposes body-free local gauges: unknown commits/age, persisted
outbox age (None for older unstamped batches), archive wait, resident isolation,
reader restarts, cached resources and shared scan IO. Parked isolation remains
queryable through the authorized facade; a zero resident count does not mean no
durable isolated Sessions. Supervisor pending/reserved bytes and Inbox maintenance
observations complete host diagnostics. No metric exporter is imposed.

## Validation boundary

Tests exercise actual public providers with the conformance storage model and
SQLite archive, including lost responses, cancellation, takeover, reader faults,
control races, quotas and single-slot fairness. A separate enterprise test
composition verifies real storage without adding enterprise dependencies here.
Neither model success nor a local feature branch establishes production rollout.

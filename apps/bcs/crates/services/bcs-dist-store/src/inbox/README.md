# Reliable Inbox provider

Status: public phase-one provider for admission, consumption, scheduling and
admission maintenance and recoverable isolation closure. Worker supervision and
independent task finalization remain application integration responsibilities. This module implements
[Worker runtime SPEC §4 and §8.2](../../../../../specs/2026-11-18-worker-runtime-phase1/spec.md),
including the gate/cut/terminal-proof and authorized recovery protocol in §7.2. The
[repository contracts](../../../../service-api/bcs-service-api/src/port/repo/distributed/README.md)
own the portable method shapes.

## Durable protocol

`StoreInboxProducer` implements the producer, consumer, scheduling, maintenance
and admission-control ports over `DocumentCas`, `Stream` and `ZSet`. It requires
no cross-key transaction or versioned sorted set. The adapters only supply
storage primitives; this public module owns the state machine.

1. Validate frozen scope and sid/tid, normalize business content, and recompute
   the digest. Canonical identity is a length-framed hash of tenant, principal
   and InputId; sid and shard are not part of that identity.
2. A shard root document atomically reserves history count/bytes and stores a
   full request plus monotonic sequence. That same root EQ reserves the first
   physical Inbox write in a bounded per-Session ledger. Reliable and intermediate
   input share this ledger, so concurrency cannot overspend either quota.
   Materialize an immutable per-sequence
   intent, then select the scope-wide canonical record. The full root copy stays
   until canonical existence is confirmed, including when the materialization
   reply is lost. An uncertain confirmation cannot become definite NotApplied.
3. Register the canonical operation in a bounded Session schedule slot before
   creating task/spec or appending Inbox. Create-once and frozen-content checks
   preserve an advanced task and its original workflow. Stable scope/task and
   Session/task indexes are confirmed before initial task creation and Accepted.
4. Append Inbox and CAS one canonical physical position. Physical duplicates are
   permitted; only the selected position is a logical delivery. Canonical progress
   records small durable phases (registration, spec, indexes, task, input), so a
   bounded repair turn resumes without repeating the whole creation prefix.
   Append credits are consumed by canonical CAS before IO; a lost credit reply
   uses separately reserved normal-repair capacity, never the control allowance.
5. Write Accepted and activation in the same schedule EQ. Copy the decision to
   the canonical record before removing its slot. Hint failures cannot undo
   Accepted. A close racing with activation uses that same document ordering.

Critical Agent events require `critical_agent_input_id(did, remote_event_id)`:
`ac-` plus the hexadecimal SHA-256 of length-framed
`["critical-agent:v1", did, remote_event_id]`. Holder/sequence changes preserve
this identity and normalized digest; kind or payload changes conflict. The
provider rejects caller-supplied alternate IDs and missing stable remote IDs.
The dispatch must match the existing task/session and frozen scope. Only User
inputs may create tasks; Human, Cancel and Agent inputs cannot bypass the gate
by supplying create_task. Final/Error cannot use the best-effort stream API.

`close_gate` requires the exact sealed owner, business head, control revision and
persisted isolation ID. It closes one admission generation before capturing its
fixed journal cut. A monotonic control revision in the schedule prevents an old
close/reopen request from refreshing the schedule version and replacing a newer
isolation. A successful fresh close uses two EQ writes and up to ten document
reads across close, cut capture and confirmation, excluding contention retries.

`closure_obligations` enumerates candidates; its caller cursor is not completion
proof. `StoreAdmissionControl` adds `AdmissionClosurePort` and requires an injected
`TerminalTaskEvidencePort`. It persists a continuous fixed-cut cursor, one pending
materialization phase, checkpoint task coverage and a cumulative evidence digest.
Each advance performs one phase/entry. It creates missing spec/index/task records
with create-once semantics before exposing the task to the independent finalizer.
Accepted conclusions remain stable. Closed Start slots remain until verified
terminal evidence exists, including when a creator resumes after recovery.

Completion requires actual immutable archive and terminal append/publication
receipts, rechecks tasks added by finalization after scanning began, and retains
an immutable certificate. `terminal_tasks` counts verified obligation observations
(including checkpoint coverage), not distinct task identities. No caller-supplied
boolean or guessed result reference proves completion. Per-phase reads include
control/schedule/certificate/progress; task verification additionally invokes the
bounded TaskFeed evidence provider and archive reads. Checkpoint coverage retains
at most 128 task IDs; each advance is bounded and can return pending progress.

Authorized recovery first persists Recovering and the original complete request.
Reopen verifies that authorization, exact closed generation and real certificate,
then advances the generation once and records a receipt. Checkpoint control only
returns Normal after confirming the actual gate receipt. After owner change a
new authorization may continue either before or after the gate write; it does not
advance an already-open generation again. A new isolation invalidates old
recovery, and if an old reopen won first its new generation is closed with a new
cut covering admissions in that interval.

## Scheduling and consumption

The schedule document owns the exact sealed owner, activation version,
next_check, listening flag and admission slots. `observe` installs the owner
fence only after verifying persisted checkpoint ownership. Every activation
invalidates old observations even when the due time is unchanged. `settle`
requires exact owner and token. NeedsRecheck requires a new obligation/input
check, not a replay of the old decision. Active and wake keys are disposable
hints; single-key reads avoid assumptions about stream partition co-location.

Each reader round rotates watched Sessions. A Pending canonical position stops
that Session before its gap; another Session can progress. Durable rejected and
noncanonical duplicate records may produce coverage without a logical input.
Receipts are bounded ephemeral reader-generation handles. Ack verifies session,
backend/log generation, issued coverage, and the actual persisted checkpoint
commit ID/digest/sequence and consumed position. A proof string grants no rights.
A row-classification deadline returns the already confirmed continuous prefix;
it does not discard that prefix and repeatedly restart the same large batch.
Closure enumeration likewise returns completed page progress before an unfinished
item; its continuation may repeat the cursor while a durable phase advances.
No Inbox trim or physical broker deletion is performed.

`FeedBatch.failures` reports per-Session failures and suspends that watch. Worker
must resolve any retained candidate, invalidate the failed watch, and rebuild
from committed consumed. A global reader failure requires rebuilding the feed.
The Worker processor rebuilds failed watches from committed consumed. Reads keep
watches installed across awaits and stage cursor/receipt changes until the
return boundary, so cancelling a poll cannot discard undelivered inputs.

## Retention, layout and bounded work

All authority is retained without TTL or automatic GC in a fresh phase-one
namespace. Never mix this format with legacy GT writers. All instances sharing
a namespace must use the same capacity profile. Layout is addressable rather
than requiring key scans: one bounded root per shard, one immutable document
per intent, one scope-wide canonical document per input, and one bounded
schedule document and Inbox stream per Session. Historical scans revisit all
retained sequences; duplicates increase scan cost. A root is the shard hotspot.

| Default limit | Enforcement |
| --- | --- |
| Input 64 KiB; document 960 KiB | Encoded request/envelope and JSON expansion checked before admission |
| Schedule 64 slots / 64 KiB | 56 normal slots; 8 reserved for existing controls/critical events |
| Pending full requests 16 / 8 normal | Atomic root reservation; full requests never occupy all schedule slots |
| Shard history 4,096 units / 64 MiB | CAS count/byte reservation before intent; first append prepaid, later physical attempts charged separately |
| Reserved history 512 units / 8 MiB | Normal admissions cannot spend control capacity; additional normal repair reserve 64 units / 128 KiB |
| Session history charge / distinct ledger 1,024 | Conservative new-session intent charge plus an independently bounded distinct-sid quota map |
| Inbox 4,096 entries / 16 MiB | Atomic root ledger per Session; 64 entries / 128 KiB each reserved for controls and for normal repair |
| Reader/watch/receipt 64 / 128 / 256 | Shared provider caps; watches/readers release on drop; unconfirmed receipts are not evicted for new work |
| EQ loop 8 attempts / 500ms | Remaining-time IO timeout, exponential 5–200ms conflict backoff |
| Scan 128 items / 1 MiB / 100ms poll | Fixed cut, retained bounded future, per-item timeout, persistent forward cursor and separate completion prefix |
| Scan IO min(8, pool/4) | Shared provider semaphore; default connection-pool size 32 |

Repair and discovery have independent durable cursors and budgets. Pending
references rotate separately from the forward cursor. A persisted lane toggle
alternates retry-first and forward-first turns, so even a pending item that uses
its entire IO time slice cannot hide later Sessions. Each call polls a retained scan for at most 100ms, including root initialization.
The future retains bounded intermediate state across polls and completes its
cursor CAS and health publication; cancellation does not restart the prefix.
Transport reads are bounded at two seconds and historical item reads at 500ms.
Host assembly must keep the repair and discovery lanes polled independently.
If only one shared IO permit is available, root/scan initialization proceeds
sequentially. A failed disposable wake after confirmed activation does not mark
that authoritative discovery check unresolved; history remains independently
enumerable.
A discovery pass checks authoritative scheduling,
unconsumed Inbox and checkpoint obligations, including future deadlines and
next_check=None. Takeover enumeration returns journal Session candidates;
one bad canonical does not poison the shard page.

A single injected ScanBudget is shared with Worker discovery and Dispatch
supervision. The default aggregate caps are 1,000 log items/second, 200 Session
checks/second and a 128-item burst. Repair, discovery and dispatch each reserve
a quarter of the quota; the last quarter is shared. Global scan IO is at most
eight and at most a quarter of the configured pool. Startup rejects absent or
mismatched profiles. These limits alone are not a capacity certification.

New Start admission requires recent monotonic evidence of complete repair and
discovery rounds. Cold producers do not trust a persisted timestamp/counter;
they must perform a new complete round or observe subsequent progress after a
recent local read. A genuinely new empty namespace has a bounded bootstrap
window. Health proof uses the fixed round start and includes conservative
publication latency; continuous appended tails do not invalidate a finished
cut. Same-round competitors cannot refresh its start. Local evidence is bounded
and may be evicted; revisiting an evicted shard requires cold verification.
At half the 60-second window a structured warning is emitted; at the full window
new Starts are backpressured. Existing identity retries, repair and controls
retain their protocol and reserved capacity. Broken storage may prevent progress;
no fixed discovery latency is claimed under that condition.

## Verification and integration limits

The provider tests use the exported `bcs_test_support::store_model::Model`, with
fault wrappers for exact write/read boundaries and controlled interleavings.
The focused suites are `reliable_inbox`, `inbox_gates`, `inbox_reader`,
`inbox_maintenance`, `admission_closure` and `control_faults`; existing admission and failure suites remain regressions.
The additional `inbox_latency` suite injects 5/10ms document RTT, tests repaired
phase progress without caller retries, full-capacity first-write and lost-credit
recovery, reader continuous-prefix delivery, cancelled reads, closure pagination and
80ms storage RTT with retained scan polls and reconstructed history tokens.
They cover every normal admission lost-write boundary, cross-shard identity,
canonical/slot/close ordering, old generations, critical event identity,
unknown confirmation reads, no-gap-skipping, actual consumed proof, independent
discovery, fixed cuts, quota races and actual scan read bytes.

Run from the Avernet root:

```sh
cargo +1.94.1 test --manifest-path apps/bcs/Cargo.toml -p bcs-dist-store --offline
cargo +1.94.1 clippy --manifest-path apps/bcs/Cargo.toml -p bcs-dist-store --lib --offline --no-deps -- -D warnings
```

These model tests do not validate production capacity or a 60-second recovery
SLO. The enterprise composition must run PROXY validation and record retained
history sizes, shard hotspots, pool size, actual IO, full scan duration and worst
missed activation delay before enabling a deployment profile. SDK-only testing
is not an acceptance substitute. No production profile is certified here.

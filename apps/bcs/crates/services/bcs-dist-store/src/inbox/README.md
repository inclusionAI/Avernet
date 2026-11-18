# Reliable Inbox provider

Status: public phase-one provider for admission, consumption, scheduling and
admission maintenance. Worker supervision and complete isolation finalization
are separate integration tasks. This module implements
[Worker runtime SPEC §4 and §8.2](../../../../../specs/2026-11-18-worker-runtime-phase1/spec.md),
with gate/cut primitives for §7.2. The
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

`close_gate` validates the actual sealed checkpoint owner, closes one generation
and captures a fixed journal cut. `closure_obligations` includes prior Accepted
and registered creators paused before task creation. It does **not** authorize
closure completion, terminal publication, archive verification or reopening.
The isolation implementation must confirm those facts, preserve old Accepted
history, and bind resume to persisted recovery control before advancing a gate.

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
Current provider tests exercise this protocol; complete Worker supervision is
part of Task 5, not established by the provider alone.

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
| Scan 128 items / 1 MiB / 100ms | Fixed cut, per-item timeout, persistent forward cursor and separate completion prefix |
| Scan IO min(8, pool/4) | Shared provider semaphore; default connection-pool size 32 |

Repair and discovery have independent durable cursors and budgets. Pending
references rotate separately from the forward cursor. A persisted lane toggle
alternates retry-first and forward-first turns, so even a pending item that uses
its entire IO time slice cannot hide later Sessions. The 100ms turn starts before
root/scan initialization, reserves time to persist its cursor, and resumes a
completed round's health publication on the next turn when necessary. A discovery pass checks authoritative scheduling,
unconsumed Inbox and checkpoint obligations, including future deadlines and
next_check=None. Takeover enumeration returns journal Session candidates;
one bad canonical does not poison the shard page.

The provider budgets each scan class at 64 Session checks/second and at most
375 log items/second, combined burst at most 128. Task 7 must combine these with
dispatch supervision and other providers under the Worker-wide SPEC budgets.
These local limits alone are not a capacity certification.

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
The focused suites are `reliable_inbox`, `inbox_gates`, `inbox_reader` and
`inbox_maintenance`; existing admission and failure suites remain regressions.
The additional `inbox_latency` suite injects 5/10ms document RTT, tests repaired
phase progress without caller retries, full-capacity first-write and lost-credit
recovery, reader continuous-prefix delivery and closure pagination.
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

# Durable DispatchBoard provider

`StoreDispatch` implements the public control, queue, held-dispatch and command
feed ports over `DocumentCas`, `ZSet` and `StreamGroups`; membership is injected
through `MembershipPort`. It contains no enterprise client or connector network
algorithm. The owning contract is
[`distributed/dispatch.rs`](../../../../service-api/bcs-service-api/src/port/repo/distributed/dispatch.rs),
with [phase-one SPEC](../../../../../specs/2026-11-18-worker-runtime-phase1/spec.md)
§5.1, §7.1 and §8.2.

## Persistence and recovery

A shard journal atomically reserves retained bytes/count and stores the complete
committed intent before any dispatch mutation. It retains a bounded immutable did
directory. The dispatch CAS applies intent and complete provenance/body/result
receipt together; only then can the pending journal slot be cleared. Receipts
survive input dequeue and outbox advancement. Replay compares full immutable
completion identity and effect body, never an opaque proof. A failed verifier is
followed by another receipt read to cover concurrent completion/outbox advance.

The document's version, due and execution/control state are authoritative. `sched`
and `held` are hints. `scan_due` independently traverses the retained directory
with a durable fixed cut and selected-work phases (apply, clear, read, hint).
Repeated invocations, including with `after: None`, resume the durable shard
cursor. The opaque page token is namespace/shard scoped; it is not an offset that
can reset an in-progress traversal. Failed or unknown items rotate; successful
repair prefixes remain durable. Every invocation polls for at most 100ms and at
most 128 phase steps. A slow phase, including its cursor CAS, remains in a bounded
local scheduler across slices. Each native IO keeps its configured timeout
(default 500ms); expiry rotates an unavailable item while its authoritative
obligation remains in the retained journal/directory.

The scheduler holds at most two shard phases, each with at most 128 staged dids.
A canceled caller retains both its current future and earlier staged results;
publication has no later await. Completion of a resumed phase releases its slot
before starting more work on that shard. Idle slots are reclaimable after five
seconds. There are no spawned tasks or scheduler/future ownership cycles, and
retained IO is counted by the injected shared scan budget. A new provider resumes
from the durable fixed-cut cursor; it may rediscover a due item, which is safe
because scans do not acknowledge or settle dispatch obligations. Unknown cursor
writes are read back on the next invocation.

Creation success includes durable discovery registration; replay also repairs
pending cleanup and the schedule hint. Notification failure does not undo it.
A terminal dispatch retains its evidence and remains due until the Supervisor
has submitted stable critical return evidence and calls `reschedule` with that
exact revision. This clears due and returns `Ended`. An old revision returns
`NeedsRecheck`; it cannot hide a new cancel/input obligation. A local Task ending
does not by itself end a Dispatch or discard unresolved cancellation.

Claims require live membership for the claimant and `Dead` for takeover; Unknown
is never eligibility. Resumption requires the exact prior holder and advances
epoch. Replays return `OwnedByMe`, preventing duplicate local actors. Every held
write rechecks exact holder and membership, then CAS-fences concurrent takeover.
`begin_send` persists the fixed request identity before returning a send permit.
Accepted operations never grant another network send permit; their evidence
methods remain idempotent. Inputs advance only an accepted contiguous prefix. `OutcomeUnknown` cancellation
remains nonterminal; `finish` requires a definitive outcome and nonempty evidence.

Command feeds merge repeated did hints until acknowledgement, including idle PEL
redelivery. One application acknowledgement covers all merged raw stream IDs.
Tokens are checked against this exact feed generation and actual deliveries.
After append, disposable notification streams are trimmed with `MaxLen(4096)`.
This is a healthy-storage length target, not a cross-call atomic admission cap.
Trim failure is reported without undoing the append; assembly must count these
failures and monitor hint backlog. No authoritative receipt/journal is trimmed.
The feed bounds raw in-flight entries and remembered acknowledgement identities
at 128 each. New notifications after acknowledgement may legitimately represent
new control and are delivered again; Dispatch claim fencing prevents actor forks.

## Explicit initial capacities

| Item | Default / maximum |
| --- | --- |
| Retained dispatches per shard | 1,024 |
| Journal pending intents | 64 and 512 KiB |
| Ordinary-work pending reserve | leaves 8 slots and 64 KiB for cancellation |
| Retained directory encoding | 256 KiB |
| Per-dispatch receipt reservations | 256 and 512 KiB |
| Ordinary-work receipt reserve | leaves 16 slots and 64 KiB for cancellation |
| One reservation charge | twice encoded intent bytes plus 16 KiB for bounded execution/evidence metadata |
| Maximum encoded physical document | 960 KiB |
| CAS episode | 8 attempts / 500ms including remaining IO timeout |
| Conflict backoff | 5ms exponential, capped at 200ms |
| Provider-local IO permits | 8, never more than configured pool size / 4 |

Reservations account for receipts, queued copies and future bounded send evidence
in the same journal CAS; concurrent admission cannot overdraw a read-then-write
balance. All receipt and directory evidence is retained, with no TTL or GC.
Remote references and terminal evidence each admit at most 4 KiB of encoded
JSON; send/claim request identities admit 512 encoded bytes. These limits account
for escape expansion, rather than treating raw string length as stored size.
Capacity therefore includes historical ended dispatches and controls. At the
maximum profile dispatch documents alone may retain roughly 512 MiB per shard,
plus journal/cursor/index/command metadata and storage encoding overhead. These
are configured caps, not a measured supported deployment capacity.

The injected ScanBudget is shared with Worker/Inbox repair and discovery, while
the local semaphore also bounds foreground provider IO. Assembly must measure
retained-history full-round age,
oldest pending age, store pool occupancy, bytes and hotspot distribution before
enabling a deployment. No provider model test proves the 60-second discovery
objective, production throughput or a particular PROXY capability.

## Verification

`tests/dispatch.rs` and `tests/dispatch_faults.rs` exercise actual provider methods
on the public StoreModel, including lost replies, later definite failure, retained
proofs, outbox advance, holder fencing, input ordering, queue deduplication,
capacity reservation, delayed IO and restarted scans. Host acceptance must use
PROXY; model and SQLite fixture execution are not host acceptance.

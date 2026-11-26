# Durable DispatchBoard provider

`StoreDispatch` owns Worker create, control, receipts, notification and supervision.
`bcs-dist-connector-store` owns command feeds, claims and holder writes. Both
use the same persistent JSON object and storage revision at `disp(did)`.
The root contains `spec`, `control`, `execution`, optional `connector`,
`receipts` and `due`; no nested `view` or positional array is used. Use a fresh
namespace when switching from the earlier experimental encoding. The owning contract is
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
does not by itself end a Dispatch or discard unresolved cancellation. Later
authorized cancellations share the first intent and retain their own effect
receipts; they do not replace the remote request or reset its observation.
Changing the content of an already accepted cancellation ID is a conflict.
A new request against a proven terminal dispatch records that outcome and
evidence rather than creating another cancellation obligation.

Worker writes use the exact observed revision (EQ); receipts and control changes
commit together. Connector writes use a strictly greater revision (GT): normally
the last read plus one, or `epoch × 10^9` on claim. A claim changes only holder,
initial phase and the optional Connector object; it cannot overwrite a racing
Worker cancel or queued input. A former holder's late write cannot cross the new
version band. A terminal write sets `due=0` in the same mutation as its evidence.
Object updates recursively merge; `null` removes an obsolete field, while arrays
(including queued inputs and receipts) replace as units. Worker journals and
scan cursors still use exact whole-document replacement.

Connector takeover requires the prior holder to be definitively dead. Retries by
an already current owner do not start another actor. A durable send intent and
contiguous acceptance watermark protect pending input; a cancel observation alone
never ends a call. See `bcs-dist-connector-store` for the authoritative rules.

Worker and Connector must configure the same `CommandStreams` partition count.
Each physical hint has its own acknowledgement. Duplicate hints are harmless:
the dispatch record decides who can act. A lost or trimmed hint does not remove
the independent Worker recovery obligation. Notifications are trimmed to a target
of 4,096 after append; trim failure does not roll back an append.

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
JSON. These limits account
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

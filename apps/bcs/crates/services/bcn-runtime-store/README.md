# BCN runtime repositories

`RuntimeStore` implements the business repository ports over an injected
`Arc<dyn AtomicStore>`. It owns no transport, database client, process-local
correctness map, credentials, or environment configuration. Bootstrap must
explicitly select a backend. Nothing here enables production admission or
changes the legacy runtime. `LocalResultStore` is an explicit test/local result
adapter and rejects the production profile.

## Storage boundary and layout

Every row is scoped by environment, tenant and runtime namespace. The fixed
`bcn-runtime-v1` atomic partition is **one physical partition per Scope**. The
1024 logical shards distribute ownership/work only; they do not distribute
physical storage. Capacity and production durability are unverified. Future
physical sharding requires a versioned routing/identity design, not a silent
configuration change.

Independent rows hold owners, session headers/checkpoints, admissions, task
metadata/snapshots, inbox entries/dedup receipts, commands/delivery leases,
dispatches, events/publication receipts, node projections and members. There is
no serialized scope/shard value or repository-wide revision/lock. Entity key
components use domain prefixes and length-delimited SHA-256; lexical sequence
keys have fixed widths. Scope isolation is structural. Every value has a schema
version envelope; incompatible or malformed data fails closed. Task and command
IDs are directly addressable without looking through other shards.

Admission atomically creates receipt, immutable principal/session/target metadata,
initial snapshot, four principal/optional-target/optional-session indexes, inbox
and wake. `TaskQueryStore` is read authority for pending and terminal tasks;
`ResultIntent` does not supply authorization metadata. The gateway additionally
applies current target access policy. IDs do not grant access. Session headers
freeze policy and retain at most `max_pending_tasks` (1–16) outstanding accepted
tasks, including the running task. Capacity is released only when its final
`ResultSaved` inbox input is checkpointed; cancel input bypasses capacity.
`repair_pending` enumerates only admissions whose Submit has not been consumed.
Atomic transactions leave no partly admitted task to synthesize from history.

## Fences, publication and recovery

Ownership records never expire. Claim compares the exact previous owner and
incarnation; epochs increase on every transfer. Heartbeat TTL is a failure
observation, not write authority. Liveness uses server time, incarnation-specific
rows and bounded pages. Failed known-member observations are Unknown; membership
page discovery errors propagate rather than invent a set of dead members.

Activation checks current ownership and frozen specification. Recovery adopts
the existing checkpoint rather than replacing its state. Checkpoint revision,
input position, state, deadlines, immutable effects/result intents and hard facts
commit together under the current owner. Wake enumeration includes admitted
sessions before activation; `load_spec` supplies their frozen specification.
`deactivate` checks checkpoint, input head and outstanding work under the fence.
Inbox reclamation deletes at most 100 committed entries per call; repeat until
empty. Dedup receipts remain, so reclamation does not permit replayed inputs.

`effect_command_id` derives a typed stable identity from session and effect
operation/result identity. `CommandBus::publish` takes only an ownership fence
and committed reference; it loads the canonical command, atomically queues it
and clears only the matching checkpoint effect. Current owners can retry; stale
owners cannot fabricate payloads or clear newer outboxes. Already published
identities do not recreate pending work on a subsequent checkpoint commit.

Delivery is a bounded paginated scan with 3-second server-time claims. Consumers
advance the returned cursor even when a page contains no eligible commands,
then cycle after reaching the end; this avoids leased-head starvation without
unbounded scans. ACK requires the matching consumer and, for Send/Cancel, an
already durable dispatch. ACK never completes a dispatch. `DispatchStore::read`
and `scan_active` expose immutable `DispatchContext` (command/session/task/node)
plus the exact operation after process restart. Active indexes are removed only
on a terminal operation receipt; Accepted/InputRequired remain discoverable.
Finished records remain directly readable and exact receipt retries return the
canonical record without mutation. A different terminal receipt/input, later
handle, takeover or delta is rejected. PREPARED alone proves unsent;
MAY_HAVE_SENT is durable before network I/O and is preserved through takeover.
No repository replays side effects.

## Event and result authority

Worker hard events load only immutable committed facts. Publication and pending
acknowledgement are atomic and fenced by current shard ownership, including for
HumanInput before any dispatch exists. Only the earliest pending event in the
session checkpoint's durable commit order may publish; later events return
Conflict and stay pending. Published event IDs still return their canonical
cursor on retry without reapplying status, including after later publication.
Terminal status requires the saved task result receipt and uses that result's
output, even after the current task switches or inbox data is reclaimed.

Connector progress belongs to the latest committed send command for its task/node.
The first immutable command commit atomically updates a separate bounded
`projection-command` row with the checkpoint. Recommitting an old command does
not restore authority. Reset, Delta and publication retries check this row and
its revision in their transaction, so a HumanReply continuation supersedes old
InputRequired progress even when both dispatches have the same epoch. The row
survives restart and delivery ACK; no dispatch epoch ordering is inferred.
`ConnectorProjection`
contains only task ID, node output and expected task cursor: it cannot overwrite
status or admission metadata. Up to 32 independent node rows are aggregated in
stable node order. Reset atomically writes the node projection, aggregate
snapshot, new task-global epoch, Reset event and publication receipt. There is
no partially published reset state: failure leaves the prior state untouched;
retry resumes the same immutable intent. A new dispatch fence must reset before
Delta; dispatch epochs never substitute for task-global stream epochs.
`append` accepts Delta only; its cursor is an expected-current cursor, and the
store allocates the next sequence. A Delta is emitted only when appending its
text to the aggregate task output exactly reproduces the new node aggregate.
Otherwise it emits an aggregate Reset with a new task epoch (for example, an
update to A in `[A, B]`). Consumers apply Reset or task-tail Delta to reconstruct
the output. This adds full bounded snapshot bytes to such events and expires
older cursors; no extra storage call is needed. Capacity remains unverified.
Concurrent projection changes conflict and
must be reloaded, preserving peer output. Values over the infrastructure byte
bounds fail atomically. `read_after` verifies the snapshot did not change around
the bounded event scan; callers retry Conflict. Old epochs receive the current
snapshot/reset boundary, not silent continuation of an obsolete stream.

The result adapter returns authoritative saved values. `InboxPort` is a trusted
internal capability, never exposed to protocol clients or Gateway callers as
arbitrary SessionInput. Worker forwards only the actual `ResultStore` return as
ResultSaved. The repository validates the exact precommitted result intent and
session/task/node/attempt association, then atomically persists its immutable
saved receipt with inbox/wake. This permits a production result adapter to stay
independent of private repository keys. An old Worker may retry that exact
committed result across ownership change, but cannot invent another payload.
The local adapter independently retains immutable results and bounded per-task
result indexes, and delegates task listing to an injected `TaskQueryStore`.

## Bounded operation costs

R = `read_many`, T = `transact`, S = `scan_page`, C = server clock. One call is
one bounded backend operation under the AtomicStore contract, not a claim about
an eventual database driver's network implementation. Counts below describe a
successful attempt; CAS conflicts are returned, never retried in a busy loop.
Workers/Connectors must back off and resume from durable state. Empty R/T calls
are included. No transaction spans network delivery or result-store I/O.

| Operation | Calls | Rows/scan bounds |
| --- | --- | --- |
| New admission | 2 R + 1 T | 5 reads, 5 conditions, 13 mutations; no scan |
| Admission retry / receipt | 1 R | 3 / 1 rows |
| Task get | 1 R | metadata + snapshot (2 rows) |
| Task list | 1 S + 1 R | page <=100 index IDs + <=100 snapshots, no history filter |
| Repair/active sessions | 1 S | <=100 live-index rows, examines <=limit+1 |
| Claim/transfer shard | 1 R + 1 T | one owner condition and mutation |
| Activation | 3 R + 1 T | owner twice + header/checkpoint, 1 checkpoint mutation |
| Deactivation | 3 R + 1 T | checkpoint + header/input head + owner; one wake delete |
| Load/load_spec | 1 R | one entity |
| Session commit | 7 R + 0–1 S + 1 T | consumes <=100 inputs, new effects/events <=32 each; up to32 new send-authority mutations; overall T <=100 conditions/mutations |
| Ordinary inbox append | 2 R + 1 T | header + sequence/dedup; 4 mutations |
| ResultSaved confirmation | above + 1–2 R | intent/task/receipt, optional final task receipt; <=6 mutations |
| Inbox read | 1 S | <=100 entries after cursor |
| Inbox reclaim | 2 R + 1 S + 1 T | <=100 committed deletes, checkpoint + owner fence |
| Effect publish | 4 R + 1 T | canonical command + delivery + checkpoint + owner; 3 mutations |
| Command receive page | 1 S + 1 R + 1 C + 1 T | <=100 ready rows and leases, <=100 lease mutations |
| Connector command ACK | 3 R + 1 T | delivery + canonical command + dispatch, 2 mutations |
| Dispatch claim | 2 R + 1 C + 1 T | command/delivery + dispatch; 2 mutations |
| Dispatch phase/handle/takeover | 1 R + 1 C + 1 T | dispatch + active index, 2 mutations |
| Dispatch receipt | 2 R + 1 C + 1 T | dispatch + inbox sequence/dedup; <=6 mutations |
| Active dispatch page | 1 S + 1 R | <=100 IDs + <=100 records; independent of ACK/history |
| Hard event | 6–7 R + 1 C + 1 T | optional final result lookup; 4 mutations |
| Connector reset/delta | 4 R + 1 S + 1 C + 1 T | <=32 node projections (scan limit33); 4 mutations |
| Snapshot / event page | 1 R / 2 R + 1 S | <=100 events, snapshot consistency check |
| Local result put | 1–2 R + 1 C + 1 T | committed intent/result + optional final result; <=3 mutations |
| Liveness register/renew/drain | 1 C + 1 R + 1 T | one incarnation row |
| Liveness inspect/page | 1 C + 1 R / 1 S + 1 C | <=100 known members / rows |

Every T is capped by AtomicStore at 100 conditions, 100 mutations, 1 MiB/value
and 8 MiB request. Session outboxes/deadlines are additionally bounded at 64
items. Oversized transitions fail without advancing a checkpoint; callers must
use a smaller bounded input batch where applicable. At the default recovery
concurrency of 64 there can be 64 concurrent bounded operations; this is not a
measured capacity promise. Long-lived dedup/result/event rows need an explicit
future retention policy, but hot paths scan only pending/active indexes or a
specific task epoch/session cursor, never all history. There is no automatic
production fallback to memory.

## Compatibility and verification

These are unreleased v1 contracts. Publication now takes committed references,
dispatch claim takes CommandId, receive returns a cursor-bearing page, and reset
takes ConnectorProjection. DispatchRecord requires DispatchContext. Existing
new-runtime payloads missing required context must be explicitly migrated or
discarded offline before enabling writers; no identity is inferred from hashes.
Legacy runtime formats/in-flight sessions are untouched. Serialization tests,
real repository behavior tests and the shared memory AtomicStore conformance
suite cover the boundary. Fault injection is against the real memory backend;
it proves local semantics only, not production durability/failover or capacity.

Focused helper modules separate versioned persistence, delivery leases, query
indexes and the explicit local result adapter from the planned repository
modules. Each source file stays below 1000 lines with one responsibility.

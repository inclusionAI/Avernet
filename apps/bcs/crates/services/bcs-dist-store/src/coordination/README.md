# Worker coordination persistence

This module implements MembershipPort, ShardLeasePort, SessionStorePort and
SessionControlPort using
public DocumentCas, StrictLease and ZSet contracts. It contains no backend client,
HTTP transport, environment access or vendor commands. Construction rejects any
missing capability before registering or claiming work.

Owner records and checkpoints each use independent private versions. Claims,
handoffs, sealing, business commits and recovery control writes use exact EQ.
The cached confirmed document permits a normal commit with one write and no
read. Conflicts reload/revalidate; idempotent success reloads the authoritative
owner before returning a receipt. Storage versions never prove a business commit.

A checkpoint contains the public envelope and a private bounded attempt ledger
for its current business head (at most 64 distinct recovery attempts, at most
8 KiB encoded list bytes, and at most 256 encoded bytes per ID). Existing-ID
retries remain idempotent when the new-attempt budget is full.
Only meaningful business progress resets the legacy streak and ledger; a
consumed-only commit does not. A seal preserves both. Known missing checkpoints
fail; fresh absence follows SPEC scheme A. Ordinary acquisition rejects an
unsupported checkpoint before sealing. `acquire_control` validates the same
claim/StrictLease and handoff fence, then seals the raw owner while preserving an
undecodable business body. Missing/corrupt owner or head cannot authorize this
path. No initialization marker is introduced.

Discovery registers the role index before the lease; orphan index records are
ignored and retained while registration may be in flight. Retire removes its
member from the three role indexes after stable termination; a failed cleanup
is retried by discovery. Each discovery page batches removal of Terminated
members only, never Absent or Unknown. The index has stable member-key
continuation and bounded pages. The strict lease implementation supplies stable termination; a missing or failed
read is Unknown, never evidence for fault takeover. Claim and handoff mutation
checks propagate Unknown as an uncertain result; it cannot become Fenced or
Rejected and release an earlier unknown operation's responsibility.

Handoff timers start at receiver method entry before storage I/O, use local
monotonic time and retain at most one request per shard
with a configurable shard ceiling (`max_handoff_timers`, default 4096).
Retries do not restart them. Confirmed source completion or handoff clearing
reclaims the exact request's timer; elapsed time alone does not forget an
uncertain write and permit a retry to restart protection. A lost timer means
expired protection, not permission to skip a session seal. The confirmed
checkpoint cache has a configurable session ceiling (`max_cached_sessions`,
default 10000); reserve precedes creation. `local_session_capacity()` advertises
this bound for runtime wiring validation. Session and shard limits are separate. Definite
Fenced/Deferred observations release their slots. The local holder is separate
from an observed document owner so successful transfer can release the source
without a late release deleting the new holder. Unknown writes and known
checkpoint-loss evidence survive errors. The embedding
runtime can release a quiescent session's cache with `SessionStorePort::release_local`;
active or uncertain work must retain its entry. Capacity exhaustion rejects new
work rather than growing memory without a bound.

## Encoded checkpoint capacity

`checkpoint_capacity()` declares total document and non-checkpoint envelope
budgets, including the host-declared `storage_envelope_bytes`. Defaults are 1 MiB,
64 KiB and 64 bytes respectively; the host must prove its adapter wrapper bound
and backend capacity. Construction serializes the maximum actual envelope,
including bounded control and legacy recovery history, and rejects incompatible
budgets. Every write checks the full encoded row and its envelope before CAS.
The Worker separately validates maximum legal business/finalization candidates;
missing capacity declarations fail startup. This is an enforced bound, not an
extra storage read or an assumption about an unnamed backend's native limit.

## Integration and migration

Admission creates spec/task through the same DocumentCas layout that Worker
reads. Legacy GT helpers remain only for other independently validated paths.
Do not mix legacy JSON or GT writers with these records. Initial adoption uses
an isolated namespace with all writers on the new protocol; existing namespaces
require a separate offline migration/recovery procedure. This change does not
automatically migrate live data or enable the Worker in existing bootstrap.

The coordination-only #4145 prototype used numeric checkpoint format 1 with an
unscoped `runner_state: {format_version, value}`. The phase-one runtime's format 1
uses `runner_state: null | {tid, state}` and task-local lifecycle state. Those
prototype namespaces are unsupported, even though the numeric version matches:
do not reopen them with this runtime or infer a task association for old state.
Use a fresh namespace; retaining or migrating prototype evidence requires a
separate offline procedure. Ordinary reads check the numeric header before body
decoding and explicitly reject the known prototype shape as `UnsupportedFormat`
(format 1), without rewriting/sealing it. Other malformed current bodies remain
data errors. Header-only control recovery continues to preserve the raw body.

The provider's lease implementation must pass the strict contract before this
store can start. A document-CAS-only backend is insufficient. The test-only model
is a fault-injection fixture, never a production fallback.

A successful source session handoff reads the current shard once before the EQ
write and once afterward, plus a lease check at each point and checkpoint reads
before/after the write. The pre-write shard read supplies both claim and handoff
validation; checks are not shared across sessions or skipped after a write.

## Isolation and authorized recovery

The [Session control contract](../../../../service-api/bcs-service-api/src/port/repo/distributed/control.rs)
and [runtime SPEC §7.2](../../../../../specs/2026-11-18-worker-runtime-phase1/spec.md)
separate Normal, Quarantined, Blocked and Recovering from business state. A
logical control revision is independent of the private document CAS version.
Owner, business head and frozen control revision guard every new mutation.
An ordinary candidate freezes its control revision before its first write; it
cannot become legal again merely because an isolation was later resumed.
Quarantined permits only commits naming the same isolation. AwaitingResult and
retained Done records cannot change their frozen result or finalization identity.
Blocked and Recovering permit no new business commits.

Attempts freeze state/input/context digests and time before risky work. Finishing
a deterministic failure and entering isolation share one checkpoint-header CAS.
`begin_replay_attempt` additionally verifies the current committed effect ID and
digest, allowing necessary replay while Quarantined. Successful/transient
outcomes do not count as toxicity. One suspended Runner slot preserves a pending or successful uncommitted attempt
and
its frozen context while takeover replays the previous committed batch at the
same head. A successful/transient replay stores one bounded completed-replay receipt and
restores that Runner in the same control CAS; only an already-confirmed previous
receipt moves to independent history before the slot is reused. Failed replay
retains both pieces of evidence under isolation. Explicit
recovery archives and clears the suspended slot without erasing its evidence.
Replacing the active attempt retains its
identity and result independently; pending history can acquire only its first
confirmed outcome. Superseded/recovered isolation records retain the original
failure and monotonic close/completion progress; an isolation ID cannot be reused
for a different failure after recovery. Recovery requests retain their
complete authorization under the stable request ID before Recovering is written.
These documents have no TTL or automatic GC in phase one.

Control writes never replace the checkpoint/outbox. They invalidate typed local
cache before awaiting IO (including cancellation/unknown replies), and refresh
it after confirmed writes. Cached control disagreement in commit triggers one
authoritative read before rejection, including updates from another provider
instance; matching normal cached commits retain the one-write path. A bounded reservation retains known checkpoint
existence even for raw acquisition. Pure Deferred/Fenced observations release
new reservations; known losses and uncertain seals remain occupied until the
runtime explicitly releases responsibility.

A control mutation reads one checkpoint header and CAS-writes it; beginning a
new attempt may additionally read its history and retain the previous attempt.
Isolation/recovery transitions may also read/write retained evidence. Each
operation is bounded by eight CAS attempts, 5–200ms conflict backoff and a 500ms
whole-operation deadline; active control content is limited to 48 KiB. Unknown
results keep the original identity and guard. This adds durable evidence IO to
risk boundaries, not an extra read to an already-confirmed ordinary commit.

Tests `session_control`, `admission_closure` and `control_faults` cover raw bad
state, cache/existence protection, exact fences, retained evidence, lost writes,
real archive/terminal proofs and authorized recovery. They use public model
providers and SQLite archive storage; they are not PROXY or production-capacity
acceptance evidence.

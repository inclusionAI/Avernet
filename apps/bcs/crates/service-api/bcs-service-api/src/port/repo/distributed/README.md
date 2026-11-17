# Distributed repository contracts

Status: declarations for an opt-in runtime; no concrete provider or runtime is
implemented by this change. The Rust declarations own the exact method shapes.

## Boundaries

| Boundary | Traits | Responsibility |
| --- | --- | --- |
| SessionInbox | `InboxProducerPort`, `InboxConsumerPort`, `ShardFeedPort`, `InboxSchedulingPort` | Durable admission, replayable per-session input, acknowledgements and recoverable activation |
| TaskFeed | `TaskPublisherPort`, `TaskReaderPort` | Committed output, task snapshots, consistent restore and resumable subscriptions |
| DispatchBoard | `DispatchControlPort`, `DispatchQueuePort`, `HeldDispatchPort`, `CommandFeedPort` | Worker intent, Connector ownership, remote progress and notification hints |
| Session state | `SessionStorePort` | Frozen spec reads, per-session owner seal, atomic business commit, unknown-result resolution and recovery metadata |
| Ownership | `ShardLeasePort`, `MembershipPort` | Shard assignment and instance liveness; Unknown is not Dead |
| Results | `ResultStorePort`, `BlobStorePort` | Immutable final archive and immutable payload storage |

Gateway application calls `DistGatewayCoreService`, which uses business repo
ports. The pure `DistRunnerCoreService` reduces supplied state and inputs; it
does no storage or remote IO. Connector orchestration uses `DistAgentClientPort`
over the existing outbound binding/client. `DistGroupDirectoryPort` freezes and
loads the exact workflow revision used by a session.

Worker/Connector admin facades are application contracts. Their future engines
implement `DistRuntimeControlPort`; service engines must not import application
contracts just to expose health/drain. Delivery adapters call the application
facades, not repo or core methods directly.

Inbox production and consumption remain separate ports. `ShardFeedPort` remains
the stateful read handle returned by `InboxConsumerPort::open`, owning watch/
unwatch/next and the lifetime of subscriptions and read positions. No combined
`SessionInboxPort` or one-method specification-reader port is introduced.

`SessionStorePort::get_spec(sid)` reads the frozen specification, including after
admission but before any checkpoint exists. It does not acquire ownership or
create/refresh state. Absence is None; storage failure is an error. Moving this
read does not move creation: `AdmitRequest.create_session` and `create_task`
remain part of `InboxProducerPort::admit` and its durable input/activation
guarantee. The API move prescribes neither a new storage layout nor extra runtime
database calls. The total remains 23 service/port traits.

## Admission, identity and queue replacement

Successful `admit` means all required facts are recoverable: immutable session
definition if new, initial Submitted task if new, the logical input and durable
activation. A retry repairs an unfinished admission before returning Duplicate.
The provider owns the atomic transaction or durable reconciliation protocol.
Initial task creation is create-once as part of Gateway admission; a delayed
retry must not replace a task already advanced by Worker. Session specification
and task scope must match the admitted input. An existing frozen specification
cannot be replaced by a retry with newer group configuration.

An identity collision compares canonical business content, not transport
metadata, a queue offset, a retry counter or holder incarnation. Critical Agent
events use admission with stable input identity; intermediate Agent events use
`append_stream`. For the latter, the provider wraps the event into an input
envelope with deterministic identity from its scoped `(did, holder_epoch, seq)`.
Worker keeps the dispatch sequence in the checkpoint and rejects old/repeated
events; sequence progress cannot be reset within one holder generation.

`ShardFeedPort` allows batching multiple sessions. It does not require one queue
per session, one connection per session, or one global commit barrier. Providers
may implement log reads, consumer groups, database polling or push delivery.
Each session is ordered; different sessions may commit independently.

`read_through` is the candidate recovery position. `DeliveryReceipt` is an
ephemeral acknowledgement handle and cannot be serialized into checkpoint state.
Only committed coverage authorizes an acknowledgement. A shared physical
partition can advance its broker acknowledgement only across a contiguous
committed prefix, even if later sessions have already completed. Duplicate
physical records may produce a progress-only batch with no logical inputs.

## Shard identity and session ownership

`ShardOwner { instance, epoch }` is the identity assigned by shard acquisition.
`ShardClaim.owner`, the sealed checkpoint owner, and commit receipts share this
type. A checkpoint stores the shard identity last sealed into that session, not
a live reference to the current shard owner. `SessionOwnership { sid, shard,
owner }` remains the session-scoped processing identity returned by acquisition.
Providers validate that sid belongs to shard and that the exact owner was sealed
into this session; constructing the value from a shard claim or inventing a
larger epoch alone cannot authorize a write.

`ClaimShard.expected_epoch` is the atomic claim precondition: None means the
shard has never been owned, otherwise the current epoch must match exactly.
After validating takeover eligibility, assign epoch 1 or the previous epoch + 1.
Preserve ownership history, reject overflow, and never treat Unknown liveness as
Dead. An exact request-ID/content retry returns the original claim; another
request using the same old epoch conflicts. No independent owner revision is
exposed. Every owner change, including planned handoff, advances the epoch.

`ShardClaim` carries only shard and owner identity, with no generic authority
string. Session acquisition must validate shard, node, incarnation, epoch and
current claim validity against stored facts before sealing; possession of the
Rust value alone grants no processing rights.

`MembershipLease` likewise contains only the registered `Instance`. Registration
binds that instance to an immutable runtime role; retrying an active registration
is idempotent, while changing its role is invalid. Co-located roles use distinct
instance identities. `renew` and `set_draining` atomically require the exact,
current, unexpired registration; an old incarnation cannot update a replacement.
Lost leases return Lost without recreating or reviving membership. Re-registration
after loss requires a fresh incarnation so an old lease cannot become valid again.
Liveness remains Alive/Dead/Unknown: an uncertain read is never proof of death.
No authority string is carried by `SessionOwnership` either. Session operations
validate the exact sealed node/incarnation/epoch and session scope against stored
facts. `CommitReceipt.proof` remains durable evidence for committed effects; it
is not removed with the ownership placeholder strings.

## Checkpoint and effect replay

1. Claim the shard, enumerate **all** pending sessions (including future due
   times), acquire/seal each session and activate its input reader.
2. Acquisition preserves the latest business checkpoint and its batch. A shard
   claim alone does not atomically fence every session. Writes completed before
   the per-session seal are visible; old writes after that seal are rejected.
3. Replay required effects from the last confirmed batch before a new business
   commit. Acquisition reconstructs a validated `CommitReceipt`; a caller-made
   string is not proof of a committed effect.
4. Build a candidate without mutating confirmed state. Commit state, consumed
   position, sequence progress, deadlines and the fixed batch together under
   session/shard scope, exact sealed-owner and `expected_commit_seq` checks.
5. A timeout is `OutcomeUnknown`. Resolve the same commit ID and digest or retry
   that identity; do not invent the next commit or start effects on uncertainty.
6. Replay with the original source commit, effect ID and content digest.
   `AlreadyApplied` requires matching durable evidence. An older event ID or
   equal version by itself is insufficient.

New checkpoints start at `commit_seq=0`. A new business commit requires the
current sequence to equal `expected_commit_seq` and the candidate sequence to
equal that value + 1, with overflow rejected. The check and write are atomic.
Another commit based on the old sequence returns `CommitSeqConflict`; an owner
lost through session sealing is `Fenced`. With valid ownership, an exact
committed ID/content retry returns its original receipt even though its expected
sequence is now old.
Matching only the sequence never proves that this request committed.

Commit admission also validates matching session/owner scope,
supported formats, outbox source commit, deterministic effect IDs
and same-ID content. Providers must validate ownership and commit proofs against stored
facts; public Rust fields are not cryptographic capabilities by themselves.

`AppendEvents` keeps predecessor evidence and applies partially completed batches
in order. A compacted batch only counts as complete with retained completion
evidence. Snapshot publication cannot move coverage or source version backward.
`Superseded` is valid only when later, valid coverage proves the older publication
has been overtaken; it is not a generic response to any higher timestamp.

Result finalization uses two business commits: first AwaitingResult + SaveResult;
then, after confirmed immutable archival, publish the terminal result reference.
If the process dies between them, replaying SaveResult recovers the same reference.
Ownership and recovery metadata writes must not clear that batch. Recovery
attempts are persisted idempotently before risky replay. The control write
validates the sealed owner and `expected_commit_seq`, updates recovery metadata
only, and never advances the business sequence or replaces checkpoint/outbox.
Repeating an attempt ID cannot increment the count again; an advanced head
returns Conflict and a lost owner returns Fenced. Concurrent metadata updates
must be serialized, and stale attempts cannot undo a business-progress reset.
`CheckpointEnvelope.recovery` is a loaded view of this independently managed
metadata, not a field copied into the next business candidate. Only actual
business progress resets the failure streak.

Owner records, checkpoint envelopes, commit receipts and recovery responses
expose no independent revision. Storage adapters may use native versions or
transactions internally. A TBase adapter may encode business writes using
`epoch * 10^9 + commit_seq`, with range/overflow checks, and reserve the epoch
boundary for sealing. Such encoding is not part of the portable API and does not
by itself prove atomic owner/sequence checks or cross-path fencing. Recovery
metadata must use an independent safe control-write protocol; it cannot rewrite
the business checkpoint at the same timestamp or consume a timestamp reserved
for its next commit. These backend guarantees remain to be verified.

Dispatch recheck `Revision` and inbox `ScheduleToken` remain: concurrent control
updates can invalidate a scheduling decision without a new Worker epoch or
business commit. They are separate from owner/checkpoint versioning.

## Schedule race and notification loss

`observe(owner)` precedes the final input check. A new admission invalidates the
observation token **even if the due time was already immediate**. `settle` checks
both owner and token atomically. On `NeedsRecheck`, recompute work/deadlines using
a fresh observation; do not blindly retry the old decision.

The provider can map this token to a row revision or a collection revision. A
collection revision may cause harmless conflicts between unrelated sessions.
Choosing the smaller due time alone cannot safely postpone or remove an entry.

Historical scheduling decisions are not replayable business effects. Wake and
command notifications are hints; durable admission/dispatch indexes must repair
loss. Due scans use bounded fair pagination, not an indefinitely repeated first
page that can starve later members.

## Snapshot, follow and retention

Task snapshot lives in the task record. There is no persisted Reset event.
Append required events before publishing a snapshot that claims their coverage;
publish before trimming anything covered by that snapshot. This is a coverage
rule, not a requirement to update task on every delta.

`restore` returns one fixed cut H: snapshot at S plus events in (S,H]. It retries
if concurrent trimming invalidates that read. `follow` checks replay validity
when delivering, so a later trim produces Gap rather than silently omitting
events. The Gateway then restores a new full view; ordinary event delivery does
not need to query the task record per event.

`trim` accepts a requested boundary, not caller-supplied evidence. The provider
verifies durable snapshot coverage and earlier task archives and coordinates
that check with deletion. `NotCovered` means retain the log. Preserve the
boundary/tombstones and completion evidence required by old effect replay.

## Dispatch and uncertain remote effects

Create registers durable rechecks even on retries. Immutable did collisions
compare content. Worker writes control intent; only the current Connector holder
writes execution. Local actor loss under OwnedByMe requires stopping/joining the
old actor and `resume_owned`, which advances the generation before resuming.

Persist possible-send intent before network IO. NotSent, rejected and unknown
submission outcomes have different retry rules. Declared downstream capabilities
determine whether to retry idempotently, query, resubscribe, or retain uncertainty.
An ownership fence cannot retract an already sent network request.

The durable confirmed-input prefix survives dequeue; replaying an old PushInput
cannot enqueue it again. Keep content evidence as well as the watermark.
An unknown earlier input cannot be skipped to acknowledge a later one.
Cancel acceptance is not cancellation confirmation. Unknown sends/cancels stay
supervised even after a user-facing timeout. `finish` accepts only definitive
terminal outcomes, never Unknown.

## Provider validation matrix

The reusable [harnesses](../../../../../../test-support/bcs-test-support/src/contract/distributed/mod.rs)
contain callable baseline assertions, not a running provider or a claim of full
conformance. Future fixtures must extend them with at least:

| Fault/interleaving | Required outcome |
| --- | --- |
| Admission persisted but activation/notification interrupted | Retry/repair finds input; never acknowledge unrecoverable input |
| Commit applied but response lost | Resolve same ID/digest; no duplicate business commit |
| Two claims use the same expected epoch | Only one new request wins; exact retry retains the same claim |
| Same owner submits competing commits from one sequence | Only one advances; the other returns CommitSeqConflict |
| Recovery attempt repeats or races with business progress | Count once; preserve checkpoint/outbox; stale head cannot undo reset |
| Expired/old-incarnation lease renews or drains | Return Lost; never revive it or change the replacement registration |
| Old owner writes after seal; old effect replays after new owner | Commit rejected; valid historical effects remain idempotent |
| New input races with settle, including equal due score | New activation survives or settle returns NeedsRecheck |
| Partial event append, snapshot lag, trim during restore/follow | Ordered recovery, consistent cut or explicit Gap |
| Snapshot not published or earlier task not archived | Trim returns NotCovered |
| SaveResult succeeds before second checkpoint and process dies | Same result reference recovered before terminal publication |
| Pending input already dequeued; old outbox retries | Input not sent twice by re-enqueue |
| Remote accepted request, local response lost | Query/idempotent recovery where supported; otherwise retain Unknown |
| Notification lost, local actor missing, shared-partition ack | Durable scans recover; no skipped session obligations |

See [ordered tasks](../../../../../../../specs/2026-11-15-distributed-contracts/tasks.md)
and [validation](../../../../../../../specs/2026-11-15-distributed-contracts/validation.md).

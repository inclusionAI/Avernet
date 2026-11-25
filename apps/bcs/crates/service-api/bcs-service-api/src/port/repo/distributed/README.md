# Distributed repository contracts

Status: contracts for an opt-in runtime. Public providers are being implemented
in `bcs-dist-store`; complete runtime assembly and provider acceptance remain
separate. The Rust declarations own the exact method shapes. See the
[reliable Inbox provider](../../../../../../services/bcs-dist-store/src/inbox/README.md)
for retained layout, cold-start health, resource bounds and validation limits.

## Boundaries

| Boundary | Traits | Responsibility |
| --- | --- | --- |
| SessionInbox | `InboxProducerPort`, `InboxConsumerPort`, `ShardFeedPort`, `InboxSchedulingPort`, `AdmissionMaintenancePort`, `AdmissionControlPort`, `AdmissionClosurePort` | Durable admission, replayable per-session input, acknowledgements and recoverable activation |
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
database calls. Output projection is a separate pure core contract, not another
storage interface.

[`DistTaskOutputReaderPort`](../../dist_output.rs) is the Worker-side output
read facade consumed by Gateway. It composes this neutral `TaskReaderPort` with
immutable Blob reads and the injected pure `DistOutputAdapterPort`. Persisted
display snapshots and artifact operations remain neutral; formatted JSON and
`FormattedOutputEvent` are transient read results. Gateway performs no display
parsing or artifact conversion. See the
[A2A formatter](../../../../../../adapters/output/bcs-dist-a2a-output/README.md).

## Admission, identity and queue replacement

Successful `admit` means all required facts are recoverable: the single scoped
admission document, immutable session definition when new, initial Submitted
task when new, logical Inbox input and durable activation. A retry repairs an
unfinished admission before returning `Accepted`; a previously accepted record
returns `Duplicate` with the same receipt. A different business digest under the
same `(scope, input_id)` returns `ContentConflict`.

The provider stores the request and repair phases in one JSON-CAS document keyed
by `(scope, input_id)`. There is no shard-wide admission root, sequence journal,
separate intent document or canonical document. Callers without a confirmed result can retry the
same scoped InputId. An incomplete request may execute, but creating its record
alone does not guarantee unattended completion. An accepted input must be
deliverable without another caller retrying an earlier input. Readers help
resolve blocking Inbox rows; a producer facing a full Session helps an occupied
slot before retrying registration. Neither timeout nor the mere presence of a
row proves acceptance or permits skipping an undecided row.

Admission creation remains create-once for the frozen Session specification and
initial task. A delayed retry cannot replace a task already advanced by Worker,
and a newer workflow revision cannot replace a frozen specification. `schedule`,
`ckpt` and `inbox` remain separate Session-local authorities. `active` and `wake`
are disposable discovery hints, not admission authority.

An identity collision compares canonical business content, not transport
metadata, a queue offset, retry counter or holder incarnation. Critical Agent
events use admission with stable input identity; intermediate Agent events use
`append_stream`. Worker keeps dispatch sequence in the checkpoint and rejects
old or repeated events.

`ShardFeedPort` allows batching multiple Sessions. It does not require one queue
per Session, one connection per Session, or one global commit barrier.

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

Task lifecycle and the pending task queue belong to Worker, independently of the
opaque Runner state. `Checkpoint.runner_state` is `Option<TaskRunnerState>` and
binds state to exactly one active tid. The Worker may discard it during independent
structural-limit finalization and must init fresh state for the next task only after terminal
publication. `TaskProgress` copies frozen output limits from the initial task and
retains cumulative usage, snapshot publication counters/deadline and finalization.
The AwaitingResult commit freezes `TaskFinalization.record` and operation identity;
the Done commit stores the archive reference and terminal event identity. Done
without confirmed `terminal_published` does not authorize starting the next task.
The same immutable snapshot Ref is reused in draft, published content and archive.

Show and Artifact events use stable artifact identities and explicit operations.
Worker and TaskFeed inject the shared
[`OutputProjectionCoreService`](../../../core/dist_projection.rs); event replay
does not charge cumulative diagnostic usage again. The pure implementation documents exact
encoding, limits and recovery in
[bcs-dist-projection](../../../../../../services/bcs-dist-projection/README.md).
Whole-Decision validation must reject terminal SetStatus before any effects;
Completed/Failed/Canceled/Rejected require the two-commit archive protocol.

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

Owner records, checkpoint envelopes and commit receipts expose no storage
revision. `SessionControl.revision` is a logical control fence used by admission
closure and recovery; it is not a backend CAS version. Storage adapters may use native versions or
transactions internally. Phase one uses opaque DocumentCAS versions shared by
sealing, business commits and recovery control. It does not encode a version as
`epoch * 10^9 + commit_seq`. Control writes preserve business sequence and outbox.
Concrete backend atomicity and fencing remain provider acceptance obligations.

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


Worker coordination uses addressable MemberRecord and full ShardRecord views.
ShardClaim/ShardOwner still contain no public storage revision. Exact-version
fencing is implemented in the repository provider. `release_local` drops only a
quiescent local cache; it cannot delete durable state. Peer controls bind exact
instances through the application boundary. See the Worker coordination SPEC and
its implementation record for provider prerequisites and initialization scheme A.

## Session isolation control

[`SessionControlPort`](control.rs) persists frozen risk attempts and separates
Normal/Quarantined/Blocked/Recovering from business state. Ordinary commits bind
the control revision; independent finalization binds the isolation operation.
[`AdmissionClosurePort`](admission.rs) owns recoverable close/cut scanning and
conditional reopen, with actual `TerminalTaskEvidencePort` evidence. Application
authorization must precede `RecoveryAuthorization`; neither an owner token nor a
caller-created completion digest grants recovery rights. See the
[control provider](../../../../../../services/bcs-dist-store/src/coordination/README.md)
and [closure provider](../../../../../../services/bcs-dist-store/src/inbox/README.md)
for retained evidence, IO budgets and validation limits.

## Gateway/Worker integration amendment (2026-11-19)

Gateway delegates stable input to TaskAdmissionCoreService and reads already
formatted output through DistTaskOutputReaderPort. Worker admission initializes
tasks and resumes the single scoped admission document when the same InputId is
retried. Caller intent and full initialized-request integrity are separate checks.
An admission document is not durable activation proof until its decision and Inbox
position are recorded. The provider may return Accepted/Duplicate; both map to the
original successful Gateway receipt.

TaskReaderPort retains neutral display data. WorkerOutputReader resolves it and
applies an injected format adapter to snapshots and explicit artifact operations.
Byte quotas for units/cumulative output/full snapshots are absent this phase;
existing Result/Blob and physical commit framing remain internal.

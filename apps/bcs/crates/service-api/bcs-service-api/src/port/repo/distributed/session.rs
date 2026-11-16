use super::{common::*, ownership::ShardClaim};
use async_trait::async_trait;
use bcs_dist_model::*;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, VecDeque};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum TaskPhase {
    Queued,
    Running,
    AwaitingResult { status: TerminalStatus },
    Done,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskProgress {
    pub tid: Tid,
    pub phase: TaskPhase,
    pub input: Payload,
    pub result_ref: Option<BlobRef>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DispatchSequence {
    pub holder_epoch: HolderEpoch,
    pub sequence: InputSeq,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum EffectKind {
    AppendEvents {
        previous: Option<EventId>,
        events: Vec<OutputEvent>,
    },
    PublishSnapshot {
        snapshot: TaskSnapshot,
    },
    CreateDispatch {
        spec: DispatchSpec,
    },
    RequestCancel {
        did: Did,
        intent: CancelIntent,
    },
    PushInput {
        did: Did,
        input: PendingInput,
    },
    SaveResult {
        record: FinalTaskRecord,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Effect {
    pub id: EffectId,
    pub content_digest: Digest,
    pub kind: EffectKind,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommittedBatch {
    pub commit_id: CommitId,
    pub source_commit: CommitSeq,
    pub content_digest: Digest,
    pub effects: Vec<Effect>,
}

/// State, consumed and outbox are ONE atomic commit. No ephemeral delivery receipt.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Checkpoint {
    pub format_version: u32,
    pub commit_seq: CommitSeq,
    pub consumed: Option<InboxPos>,
    pub tasks: VecDeque<TaskProgress>,
    pub runner_state: RunnerState,
    pub dispatch_seq: BTreeMap<Did, DispatchSequence>,
    pub deadlines: Vec<Deadline>,
    pub view_drafts: BTreeMap<Tid, TaskContent>,
    pub last_event_id: Option<EventId>,
    pub outbox: Option<CommittedBatch>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RecoveryMetadata {
    pub takeover_streak: u32,
    pub last_attempt: Option<RequestId>,
    pub quarantined: bool,
    pub last_active_at: TimestampMillis,
}

/// Loaded view; recovery metadata has an independent control write path and is
/// not part of CommitRequest::candidate. This does not prescribe one storage row.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckpointEnvelope {
    pub owner: ShardOwner,
    pub checkpoint: Checkpoint,
    pub recovery: RecoveryMetadata,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AcquiredSession {
    pub ownership: SessionOwnership,
    pub envelope: CheckpointEnvelope,
    /// Reconstructed proof for replaying the persisted batch; absent before first commit.
    pub last_commit: Option<CommitReceipt>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum AcquireOutcome {
    Acquired(AcquiredSession),
    Fenced,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommitRequest {
    pub owner: SessionOwnership,
    /// Exact current business sequence; candidate must advance it by one.
    pub expected_commit_seq: CommitSeq,
    pub commit_id: CommitId,
    pub content_digest: Digest,
    pub candidate: Checkpoint,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommitOutcome {
    Committed(CommitReceipt),
    AlreadyCommitted(CommitReceipt),
    CommitSeqConflict,
    Fenced,
    ContentConflict,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommitResolution {
    Committed(CommitReceipt),
    DefinitelyNotCommitted,
    OutcomeUnknown,
    Fenced,
    ContentConflict,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecoveryUpdate {
    Recorded { metadata: RecoveryMetadata },
    Conflict,
    Fenced,
}

#[async_trait]
pub trait SessionStorePort: Send + Sync {
    /// Read the immutable specification established by InboxProducerPort::admit,
    /// including before the first checkpoint exists. Does not acquire ownership,
    /// create a session, or refresh its frozen definition. None means absent;
    /// storage failures remain errors, never a missing-spec result.
    async fn get_spec(&self, sid: &Sid) -> Result<Option<SessionSpec>, StoreError>;
    /// Validate shard/node/incarnation/epoch and current claim validity against
    /// stored ownership, then create or seal its ShardOwner into this session,
    /// then load latest preserved business data. Claiming a shard alone cannot
    /// authorize session writes. Preserve commit_seq/outbox on takeover; new
    /// checkpoints start at commit_seq=0. Old post-seal writes fail.
    async fn acquire(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
        initial: Checkpoint,
    ) -> Result<AcquireOutcome, StoreError>;
    async fn load(&self, sid: &Sid) -> Result<Option<CheckpointEnvelope>, StoreError>;
    /// Atomically validate sid-to-shard membership, the sealed owner's exact
    /// node/incarnation/epoch, and expected_commit_seq AND
    /// write the full candidate with commit_seq=expected_commit_seq+1 (no overflow).
    /// Same-owner competing commits at an advanced head return CommitSeqConflict;
    /// a lost owner is Fenced. With valid ownership, an exact ID/content retry
    /// returns its receipt despite its old expected sequence; equality alone is not
    /// proof of success. Preserve independently updated recovery metadata, except
    /// for an atomic failure-streak reset when this commit makes business progress.
    async fn commit(&self, request: CommitRequest) -> Result<CommitOutcome, StoreError>;
    async fn resolve_commit(
        &self,
        owner: &SessionOwnership,
        commit_id: &CommitId,
        digest: &Digest,
    ) -> Result<CommitResolution, StoreError>;
    /// Before risky replay, atomically validate the sealed owner and business head
    /// and record the attempt once. Repeated attempt IDs do not increment again;
    /// an advanced business head returns Conflict, a lost owner Fenced.
    /// Update only control metadata, never owner/commit_seq/checkpoint/outbox.
    /// Providers serialize metadata updates without exposing another revision;
    /// stale attempts cannot undo a progress reset. Metadata writes are not progress.
    async fn record_recovery_attempt(
        &self,
        owner: &SessionOwnership,
        expected_commit_seq: CommitSeq,
        attempt: RequestId,
    ) -> Result<RecoveryUpdate, StoreError>;
}

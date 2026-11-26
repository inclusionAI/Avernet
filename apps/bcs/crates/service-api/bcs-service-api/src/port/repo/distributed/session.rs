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
    /// Committed Runner reductions, excluding replay and consumed-only work.
    #[serde(default)]
    pub steps: u32,
    pub tid: Tid,
    pub phase: TaskPhase,
    pub input: Payload,
    pub result_ref: Option<BlobRef>,
    /// Copied from the create-once task snapshot, never from current host config.
    pub output_limits: OutputLimits,
    pub output_usage: OutputUsage,
    pub publication: SnapshotProgress,
    pub finalization: Option<TaskFinalization>,
}

/// Frozen by the AwaitingResult commit. Archive recovery keeps the same record
/// and identity. Done alone cannot start the next task: terminal publication
/// must first be confirmed, with its stable output event identity retained.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskFinalization {
    pub operation_id: RequestId,
    pub record: FinalTaskRecord,
    pub terminal_event_id: Option<EventId>,
    pub terminal_published: bool,
}

/// Private reducer state belongs to exactly one active task. None is valid
/// between tasks and during independent finalization after discarding that state.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskRunnerState {
    pub tid: Tid,
    pub state: RunnerState,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DispatchSequence {
    /// Exact task ownership; hierarchical-looking IDs are not a scope proof.
    pub tid: Tid,
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
    /// Frozen before the first Blob/commit IO. Diagnostic age only; never lease
    /// authority. Omission preserves hashes of earlier coordination-only data.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_at: Option<TimestampMillis>,
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
    pub runner_state: Option<TaskRunnerState>,
    pub dispatch_seq: BTreeMap<Did, DispatchSequence>,
    pub deadlines: Vec<Deadline>,
    /// Retained identities for this Task lifetime, including canceled/fired
    /// occurrences. Reusing an absent identity cannot arm a second timeout.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub deadline_history: BTreeMap<DeadlineId, Deadline>,
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
    /// Current graceful handoff still protects the exact source owner.
    Deferred {
        check_after: std::time::Duration,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionTransferOutcome {
    Transferred,
    AlreadyTransferred,
    Fenced,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CommitPurpose {
    Ordinary { control_revision: u64 },
    Isolation { operation_id: RequestId },
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommitRequest {
    pub purpose: CommitPurpose,
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

/// Enforced JSON encoding bounds for a Session provider, not a measurement of
/// the underlying database. The host must confirm that its DocumentCas adapter
/// can store this complete document, including any private storage encoding.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SessionCheckpointCapacity {
    /// Complete stored document, including checkpoint, control and storage wrapper.
    pub max_document_bytes: usize,
    /// Maximum bytes outside Checkpoint, including the private storage wrapper.
    pub max_envelope_bytes: usize,
}
impl Default for SessionCheckpointCapacity {
    fn default() -> Self {
        Self { max_document_bytes: 1024 * 1024, max_envelope_bytes: 64 * 1024 }
    }
}

#[async_trait]
pub trait SessionStorePort: Send + Sync {
    /// None means this provider has not adopted the bounded Worker checkpoint
    /// contract. WorkerProcessor must reject such a composition at startup.
    fn checkpoint_capacity(&self) -> Option<SessionCheckpointCapacity> {
        None
    }

    /// Maximum local session reservations supported by this port instance.
    /// None means no local cache limit. Runtime wiring must reject a configured
    /// active-session limit above Some(capacity), and apply backpressure before
    /// issuing acquisitions. Unknown writes still occupy their reservations.
    fn local_session_capacity(&self) -> Option<usize> {
        None
    }

    /// Release a quiescent local cache entry only. Never delete durable state or
    /// revoke another owner. The caller must resolve current-round/commit/effect
    /// and scheduling operations first. Providers without a cache need no work.
    async fn release_local(&self, _owner: &SessionOwnership) -> Result<(), StoreError> {
        Ok(())
    }
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
    /// Source is quiescent before calling. Preserve the latest full envelope;
    /// never overwrite with a caller snapshot. Uncertain attempts retain identity.
    async fn handoff_session(
        &self,
        source: &SessionOwnership,
        target: &ShardClaim,
        request_id: &RequestId,
    ) -> Result<SessionTransferOutcome, StoreError>;
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

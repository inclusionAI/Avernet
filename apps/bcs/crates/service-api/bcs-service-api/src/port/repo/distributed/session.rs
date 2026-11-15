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

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckpointEnvelope {
    pub owner: SessionOwner,
    pub revision: Revision,
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
    pub expected_revision: Revision,
    pub commit_id: CommitId,
    pub content_digest: Digest,
    pub candidate: Checkpoint,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommitOutcome {
    Committed(CommitReceipt),
    AlreadyCommitted(CommitReceipt),
    RevisionConflict,
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
    Recorded {
        revision: Revision,
        metadata: RecoveryMetadata,
    },
    Conflict,
    Fenced,
}

#[async_trait]
pub trait SessionStorePort: Send + Sync {
    /// Validate claim, create or seal owner, then load latest preserved business data.
    /// Old pre-seal commits survive; old post-seal writes fail. No outbox clearing.
    async fn acquire(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
        initial: Checkpoint,
    ) -> Result<AcquireOutcome, StoreError>;
    async fn load(&self, sid: &Sid) -> Result<Option<CheckpointEnvelope>, StoreError>;
    /// Atomic owner/revision check AND full candidate write. An equal version alone
    /// is not idempotency evidence: check commit identity and content digest.
    async fn commit(&self, request: CommitRequest) -> Result<CommitOutcome, StoreError>;
    async fn resolve_commit(
        &self,
        owner: &SessionOwnership,
        commit_id: &CommitId,
        digest: &Digest,
    ) -> Result<CommitResolution, StoreError>;
    /// Before replay; updates metadata/revision, preserves batch. Only business
    /// progress resets the streak; repeated metadata writes are not progress.
    async fn record_recovery_attempt(
        &self,
        owner: &SessionOwnership,
        expected: Revision,
        attempt: RequestId,
    ) -> Result<RecoveryUpdate, StoreError>;
}

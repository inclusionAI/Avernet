use bcs_dist_model::{
    CommitId, CommitSeq, Digest, EffectId, InboxPos, Instance, PageToken, ShardId, Sid, WorkerEpoch,
};
use serde::{Deserialize, Serialize};
use std::num::NonZeroU32;

/// Only machine-safe diagnostic codes, never credentials or raw provider bodies.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum StoreError {
    #[error("operation definitely not applied: {code}")]
    NotApplied { code: String },
    #[error("operation outcome unknown: {operation_id}")]
    OutcomeUnknown { operation_id: String },
    #[error("invalid contract input: {code}")]
    Invalid { code: String },
    #[error("unsupported stored format: {format}")]
    UnsupportedFormat { format: u32 },
}

/// Opaque dispatch recheck version, not a shard or checkpoint revision.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Revision(pub String);

/// Shard-assigned identity. A checkpoint stores the identity sealed into that
/// session, which may lag the shard's current owner until session acquisition.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ShardOwner {
    pub instance: Instance,
    pub epoch: WorkerEpoch,
}

/// Session processing identity returned after acquisition/sealing. Providers
/// validate sid-to-shard membership and the exact owner sealed in the checkpoint;
/// a caller-created value or a larger epoch alone cannot authorize a write.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionOwnership {
    pub sid: Sid,
    pub shard: ShardId,
    pub owner: ShardOwner,
}

/// Not serialized into checkpoint state. A string alone is not proof of commit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitReceipt {
    pub sid: Sid,
    pub owner: ShardOwner,
    pub commit_id: CommitId,
    pub content_digest: Digest,
    pub sequence: CommitSeq,
    pub consumed: Option<InboxPos>,
    pub proof: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EffectSource {
    pub commit: CommitReceipt,
    pub effect_id: EffectId,
    pub content_digest: Digest,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CompletionCertificate {
    pub sid: Sid,
    pub covered_commit: CommitSeq,
    pub proof: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PageRequest {
    pub after: Option<PageToken>,
    pub limit: NonZeroU32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Page<T> {
    pub items: Vec<T>,
    pub next: Option<PageToken>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IdempotentWrite {
    Applied,
    AlreadyApplied,
    ContentConflict,
}

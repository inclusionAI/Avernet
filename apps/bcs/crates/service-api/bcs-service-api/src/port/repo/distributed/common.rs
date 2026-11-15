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

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Revision(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionOwner {
    pub instance: Instance,
    pub epoch: WorkerEpoch,
}

/// Runtime capability issued by acquisition; providers validate its authority.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionOwnership {
    pub sid: Sid,
    pub shard: ShardId,
    pub owner: SessionOwner,
    pub authority: String,
}

/// Not serialized into checkpoint state. A string alone is not proof of commit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitReceipt {
    pub sid: Sid,
    pub owner: SessionOwner,
    pub commit_id: CommitId,
    pub content_digest: Digest,
    pub sequence: CommitSeq,
    pub revision: Revision,
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

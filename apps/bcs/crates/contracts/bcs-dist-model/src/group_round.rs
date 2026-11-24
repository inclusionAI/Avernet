//! Candidate strict-group round records. D0 is not frozen and no runtime writes
//! these records yet. Decoding identifies a record, never authorizes execution:
//! the owning store must verify scope, sealed owner, commit and referenced data.
use crate::{BlobRef, CommitId, CommitSeq, Digest, GroupId, RequestId, Sid, TaskScope,
    TerminalStatus, Tid, TimestampMillis, WorkflowRef};
use serde::{Deserialize, Serialize};
use std::num::NonZeroU64;

/// Only the explicitly supported format may be decoded; legacy records are not
/// upgraded by filling missing ownership or source fields.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "u32", into = "u32")]
pub enum RoundSchemaVersion {
    V1,
}

impl TryFrom<u32> for RoundSchemaVersion {
    type Error = &'static str;

    fn try_from(value: u32) -> Result<Self, Self::Error> {
        match value {
            1 => Ok(Self::V1),
            _ => Err("unsupported group round schema version"),
        }
    }
}

impl From<RoundSchemaVersion> for u32 {
    fn from(_: RoundSchemaVersion) -> Self { 1 }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GroupRoundSource {
    GroupA2a,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RoundKey {
    pub scope: TaskScope,
    pub group: GroupId,
    pub sid: Sid,
    pub tid: Tid,
    /// A strict round starts at one; zero cannot alias legacy session state.
    pub generation: NonZeroU64,
}

/// Transport-independent ordered input. HTTP DTOs and file input are excluded.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum RoundPart {
    Text { text: String },
    Data { data: serde_json::Value },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RoundInput {
    pub schema_version: RoundSchemaVersion,
    pub source: GroupRoundSource,
    pub key: RoundKey,
    pub ordered_parts: Vec<RoundPart>,
    pub workflow_ref: WorkflowRef,
    /// Immutable, digest-checked history boundary, not a mutable latest pointer.
    /// The owner must pin its contents before admitting the round.
    pub history_cut: BlobRef,
    pub execution_deadline: TimestampMillis,
}

/// Original normalization and return semantics survive later default changes.
/// The normalization reference owns schema/defaults/extensions and explicit
/// context presence. The request key is (scope, group, request_id), not Sid.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AcceptedRound {
    pub input: RoundInput,
    pub request_id: RequestId,
    pub request_digest: Digest,
    pub normalization_ref: BlobRef,
    pub return_configuration: serde_json::Value,
}

/// Busy is a rejected competition, not a reservation or queued Task. Storage
/// errors and unconfirmed writes are errors, never Busy or an absent request.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Admission {
    New { accepted: AcceptedRound },
    Replay { accepted: AcceptedRound },
    Busy,
    Conflict,
    Expired,
}

/// Persisted candidate proof descriptor, NOT a bearer capability. A reader must
/// verify this exact record against the authoritative commit/cold-proof store.
/// closure_ref covers every dispatch attempt (including an explicitly empty
/// set); a missing reference never means no work remains. Failed/canceled rounds
/// also keep a bounded result record, so result_ref is deliberately required.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TerminalProof {
    pub schema_version: RoundSchemaVersion,
    pub key: RoundKey,
    pub commit_id: CommitId,
    pub commit_seq: CommitSeq,
    pub status: TerminalStatus,
    pub result_ref: BlobRef,
    pub closure_ref: BlobRef,
}

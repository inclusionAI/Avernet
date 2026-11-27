use crate::ids::*;
use crate::{ArtifactUpdate, OutputLimits, OutputUsage, RoundPart};
use serde::{Deserialize, Serialize};
use std::num::NonZeroU32;

/// One ordered artifact part of a task's terminal products. The part order is
/// the caller's stable publication order; identity is unique per
/// `(tid, artifact_id)` and never renumbered by storage.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ArtifactPart {
    pub artifact_id: ArtifactId,
    pub order: NonZeroU32,
    pub part: RoundPart,
}

impl ArtifactPart {
    /// Expand a final answer's typed parts into the archive's flat, ordered
    /// rows. The length prefix and index distinguish each part without losing
    /// the originating artifact's identity.
    pub fn from_final_answer(
        artifact: &ArtifactId,
        content: &serde_json::Value,
        offset: usize,
    ) -> Option<Vec<Self>> {
        let parts: Vec<RoundPart> = serde_json::from_value(content.clone()).ok()?;
        if parts.is_empty() {
            return None;
        }
        parts.into_iter().enumerate().map(|(index, part)| {
            let order = offset.checked_add(index)?.checked_add(1)?;
            Some(Self {
                artifact_id: ArtifactId(format!("final:{}:{}:{index}", artifact.0.len(), artifact.0)),
                order: NonZeroU32::new(u32::try_from(order).ok()?)?,
                part,
            })
        }).collect()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskStatus {
    Submitted,
    Working,
    InputRequired,
    AuthRequired,
    Completed,
    Failed,
    Canceled,
    Rejected,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TerminalStatus {
    Completed,
    Failed,
    Canceled,
    Rejected,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InteractionKind {
    HumanInput,
    Authorization,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Interaction {
    pub id: InteractionId,
    pub did: Did,
    pub kind: InteractionKind,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskContent {
    pub status: TaskStatus,
    pub interactions: Vec<Interaction>,
    /// Full display state (or immutable reference); excludes ephemeral thinking.
    pub snapshot: Payload,
    pub result_ref: Option<BlobRef>,
}

/// State, content and event coverage are published as one atomic projection.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskSnapshot {
    pub schema_version: u32,
    pub scope: TaskScope,
    pub sid: Sid,
    pub tid: Tid,
    pub content: TaskContent,
    pub output_limits: OutputLimits,
    /// Usage at this published cut; readers fold later events without charging
    /// this again. The checkpoint remains authoritative for admission of output.
    pub output_usage: OutputUsage,
    pub snapshot_event_id: Option<EventId>,
    pub source_commit: CommitSeq,
    pub created_at: TimestampMillis,
    pub updated_at: TimestampMillis,
}

/// A reconstructed cut; not a claim that the stored snapshot itself is current.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskView {
    pub scope: TaskScope,
    pub sid: Sid,
    pub tid: Tid,
    pub content: TaskContent,
    pub covered_through: Option<EventId>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OutputClass {
    Output,
    Tool,
    Thinking,
    /// A strict FSM final artifact: its Upsert contains ordered `RoundPart`s.
    FinalAnswer,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum OutputBody {
    Status {
        status: TaskStatus,
        interactions: Vec<Interaction>,
    },
    Artifact {
        node: NodeId,
        artifact_id: ArtifactId,
        class: OutputClass,
        update: ArtifactUpdate,
    },
    Terminal {
        status: TerminalStatus,
        result_ref: BlobRef,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OutputEvent {
    pub id: EventId,
    pub tid: Tid,
    pub body: OutputBody,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FinalTaskRecord {
    pub schema_version: u32,
    pub scope: TaskScope,
    pub sid: Sid,
    pub tid: Tid,
    pub status: TerminalStatus,
    pub snapshot: Payload,
    pub result: Payload,
    pub source_commit: CommitSeq,
    pub finalized_at: TimestampMillis,
}

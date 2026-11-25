use crate::ids::*;
use crate::{ArtifactUpdate, OutputLimits, OutputUsage};
use serde::{Deserialize, Serialize};

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

use crate::ids::*;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionKind {
    FsmGroup,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkflowRef {
    pub definition_id: String,
    pub revision: String,
    pub digest: Digest,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AgentBinding {
    pub agent: AgentId,
    pub binding_version: BindingVersion,
    pub credential_ref: CredentialRef,
}

/// Capabilities are explicit; a transport method's presence is not a guarantee.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DownstreamCapabilities {
    pub idempotent_start: bool,
    pub idempotent_input: bool,
    pub query_by_request: bool,
    pub query_by_task: bool,
    pub subscribe: bool,
    pub resume_events: bool,
    pub cancel: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ParticipantBinding {
    pub node: NodeId,
    pub target: AgentBinding,
    pub pool: PoolId,
    pub capabilities: DownstreamCapabilities,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionLimits {
    pub max_queued_tasks: u32,
    pub max_task_steps: u32,
    pub task_timeout_millis: u64,
    pub max_inline_bytes: u32,
}

/// Frozen at admission. Identifiers do not confer authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionSpec {
    pub schema_version: u32,
    pub sid: Sid,
    pub shard: ShardId,
    pub scope: TaskScope,
    pub kind: SessionKind,
    pub group: GroupId,
    pub workflow: WorkflowRef,
    pub participants: Vec<ParticipantBinding>,
    pub limits: SessionLimits,
    pub digest: Digest,
    pub created_at: TimestampMillis,
}

use crate::ids::*;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum SessionTarget {
    DirectAgent { agent: AgentId },
    Group { group: GroupId },
}

impl SessionTarget {
    pub fn id(&self) -> &str {
        match self {
            Self::DirectAgent { agent } => &agent.0,
            Self::Group { group } => &group.0,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CollaborationMode {
    DirectA2a,
    StateMachine,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModeConfigEnvelope {
    pub mode: CollaborationMode,
    pub mode_version: u32,
    pub revision: String,
    pub digest: Digest,
    pub payload: Payload,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuthorizedAgent {
    pub agent: AgentId,
    pub pool: PoolId,
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
pub struct SessionLimits {
    pub max_queued_tasks: u32,
    pub max_task_steps: u32,
    pub task_timeout_millis: u64,
    pub max_inline_bytes: u32,
}

pub const SESSION_SCHEMA_VERSION: u32 = 2;

/// Frozen at admission. Identifiers do not confer authority.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionSpec {
    pub schema_version: u32,
    pub sid: Sid,
    pub shard: ShardId,
    pub scope: TaskScope,
    pub target: SessionTarget,
    pub mode_envelope: ModeConfigEnvelope,
    pub authorized_agents: Vec<AuthorizedAgent>,
    pub limits: SessionLimits,
    pub digest: Digest,
    pub created_at: TimestampMillis,
}

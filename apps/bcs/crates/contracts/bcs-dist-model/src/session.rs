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
    /// Memory-isolation evidence for the bound target (0.1.2): the target
    /// operates in a verified isolated memory domain, or shared global memory
    /// is disabled. Fail-closed serde default: a payload written without the
    /// field (0.1.1 and earlier) reads as `false` — isolation unverified —
    /// so publication gates reject it rather than inferring it.
    #[serde(default)]
    pub isolated_memory: bool,
    /// Negotiated persistent dedupe (0.1.3, plan T6): the target durably
    /// guarantees, for a recorded proof's guarantee window, that re-sending
    /// a request id it already accepted returns the same task instead of
    /// running a second one. `idempotent_start`/`idempotent_input` alone are
    /// only declared idempotency without that guarantee, and a strict source
    /// must not resend on them. Fail-closed serde default like the others.
    #[serde(default)]
    pub persistent_dedupe: bool,
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

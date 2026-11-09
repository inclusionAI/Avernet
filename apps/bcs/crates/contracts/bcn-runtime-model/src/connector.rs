use serde::{Deserialize, Serialize};
use crate::*;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CancellationSupport { Unsupported, SingleTask, ExclusiveContext }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum IdempotencySupport {
    Unsupported,
    /// A bounded window alone never authorizes replay of MAY_HAVE_SENT.
    Window { key: String, window_ms: u64 },
    OperationLifetime { key: String },
    EnforcedExpiry { key: String },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConnectorCapabilities {
    pub streaming: bool,
    pub query: bool,
    pub subscribe: bool,
    pub input_continuation: bool,
    pub cancellation: CancellationSupport,
    pub idempotency: IdempotencySupport,
}
/// Opaque reference only; resolved credentials must never be serialized in state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CredentialRef { pub name: String, pub version: String }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FrozenTargetBinding {
    pub target: AgentId,
    pub binding_version: String,
    /// Opaque catalog binding; the external client owns endpoint/protocol resolution.
    pub binding_id: String,
    pub capabilities: ConnectorCapabilities,
    pub credential_ref: Option<CredentialRef>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionHandle {
    pub target: AgentId,
    pub binding_version: String,
    pub remote_task_id: Option<String>,
    pub remote_context_id: Option<String>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentSendRequest {
    pub dispatch_id: DispatchId,
    pub operation_id: OperationId,
    pub binding: FrozenTargetBinding,
    pub message: Message,
    pub handle: Option<ExecutionHandle>,
    /// Immutable downstream-enforced expiry, when the capability supports it.
    pub expires_at_ms: Option<u64>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AgentSendResponse { Message {
    parts: Vec<MessagePart>,
    /// Actual server context from a direct Message response; no remote task is invented.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    handle: Option<ExecutionHandle>,
}, Task { snapshot: ExecutionSnapshot } }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ExecutionSnapshot { pub handle: ExecutionHandle, pub status: TaskStatus, pub output: Vec<MessagePart>, pub error: Option<RuntimeError> }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AgentEvent {
    Accepted { handle: ExecutionHandle },
    Delta { text: String },
    Snapshot { snapshot: ExecutionSnapshot },
    InputRequired { handle: ExecutionHandle, prompt: Vec<MessagePart> },
    AuthRequired { handle: ExecutionHandle },
    Completed { output: Vec<MessagePart> },
    Failed { error: RuntimeError },
    /// Terminal failure without a remote handle, preserving bounded partial output.
    FailedWithOutput { error: RuntimeError, output: Vec<MessagePart> },
    /// Local observation stopped; remote termination is not established.
    ObservationFailed { error: RuntimeError, output: Vec<MessagePart> },
    Aborted { affected_remote_task_ids: Vec<String> },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AgentCancelRequest {
    pub operation_id: OperationId,
    pub handle: ExecutionHandle,
    pub exclusive_context: bool,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CancelOutcome {
    Confirmed { affected_remote_task_ids: Vec<String> },
    AlreadyTerminal,
    Unknown,
}

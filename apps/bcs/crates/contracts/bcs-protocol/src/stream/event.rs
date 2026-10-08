//! Engine-neutral top-level streaming event types.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::agent::{ApprovalData, LifecycleData, PhaseData, ThinkingData, ToolData};

#[derive(Debug, Clone)]
pub enum StreamEvent {
    Agent(AgentEvent),
    Chat(ChatEvent),
    Interaction(InteractionEvent),
    Ping { ts: Option<u64> },
    Unknown { event: String, raw: Value },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InteractionPhase {
    Requested,
    Resolved,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InteractionKind {
    Exec,
    AskUser,
    ModeSwitch,
}

/// Common Provider 2.0 interaction envelope.
///
/// Kind-specific fields intentionally remain in `raw`: BCS routes and tracks
/// the interaction, while Provider remains authoritative for its engine-native
/// options and resolution payload.
#[derive(Debug, Clone)]
pub struct InteractionEvent {
    pub run_id: String,
    pub seq: Option<u64>,
    pub ts: Option<u64>,
    /// Canonical BCS session id. Legacy Provider `sessionKey` input is
    /// normalized to this field by the boundary parser.
    pub session_id: Option<String>,
    pub phase: InteractionPhase,
    pub interaction_id: String,
    pub kind: InteractionKind,
    pub raw: Value,
}

#[derive(Debug, Clone)]
pub struct AgentEvent {
    /// Engine-internal run id (opaque; NOT used for correlation).
    pub run_id: String,
    pub seq: Option<u64>,
    pub ts: Option<u64>,
    /// Canonical BCS session id. Legacy Provider `sessionKey` input is
    /// normalized to this field by the boundary parser.
    pub session_id: Option<String>,
    pub data: AgentData,
    pub raw: Value,
}

#[derive(Debug, Clone)]
pub enum AgentData {
    Tool(ToolData),
    Thinking(ThinkingData),
    Assistant { raw: Value },
    Error { raw: Value },
    Approval(ApprovalData),
    Lifecycle(LifecycleData),
    Phase(PhaseData),
    Unknown { stream: String, raw: Value },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChatState {
    Delta,
    Final,
    Aborted,
    Error,
}

#[derive(Debug, Clone)]
pub struct ChatEvent {
    pub run_id: String,
    pub seq: Option<u64>,
    pub ts: Option<u64>,
    pub state: ChatState,
    /// Canonical BCS session id. Legacy Provider `sessionKey` input is
    /// normalized to this field by the boundary parser.
    pub session_id: Option<String>,
    pub content: Option<String>,
    pub delta_text: Option<String>,
    pub stop_reason: Option<String>,
    pub error_message: Option<String>,
    pub error_kind: Option<String>,
    pub error_code: Option<String>,
    pub message: Option<Value>,
    pub raw: Value,
}

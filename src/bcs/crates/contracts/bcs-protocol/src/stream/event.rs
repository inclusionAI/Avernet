//! Engine-neutral top-level streaming event types.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::agent::{ApprovalData, LifecycleData, PhaseData, ThinkingData, ToolData};

#[derive(Debug, Clone)]
pub enum StreamEvent {
    Agent(AgentEvent),
    Chat(ChatEvent),
    Ping { ts: Option<u64> },
    Unknown { event: String, raw: Value },
}

#[derive(Debug, Clone)]
pub struct AgentEvent {
    /// Engine-internal run id (opaque; NOT used for correlation).
    pub run_id: String,
    pub seq: Option<u64>,
    pub ts: Option<u64>,
    pub session_key: Option<String>,
    pub data: AgentData,
    pub raw: Value,
}

#[derive(Debug, Clone)]
pub enum AgentData {
    Tool(ToolData),
    Thinking(ThinkingData),
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
    pub state: ChatState,
    pub session_key: Option<String>,
    pub delta_text: Option<String>,
    pub stop_reason: Option<String>,
    pub error_message: Option<String>,
    pub error_kind: Option<String>,
    pub message: Option<Value>,
    pub raw: Value,
}

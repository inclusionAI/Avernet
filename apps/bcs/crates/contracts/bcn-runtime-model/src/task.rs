use serde::{Deserialize, Serialize};
use serde_json::Value;
use crate::*;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Message {
    pub message_id: MessageId,
    pub context_id: Option<SessionId>,
    pub task_id: Option<TaskId>,
    pub parts: Vec<MessagePart>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum MessagePart { Text { text: String }, Data { value: Value }, File { uri: String, media_type: String } }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SubmitRequest {
    pub target: ExecutionTarget,
    pub message: Message,
    /// Selects an original active HumanInput; absent is inferred only when unique.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interaction_id: Option<InteractionId>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskStatus { Submitted, Working, InputRequired, AuthRequired, PendingResult, Completed, Failed, Cancelled }
impl TaskStatus {
    pub fn is_terminal(self) -> bool { matches!(self, Self::Completed | Self::Failed | Self::Cancelled) }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ActiveInteraction { pub interaction_id: InteractionId, pub prompt: String }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskView {
    /// Current unresolved questions, separate from produced artifacts.
    #[serde(default)]
    pub active_interactions: Vec<ActiveInteraction>,
    pub task_id: TaskId,
    pub session_id: SessionId,
    pub target: ExecutionTarget,
    pub status: TaskStatus,
    pub output: Vec<MessagePart>,
    pub error: Option<RuntimeError>,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CancelCommand { pub task_id: TaskId, pub operation_id: OperationId }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TaskQuery { pub target: Option<ExecutionTarget>, pub session_id: Option<SessionId>, pub cursor: Option<String>, pub limit: u32 }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskPage { pub items: Vec<TaskView>, pub next_cursor: Option<String>, pub total_size: u64 }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Page<T> { pub items: Vec<T>, pub next_cursor: Option<String> }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventCursor { pub epoch: u64, pub sequence: u64 }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskEvent { pub id: EventId, pub task_id: TaskId, pub cursor: EventCursor, pub kind: TaskEventKind }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum TaskEventKind { Reset { snapshot: TaskView }, Delta { text: String }, Domain { event: DomainEvent } }

/// Frozen and fenced before the authoritative result write; its payload is immutable.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ResultIntent {
    pub result_id: ResultId,
    pub task_id: TaskId,
    pub node_id: Option<String>,
    pub attempt: u32,
    pub status: TaskStatus,
    pub output: Vec<MessagePart>,
    pub error: Option<RuntimeError>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SavedResult { pub intent: ResultIntent, pub saved_at_ms: u64 }

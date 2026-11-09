use std::collections::BTreeMap;
use serde::{Deserialize, Serialize};
use crate::*;
pub use bcs_domain::collaboration::StateMachineDefinition;

pub const CHECKPOINT_SCHEMA_VERSION: u32 = 1;
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunnerKind { DirectA2a, StructuredGroup }
/// Definition and bindings are frozen at admission, not reloaded during recovery.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionSpec {
    pub session_id: SessionId,
    pub target: ExecutionTarget,
    pub kind: RunnerKind,
    pub definition: Option<StateMachineDefinition>,
    pub bindings: BTreeMap<String, FrozenTargetBinding>,
    pub policy: ExecutionPolicy,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionPolicy {
    pub execution_timeout_ms: u64,
    pub human_timeout_ms: u64,
    pub judge_available: bool,
    pub max_pending_tasks: u32,
    pub max_graph_nodes: u32,
    pub max_agents: u32,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunnerState {
    pub schema_version: u32,
    pub kind: RunnerKind,
    pub session_id: SessionId,
    pub target: ExecutionTarget,
    pub current_task: Option<TaskExecution>,
    pub pending_tasks: Vec<QueuedTask>,
    pub direct_context: Option<ExecutionHandle>,
    pub context_blocked: bool,
    pub graph: BTreeMap<String, NormalizedNode>,
    pub bindings: BTreeMap<String, FrozenTargetBinding>,
    pub policy: ExecutionPolicy,
}
/// Normalized execution graph, independent from the legacy executor.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NormalizedNode {
    pub id: String,
    pub kind: NormalizedNodeKind,
    pub predecessors: Vec<String>,
    pub successors: Vec<String>,
    pub timeout_ms: u64,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum NormalizedNodeKind {
    BotTask { binding_key: String, prompt: String },
    HumanInput { prompt: String },
    Judge { prompt: String, branches: BTreeMap<String, Vec<String>> },
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QueuedTask {
    pub task_id: TaskId,
    pub message: Message,
    pub admitted_at_ms: u64,
    /// Cancel bypasses dispatch, but remains bounded in the queue until saved.
    #[serde(default)]
    pub terminal_intent: Option<ResultIntent>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskExecution {
    pub task_id: TaskId,
    pub message: Message,
    pub status: TaskStatus,
    pub nodes: BTreeMap<String, NodeExecution>,
    pub result_intent: Option<ResultIntent>,
    pub cancel_requested: bool,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NodeExecution {
    pub phase: NodePhase,
    pub attempt: u32,
    pub dispatch_id: Option<DispatchId>,
    pub handle: Option<ExecutionHandle>,
    pub interaction_id: Option<InteractionId>,
    pub selected_branch: Option<String>,
    pub result_intent: Option<ResultIntent>,
    pub saved_result: Option<SavedResult>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NodePhase { Pending, Running, WaitingInput, WaitingJudge, PendingResult, Completed, Failed, Skipped, Cancelled }
/// IDs and time arrive as data; reducers must not read clocks or create random IDs.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionInput { pub event_id: EventId, pub at_ms: u64, pub kind: SessionInputKind }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum SessionInputKind {
    Submit { task: QueuedTask },
    Agent { task_id: TaskId, node_id: Option<String>, dispatch_id: DispatchId, event: AgentEvent },
    Cancellation { task_id: TaskId, node_id: Option<String>, dispatch_id: DispatchId, operation_id: OperationId, outcome: CancelOutcome },
    JudgeDecided { task_id: TaskId, node_id: String, branch: String },
    HumanReply { task_id: TaskId, interaction_id: InteractionId, caller: CallerContext, message: Message },
    Cancel { command: CancelCommand },
    TimerFired { timer_id: TimerId, #[serde(default)] output: Vec<MessagePart> },
    ResultSaved { result: SavedResult },
    EffectFailed { operation_id: OperationId, error: RuntimeError },
    /// Internal bounded fallback for an accepted input that cannot be checkpointed.
    /// The Worker commits this reduction at the original inbox position.
    TaskFailure { task_id: TaskId, error: RuntimeError, output: Vec<MessagePart> },
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Transition {
    pub next_state: RunnerState,
    pub effects: Vec<EffectIntent>,
    pub events: Vec<DomainEvent>,
    pub timer_changes: Vec<TimerChange>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum EffectIntent {
    /// Frozen outbox correlation survives task switches and checkpoint recovery.
    SendAgent { task_id: TaskId, node_id: Option<String>, request: AgentSendRequest },
    /// Cancellation owns a separate dispatch, operation and frozen target binding.
    CancelAgent { task_id: TaskId, node_id: Option<String>, dispatch_id: DispatchId, binding: FrozenTargetBinding, request: AgentCancelRequest },
    Judge { request: JudgeRequest },
    SaveResult { intent: ResultIntent },
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JudgeRequest {
    pub operation_id: OperationId,
    pub task_id: TaskId,
    pub node_id: String,
    pub prompt: String,
    pub choices: Vec<String>,
    pub inputs: BTreeMap<String, SavedResult>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DomainEvent { pub event_id: EventId, pub task_id: TaskId, pub kind: DomainEventKind }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DomainEventKind {
    TaskStatusChanged { status: TaskStatus },
    NodeCompleted { node_id: String, result_id: ResultId },
    InputRequired { interaction_id: InteractionId, prompt: String },
    BranchSelected { node_id: String, branch: String },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Deadline { pub timer_id: TimerId, pub at_ms: u64 }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TimerChange { Upsert(Deadline), Cancel(TimerId) }

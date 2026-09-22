//! Local message facts. Delivered means persisted, never externally delivered.
use super::{AppendEventRecord, MarkHumanNodeRunningCommand};
use bcs_domain::{NewMessage, StateMachineNodeRun};

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineHistoryIdentity {
    pub session_id: String,
    pub run_id: String,
    pub node_id: String,
    pub attempt: i32,
    pub event: String,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineHistoryPayload {
    pub schema_version: u32,
    pub message_id: String,
    pub message: NewMessage,

}

#[derive(Debug, Clone)]
pub struct StateMachineHistoryCheckpoint {
    pub operation_key: String,
    pub payload: StateMachineHistoryPayload,
    pub delivered_at_ms: Option<u64>,
}

#[derive(Debug, Clone)]
pub enum StateMachineHistoryMutation {
    ActivateHuman(MarkHumanNodeRunningCommand),
    AcceptOutput {
        judging: bool,
    },
    /// Freeze existing immutable evidence under an exact Node snapshot fence.
    Preserve(StateMachineNodeRun),
}

#[derive(Debug, Clone)]
pub struct AcceptStateMachineHistory {
    pub payload: StateMachineHistoryPayload,
    pub mutation: StateMachineHistoryMutation,
    pub event: Option<AppendEventRecord>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct StateMachineHistoryCursor {
    pub run_id: String,
    pub operation_key: String,
}

#[derive(Debug, Clone)]
pub struct StateMachineHistoryPage {
    pub checkpoints: Vec<StateMachineHistoryCheckpoint>,
    /// Invalid payloads stay pending, but cannot block later checkpoint keys.
    pub failures: Vec<StateMachineHistoryCursor>,
    pub next: Option<StateMachineHistoryCursor>,
    pub pending_count: u64,
    pub oldest_pending_at_ms: Option<u64>,
}

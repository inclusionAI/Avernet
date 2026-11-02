//! Abnormal Run completion is a single business transaction, never a network send.
use serde::{Deserialize, Serialize};
use super::{AppendEventRecord, FailStateMachineStartup, message_delivery::AdmitMessageDeliveries};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineRunReason {
    NodeFailed, HumanTimeout, StartupFailed, PublicationFailed, UserAborted,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineRunResultV1 {
    pub schema_version: u32,
    pub run_id: String,
    pub workflow_name: String,
    pub status: crate::StateMachineRunStatus,
    pub failed_node_id: Option<String>,
    pub attempt: Option<i32>,
    pub reason_category: Option<StateMachineRunReason>,
    pub completed_node_ids: Vec<String>,
}

impl StateMachineRunResultV1 {
    pub fn message_id(&self) -> String { format!("run-result:{}", self.run_id) }
    pub fn text(&self) -> crate::ServiceResult<String> {
        Ok(format!("[State Machine Run Result]\n{}", serde_json::to_string(self)?))
    }
}

#[derive(Debug, Clone)]
pub struct CommitStateMachineTerminal {
    pub run_id: String,
    pub result: StateMachineRunResultV1,
    /// Internal diagnostic only; never copied into result/event data.
    pub error: Option<String>,
    pub completed_at_ms: u64,
    pub admission: Option<AdmitMessageDeliveries>,
    pub event: Option<AppendEventRecord>,
    /// Preserve the original missing-fact CAS and preparation grace.
    pub startup: Option<FailStateMachineStartup>,
}

use std::fmt;

use serde::{Deserialize, Serialize};

use crate::ServiceResult;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineRunTerminalStatus {
    Failed,
    Aborted,
}

impl StateMachineRunTerminalStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Failed => "failed",
            Self::Aborted => "aborted",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineTerminalReason {
    NodeFailed,
    HumanTimeout,
    StartupFailed,
    PublicationFailed,
    UserAborted,
    SessionClosed,
    GroupClosed,
}

impl StateMachineTerminalReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::NodeFailed => "node_failed",
            Self::HumanTimeout => "human_timeout",
            Self::StartupFailed => "startup_failed",
            Self::PublicationFailed => "publication_failed",
            Self::UserAborted => "user_aborted",
            Self::SessionClosed => "session_closed",
            Self::GroupClosed => "group_closed",
        }
    }
}

impl fmt::Display for StateMachineTerminalReason {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineRunResultPayload {
    pub run_id: String,
    pub group_id: String,
    pub session_id: String,
    pub workflow_name: String,
    pub status: StateMachineRunTerminalStatus,
    pub failed_node_id: Option<String>,
    pub attempt: Option<i32>,
    pub reason: StateMachineTerminalReason,
    pub completed_node_ids: Vec<String>,
    pub completed_nodes_truncated: bool,
    pub target_bot_id: String,
    pub created_at_ms: u64,
}

impl StateMachineRunResultPayload {
    const MAX_COMPLETED_NODE_IDS: usize = 128;

    pub fn failed(
        run_id: String,
        group_id: String,
        session_id: String,
        workflow_name: String,
        target_bot_id: String,
        failed_node_id: Option<String>,
        attempt: Option<i32>,
        reason: StateMachineTerminalReason,
        completed_node_ids: Vec<String>,
        created_at_ms: u64,
    ) -> Self {
        let (completed_node_ids, completed_nodes_truncated) = Self::bounded_nodes(completed_node_ids);
        Self {
            run_id,
            group_id,
            session_id,
            workflow_name,
            status: StateMachineRunTerminalStatus::Failed,
            failed_node_id,
            attempt: attempt.map(|value| value.saturating_add(1)),
            reason,
            completed_node_ids,
            completed_nodes_truncated,
            target_bot_id,
            created_at_ms,
        }
    }

    pub fn aborted(
        run_id: String,
        group_id: String,
        session_id: String,
        workflow_name: String,
        target_bot_id: String,
        reason: StateMachineTerminalReason,
        completed_node_ids: Vec<String>,
        created_at_ms: u64,
    ) -> Self {
        let (completed_node_ids, completed_nodes_truncated) = Self::bounded_nodes(completed_node_ids);
        Self {
            run_id,
            group_id,
            session_id,
            workflow_name,
            status: StateMachineRunTerminalStatus::Aborted,
            failed_node_id: None,
            attempt: None,
            reason,
            completed_node_ids,
            completed_nodes_truncated,
            target_bot_id,
            created_at_ms,
        }
    }

    pub fn render_manager_message(&self) -> String {
        let status = match self.status {
            StateMachineRunTerminalStatus::Failed => "Failed",
            StateMachineRunTerminalStatus::Aborted => "Aborted",
        };
        let failed_node = self.failed_node_id.as_deref().unwrap_or("none");
        let attempt = self.attempt.map_or_else(|| "none".to_string(), |value| value.to_string());
        let completed = if self.completed_node_ids.is_empty() {
            "none".to_string()
        } else {
            self.completed_node_ids.join(",")
        };
        let truncated = if self.completed_nodes_truncated { "\ncompleted_nodes_truncated: true" } else { "" };
        format!(
            "[State Machine Run {status}]\nrun_id: {}\nworkflow: {}\nstatus: {}\nfailed_node: {failed_node}\nattempt: {attempt}\nreason: {}\ncompleted_nodes: {completed}{truncated}",
            self.run_id,
            self.workflow_name,
            self.status.as_str(),
            self.reason,
        )
    }

    fn bounded_nodes(mut node_ids: Vec<String>) -> (Vec<String>, bool) {
        node_ids.sort();
        node_ids.dedup();
        let truncated = node_ids.len() > Self::MAX_COMPLETED_NODE_IDS;
        node_ids.truncate(Self::MAX_COMPLETED_NODE_IDS);
        (node_ids, truncated)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineResultPublishCommand {
    pub run_id: String,
    pub group_id: String,
    pub session_id: String,
    pub sender_bot_id: String,
    pub content: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub terminal_result: Option<StateMachineRunResultPayload>,
    pub created_at_ms: u64,
}

/// Publishes a completed one-shot state-machine result back into its chat
/// session without coupling the collaboration runtime to routing or delivery.
/// Success acknowledges durable Group queue acceptance for managed recipients
/// and synchronous delivery acceptance for legacy recipients, not Bot completion.
/// An error may follow a partial publication. Conflicting history, empty queue
/// admissions and rejected recipients must not be acknowledged as success.
/// Stable message identity deduplicates history, but does not promise idempotent
/// Bot routing: callers must not blindly replay an ambiguous invocation.
#[async_trait::async_trait]
pub trait StateMachineResultPublisherPort: Send + Sync {
    async fn publish_state_machine_result(
        &self,
        cmd: StateMachineResultPublishCommand,
    ) -> ServiceResult<()>;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terminal_failure_message_is_structured_and_does_not_include_raw_error() {
        let result = StateMachineRunResultPayload::failed(
            "run-1".into(),
            "group-1".into(),
            "session-1".into(),
            "谁是卧底 · 第 1 轮发言".into(),
            "manager-1".into(),
            Some("speak_2".into()),
            Some(0),
            StateMachineTerminalReason::NodeFailed,
            vec!["speak_1".into()],
            123,
        );

        let content = result.render_manager_message();
        assert!(content.contains("[State Machine Run Failed]"));
        assert!(content.contains("[State Machine Run Failed]\nrun_id: run-1"));
        assert!(!content.contains("`n"));
        assert!(content.contains("failed_node: speak_2"));
        assert!(content.contains("reason: node_failed"));
        assert!(content.contains("completed_nodes: speak_1"));
        assert!(!content.contains("internal raw error"));
        assert!(!content.contains("manager-1"));
    }

    #[test]
    fn aborted_result_never_renders_a_retry_hint() {
        let result = StateMachineRunResultPayload::aborted(
            "run-2".into(),
            "group-1".into(),
            "session-1".into(),
            "workflow".into(),
            "manager-1".into(),
            StateMachineTerminalReason::UserAborted,
            vec!["speak_1".into()],
            456,
        );

        let content = result.render_manager_message();
        assert!(content.contains("[State Machine Run Aborted]"));
        assert!(content.contains("reason: user_aborted"));
        assert!(!content.contains("--retry"));
    }
}

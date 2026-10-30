//! Manager RunResult for failed or aborted Runs. The intent is written by the
//! same local transaction as the terminal Run CAS (`TerminateRun`); delivery is
//! fenced by a lease and never resent once a send may have started.
use serde::{Deserialize, Serialize};

/// Header that marks a RunResult for the receiving manager.
pub const STATE_MACHINE_RUN_RESULT_HEADER: &str = "[State Machine Run Result]";
const MAX_LISTED_NODES: usize = 32;
const MAX_TEXT_BYTES: usize = 1024;

/// Why a Run reached a terminal failure. Set by the caller that decides the
/// failure; never derived from error text.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineRunTerminalReason {
    NodeFailed,
    HumanTimeout,
    StartupFailed,
    PublicationFailed,
    UserAborted,
    SessionClosed,
    GroupClosed,
}

impl StateMachineRunTerminalReason {
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

    /// Session end and Group closure remove the recipient context, so they
    /// produce only the public event and never a RunResult.
    pub fn notifies_manager(self) -> bool {
        !matches!(self, Self::SessionClosed | Self::GroupClosed)
    }
}

/// Terminal status carried by a RunResult.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineRunResultTerminal {
    Failed,
    Aborted,
}

impl StateMachineRunResultTerminal {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Failed => "failed",
            Self::Aborted => "aborted",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineRunFailedNode {
    pub node_id: String,
    /// One-based attempt that failed.
    pub attempt: i32,
    pub max_attempts: i32,
}

/// Immutable RunResult facts. Contains no Node artifact, raw error text or
/// caller-supplied abort reason.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineRunResultPayload {
    pub run_id: String,
    pub group_id: String,
    pub session_id: String,
    /// The submitting manager, `run.created_by`.
    pub target_bot_id: String,
    pub workflow: String,
    pub terminal: StateMachineRunResultTerminal,
    pub reason: StateMachineRunTerminalReason,
    pub failed_node: Option<StateMachineRunFailedNode>,
    pub completed_nodes: Vec<String>,
    pub terminated_at_ms: u64,
    /// After this instant a pending intent expires instead of waking the manager.
    pub deadline_ms: u64,
}

impl StateMachineRunResultPayload {
    pub fn validate(&self) -> crate::ServiceResult<()> {
        let invalid = |message: &str| crate::ServiceError::InternalError(format!("invalid RunResult payload: {message}"));
        if self.run_id.is_empty() || self.run_id.len() > 128 || self.group_id.is_empty() || self.session_id.is_empty()
            || self.target_bot_id.trim().is_empty() {
            return Err(invalid("missing identity"));
        }
        if self.deadline_ms <= self.terminated_at_ms || self.deadline_ms > i64::MAX as u64 {
            return Err(invalid("deadline"));
        }
        if !self.reason.notifies_manager() {
            return Err(invalid("reason does not notify the manager"));
        }
        let aborted = self.terminal == StateMachineRunResultTerminal::Aborted;
        if aborted != (self.reason == StateMachineRunTerminalReason::UserAborted) || (aborted && self.failed_node.is_some()) {
            return Err(invalid("terminal status and reason disagree"));
        }
        Ok(())
    }

    /// Bot-facing text: a fixed header plus short fields, bounded in size.
    pub fn render_text(&self) -> String {
        let mut lines = vec![
            STATE_MACHINE_RUN_RESULT_HEADER.to_string(),
            format!("run_id: {}", self.run_id),
            format!("workflow: {}", one_line(&self.workflow)),
            format!("status: {}", self.terminal.as_str()),
            format!("reason: {}", self.reason.as_str()),
            format!("failed_node: {}", self.failed_node.as_ref().map_or(String::new(), |node| one_line(&node.node_id))),
            format!("attempt: {}", self.failed_node.as_ref().map_or(String::new(),
                |node| format!("{}/{}", node.attempt, node.max_attempts))),
        ];
        let listed: Vec<String> = self.completed_nodes.iter().take(MAX_LISTED_NODES).map(|id| one_line(id)).collect();
        let mut completed = listed.join(", ");
        if self.completed_nodes.len() > MAX_LISTED_NODES {
            completed.push_str(&format!(", … (+{})", self.completed_nodes.len() - MAX_LISTED_NODES));
        }
        lines.push(format!("completed_nodes: {completed}"));
        let mut text = lines.join("\n");
        if text.len() > MAX_TEXT_BYTES {
            let mut cut = MAX_TEXT_BYTES - "…".len();
            while !text.is_char_boundary(cut) {
                cut -= 1;
            }
            text.truncate(cut);
            text.push('…');
        }
        text
    }
}

fn one_line(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineRunResultStatus {
    Pending,
    Delivering,
    Delivered,
    Skipped,
    Failed,
    Expired,
}

impl StateMachineRunResultStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Delivering => "delivering",
            Self::Delivered => "delivered",
            Self::Skipped => "skipped",
            Self::Failed => "failed",
            Self::Expired => "expired",
        }
    }
}

#[derive(Debug, Clone)]
pub struct StateMachineRunResultCheckpoint {
    pub payload: StateMachineRunResultPayload,
    pub status: StateMachineRunResultStatus,
    pub lease_owner: Option<String>,
    pub lease_token: i64,
    pub lease_until_ms: Option<u64>,
    pub error: Option<String>,
    pub delivered_at_ms: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct StateMachineRunResultClaim {
    pub payload: StateMachineRunResultPayload,
    pub owner: String,
    pub token: i64,
    pub lease_until_ms: u64,
}

/// Publisher outcome recorded on the checkpoint.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StateMachineRunResultOutcome {
    /// Durably admitted for a managed target, or synchronously accepted by a legacy one.
    Delivered,
    /// A boundary check failed before any send: Session not running, Group
    /// closed, or the manager is no longer a Session Bot.
    Skipped { reason: String },
    /// Rejected before any send; safe to retry until the deadline.
    Retry { error: String },
    /// The send may have happened. Never resent.
    Uncertain { error: String },
}

#[cfg(test)]
mod tests {
    use super::*;

    fn payload() -> StateMachineRunResultPayload {
        StateMachineRunResultPayload {
            run_id: "sm-1".into(), group_id: "g".into(), session_id: "g:s".into(), target_bot_id: "manager".into(),
            workflow: "谁是卧底 第1轮\n发言".into(), terminal: StateMachineRunResultTerminal::Failed,
            reason: StateMachineRunTerminalReason::NodeFailed,
            failed_node: Some(StateMachineRunFailedNode { node_id: "speak_3".into(), attempt: 1, max_attempts: 1 }),
            completed_nodes: vec!["speak_1".into(), "speak_2".into()], terminated_at_ms: 10, deadline_ms: 20,
        }
    }

    #[test]
    fn renders_fixed_header_and_short_fields() {
        assert_eq!(payload().render_text(), "[State Machine Run Result]\nrun_id: sm-1\nworkflow: 谁是卧底 第1轮 发言\n\
            status: failed\nreason: node_failed\nfailed_node: speak_3\nattempt: 1/1\ncompleted_nodes: speak_1, speak_2");
    }

    #[test]
    fn bounds_listed_nodes_and_text_size() {
        let mut p = payload();
        p.completed_nodes = (0..40).map(|i| format!("node_{i}_{}", "x".repeat(40))).collect();
        let text = p.render_text();
        assert!(text.len() <= MAX_TEXT_BYTES);
        assert!(text.ends_with('…'));
        p.completed_nodes = (0..40).map(|i| format!("n{i}")).collect();
        assert!(p.render_text().contains(", … (+8)"));
    }

    #[test]
    fn aborted_results_carry_only_user_abort_without_node() {
        let mut p = payload();
        assert!(p.validate().is_ok());
        p.terminal = StateMachineRunResultTerminal::Aborted;
        assert!(p.validate().is_err());
        p.reason = StateMachineRunTerminalReason::UserAborted;
        p.failed_node = None;
        assert!(p.validate().is_ok());
        p.reason = StateMachineRunTerminalReason::SessionClosed;
        assert!(p.validate().is_err());
    }
}

//! Structured, sanitized terminal results for StateMachine manager wake-ups.
use super::*;
use bcs_service_api::{StateMachineRunResultPayload, StateMachineTerminalReason};

pub(super) struct RunFailure {
    pub(super) reason: StateMachineTerminalReason,
    pub(super) node: Option<(String, i32)>,
    pub(super) error: String,
}

impl RunFailure {
    pub(super) fn with_reason(reason: StateMachineTerminalReason, node_id: &str, attempt: i32, error: String) -> Self {
        Self { reason, node: Some((node_id.to_string(), attempt)), error }
    }

    pub(super) fn node(node_id: &str, attempt: i32, error: String) -> Self {
        Self::with_reason(StateMachineTerminalReason::NodeFailed, node_id, attempt, error)
    }

    pub(super) fn human_timeout(node_id: &str, attempt: i32, error: String) -> Self {
        Self::with_reason(StateMachineTerminalReason::HumanTimeout, node_id, attempt, error)
    }

    pub(super) fn startup(error: String) -> Self {
        Self { reason: StateMachineTerminalReason::StartupFailed, node: None, error }
    }

    pub(super) fn publication(error: String) -> Self {
        Self { reason: StateMachineTerminalReason::PublicationFailed, node: None, error }
    }
}

pub(super) fn classify_abort_reason(reason: Option<&str>) -> StateMachineTerminalReason {
    match reason {
        Some("group_closed" | "group_deleted" | "group_not_found") => StateMachineTerminalReason::GroupClosed,
        Some("session_closed" | "session_not_found") => StateMachineTerminalReason::SessionClosed,
        _ => StateMachineTerminalReason::UserAborted,
    }
}

pub(super) fn failure_event_reason(
    failure_reason: StateMachineTerminalReason,
    terminal_result: Option<&StateMachineRunResultPayload>,
) -> StateMachineTerminalReason {
    terminal_result.map_or(failure_reason, |result| result.reason)
}

impl CollaborationRuntime {
    pub(super) async fn build_failed_result(
        &self,
        run: &StateMachineRun,
        workflow_name: String,
        failure: &RunFailure,
        created_at_ms: u64,
    ) -> Result<Option<StateMachineRunResultPayload>, CollaborationRuntimeError> {
        let Some(target_bot_id) = run.created_by.clone() else { return Ok(None); };
        let nodes = self.runs.list_node_runs(&run.run_id).await?;
        let completed_node_ids = nodes.iter()
            .filter(|node| node.status == StateMachineNodeStatus::Completed)
            .map(|node| node.node_id.clone())
            .collect();
        let reason = failure.reason;
        Ok(Some(StateMachineRunResultPayload::failed(
            run.run_id.clone(), run.group_id.clone(), run.session_id.clone(), workflow_name,
            target_bot_id, failure.node.as_ref().map(|(node_id, _)| node_id.clone()),
            failure.node.as_ref().map(|(_, attempt)| *attempt), reason,
            completed_node_ids, created_at_ms,
        )))
    }

    pub(super) async fn build_aborted_result(
        &self,
        run: &StateMachineRun,
        workflow_name: String,
        reason: StateMachineTerminalReason,
        created_at_ms: u64,
    ) -> Result<Option<StateMachineRunResultPayload>, CollaborationRuntimeError> {
        let Some(target_bot_id) = run.created_by.clone() else { return Ok(None); };
        let completed_node_ids = self.runs.list_node_runs(&run.run_id).await?
            .into_iter()
            .filter(|node| node.status == StateMachineNodeStatus::Completed)
            .map(|node| node.node_id)
            .collect();
        Ok(Some(StateMachineRunResultPayload::aborted(
            run.run_id.clone(), run.group_id.clone(), run.session_id.clone(), workflow_name,
            target_bot_id, reason, completed_node_ids, created_at_ms,
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classifies_system_abort_reasons_without_treating_them_as_user_cancellation() {
        assert_eq!(classify_abort_reason(Some("group_closed")), StateMachineTerminalReason::GroupClosed);
        assert_eq!(classify_abort_reason(Some("group_deleted")), StateMachineTerminalReason::GroupClosed);
        assert_eq!(classify_abort_reason(Some("session_not_found")), StateMachineTerminalReason::SessionClosed);
        assert_eq!(classify_abort_reason(Some("user cancellation")), StateMachineTerminalReason::UserAborted);
        assert_eq!(classify_abort_reason(None), StateMachineTerminalReason::UserAborted);
    }

    #[test]
    fn human_timeout_failure_keeps_a_typed_reason() {
        let failure = RunFailure::human_timeout("review", 0, "timed out".into());
        assert_eq!(failure.reason, StateMachineTerminalReason::HumanTimeout);
        assert_eq!(failure.node.as_ref().map(|(node_id, attempt)| (node_id.as_str(), *attempt)), Some(("review", 0)));
    }

    #[test]
    fn public_failure_event_uses_the_sanitized_terminal_reason() {
        let terminal_result = StateMachineRunResultPayload::failed(
            "run".into(), "group".into(), "session".into(), "workflow".into(),
            "bot".into(), Some("review".into()), Some(0),
            StateMachineTerminalReason::HumanTimeout, Vec::new(), 100,
        );
        assert_eq!(
            failure_event_reason(StateMachineTerminalReason::NodeFailed, Some(&terminal_result)),
            StateMachineTerminalReason::HumanTimeout,
        );
    }
}

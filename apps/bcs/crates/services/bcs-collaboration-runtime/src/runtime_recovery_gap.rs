use super::*;
use bcs_service_api::{FailStateMachineStartup, StateMachineMissingStartupFact as Missing};

impl CollaborationRuntime {
    pub(super) async fn converge_missing_startup(&self, run: &StateMachineRun, missing: Missing,
        preparation_error: Option<String>) -> Result<bool, CollaborationRuntimeError> {
        if !self.runs.fail_missing_startup(FailStateMachineStartup { run_id: run.run_id.clone(), missing,
            failed_at_ms: bcs_protocol::now_ms(), preparation_error }).await? { return Ok(false); }
        self.runs.supersede_inactive_node_dispatches(&run.run_id).await?;
        // Run and its typed failure fact are committed. Session completion is
        // independent, recoverable by the existing Running Session page.
        if let Some(session) = self.sessions.get(&run.session_id).await
            .map_err(|e| CollaborationRuntimeError::Internal(ServiceError::InternalError(e.to_string())))? {
            self.recover_terminal_run_session(&session).await?;
        }
        warn!(run_id = %run.run_id, session_id = %run.session_id, missing = ?missing,
            "state_machine: missing original startup facts converged to Failed");
        Ok(true)
    }

    pub(super) async fn complete_missing_snapshot_session(&self, run: &StateMachineRun) -> Result<bool, CollaborationRuntimeError> {
        let saved = self.runs.get_startup_failure(&run.run_id).await?.ok_or_else(||
            CollaborationRuntimeError::InvalidRequest("Session recovery requires an immutable Run snapshot or committed startup failure".into()))?;
        if run.status != StateMachineRunStatus::Failed || saved.missing != Missing::Snapshot
            || saved.run_id != run.run_id || saved.group_id != run.group_id || saved.session_id != run.session_id
            || saved.session_activation_count != run.session_activation_count || saved.run_created_at_ms != run.created_at
            || Some(saved.failed_at_ms) != run.completed_at || run.error.as_ref() != Some(&saved.error) {
            return Err(CollaborationRuntimeError::InvalidRequest("startup failure does not match terminal Run".into()));
        }
        let Some(completed) = self.complete_service_session_for_run(run, None, Some(saved.error)).await
            .map_err(|e| CollaborationRuntimeError::Internal(ServiceError::InternalError(e.to_string())))? else { return Ok(false); };
        bcs_callback::dispatch::maybe_dispatch_for_session_with_url_guard(
            completed, self.groups.clone(), self.sessions.clone(), self.callback_url_guard.clone(),
        );
        // No original workflow snapshot exists, so do not invent terminal IM
        // content or execute/recompile today's definition during recovery.
        Ok(true)
    }
}

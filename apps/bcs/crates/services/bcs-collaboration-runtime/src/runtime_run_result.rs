//! Abnormal terminal orchestration. Persistence owns the combined transaction.
use super::*;
use bcs_service_api::port::repo::{CommitStateMachineTerminal, StateMachineRunResultV1};

fn safe_label(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars().filter(|c| !c.is_control()) {
        if out.len() + c.len_utf8() > 256 { break; }
        out.push(c);
    }
    if out.is_empty() { "unnamed".into() } else { out }
}

impl CollaborationRuntime {
    pub(super) async fn commit_abnormal(&self, run: &StateMachineRun, status: StateMachineRunStatus,
        reason: Option<Reason>, node: Option<(&str, i32)>, error: Option<String>,
        startup: Option<bcs_service_api::FailStateMachineStartup>, internal_scope_invalid: bool) -> Result<bool, CollaborationRuntimeError> {
        for attempt in 0..3 {
        let now = startup.as_ref().map(|c| c.failed_at_ms).unwrap_or_else(bcs_protocol::now_ms);
        let session = self.sessions.get(&run.session_id).await
            .map_err(|e| CollaborationRuntimeError::Internal(ServiceError::InternalError(e.to_string())))?;
        let group = self.groups.get(&run.group_id).await;
        let scope_valid = !internal_scope_invalid && session.as_ref().is_some_and(|s| s.group_id == run.group_id && s.status == SessionStatus::Running)
            && group.as_ref().is_some_and(|g| g.status != GroupStatus::Closed);
        let snapshot = self.definitions.get_run_snapshot(&run.run_id).await?;
        let name = snapshot.map(|s| s.definition.name).unwrap_or_else(|| format!("{}@{}", run.definition_id, run.definition_version));
        let result = StateMachineRunResultV1 { schema_version: 1, run_id: run.run_id.clone(),
            workflow_name: safe_label(&name), status, failed_node_id: node.map(|n| n.0.into()), attempt: node.map(|n| n.1),
            reason_category: if scope_valid { reason } else { None }, completed_node_ids: self.runs.completed_node_ids(&run.run_id).await? };
        let admission = if scope_valid && session.as_ref().is_some_and(|s| s.session_kind == SessionKind::Chat)
            && let (Some(manager), Some(publisher)) = (run.created_by.as_ref(), self.result_publisher.as_ref()) {
            publisher.prepare_run_result(StateMachineResultPublishCommand { run_id: run.run_id.clone(), group_id: run.group_id.clone(),
                session_id: run.session_id.clone(), sender_bot_id: manager.clone(), content: result.text()?, created_at_ms: now }).await?
        } else { None };
        let event_type = if status == StateMachineRunStatus::Failed { "state_machine.run.failed" } else { "state_machine.run.aborted" };
        let event = self.prepare_public_event(event_type, run, "state_machine.run", &run.run_id, "terminal", None, None, now,
            BTreeMap::from([("result".into(), serde_json::to_value(&result).map_err(ServiceError::from)?),
                ("completed_at".into(), serde_json::json!(event_timestamp(now)?)),
                ("duration_ms".into(), serde_json::json!(now.saturating_sub(run.created_at))),
                ("scope_invalid".into(), serde_json::json!(!scope_valid))]))?;
        match self.runs.commit_terminal(CommitStateMachineTerminal { run_id: run.run_id.clone(), result, error: error.clone(),
            completed_at_ms: now, admission, event, startup: startup.clone() }).await {
            Err(ServiceError::Conflict(_)) if attempt < 2 => continue,
            result => return result.map_err(Into::into),
        }
        }
        Err(CollaborationRuntimeError::Conflict("Run terminal state changed repeatedly".into()))
    }

    pub(super) async fn fail_run(&self, run: &StateMachineRun, error: String) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        self.fail_run_reason(run, error, Reason::StartupFailed, None).await
    }
    pub(super) async fn fail_run_reason(
        &self,
        run: &StateMachineRun,
        error: String,
        reason: Reason,
        node: Option<(&str, i32)>,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let failed = self.commit_abnormal(run, StateMachineRunStatus::Failed, Some(reason), node,
            Some(error.clone()), None, false).await?;
        if !failed { return self.run_view(&run.run_id).await; }
        let is_chat_session = self.is_chat_session(&run.session_id).await?;
        if !is_chat_session && self.session_channel_outbound.is_some() {
            let snapshot = self.definitions.get_run_snapshot(&run.run_id).await?.ok_or_else(||
                CollaborationRuntimeError::InvalidRequest("terminal IM requires original Run snapshot".into()))?;
            self.prepare_terminal_im(run, snapshot.definition.name, StateMachineTerminalStatus::Failed, None).await?;
        }
        let (session_complete_result, _session_transitioned) =
            if is_chat_session {
                ("chat_preserved", false)
            } else {
                match self
                    .complete_service_session_for_run(run, None, Some(error.clone()))
                    .await
                {
                    Ok(Some(_)) => ("completed", true),
                    Ok(None) => ("not_running", false),
                    Err(error) => {
                        warn!(
                            run_id = %run.run_id,
                            group_id = %run.group_id,
                            session_id = %run.session_id,
                            error = %error,
                            "state_machine: failed to complete session for failed run"
                        );
                        return Err(CollaborationRuntimeError::Internal(ServiceError::InternalError(
                            format!("failed to complete Session for failed Run: {error}"),
                        )));
                    }
                }
            };
        if !is_chat_session && self.session_channel_outbound.is_some() { self.resume_terminal_im(&run.run_id).await?; }
        warn!(
            run_id = %run.run_id,
            group_id = %run.group_id,
            session_id = %run.session_id,
            definition_id = %run.definition_id,
            definition_version = run.definition_version,
            error = %error,
            session_complete_result = %session_complete_result,
            "state_machine: run failed"
        );
        log_state_machine_run_failed(run, &error, session_complete_result);
        self.run_view(&run.run_id).await
    }

    pub(super) async fn cancel_run_internal(
        &self,
        cmd: CancelStateMachineRunCommand,
        scope_invalid: bool,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        let reason = cmd.reason.clone();
        let aborted = self.commit_abnormal(&run, StateMachineRunStatus::Aborted,
            Some(Reason::UserAborted), None, cmd.reason, None, scope_invalid).await?;
        if !aborted {
            return self.run_view(&cmd.run_id).await?
                .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id));
        }
        let is_chat_session = self.is_chat_session(&run.session_id).await?;
        let (completed_session, session_missing) = if is_chat_session {
            (None, false)
        } else {
            match self
                .complete_service_session_for_run(&run, None, Some("aborted".to_string()))
                .await
            {
                Ok(completed_session) => (completed_session, false),
                Err(SessionUseCaseError::NotFound(_)) => (None, true),
                Err(error) => {
                    return Err(CollaborationRuntimeError::InvalidRequest(error.to_string()));
                }
            }
        };
        let session_complete_result = if is_chat_session {
            "chat_preserved"
        } else if session_missing {
            "missing"
        } else if completed_session.is_some() {
            "completed"
        } else {
            "not_running"
        };
        info!(
            run_id = %cmd.run_id,
            group_id = %run.group_id,
            session_id = %run.session_id,
            definition_id = %run.definition_id,
            definition_version = run.definition_version,
            reason = ?reason,
            session_complete_result = %session_complete_result,
            "state_machine: run aborted"
        );
        self.run_view(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id))
    }

}

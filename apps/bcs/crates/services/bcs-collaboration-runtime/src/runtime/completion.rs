use super::*;
fn final_output_text(
    compiled: &CompiledStateMachine,
    nodes: &[StateMachineNodeRun],
) -> Option<String> {
    let state_machine = match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => return None,
    };
    let final_node_id = state_machine.nodes.iter().find_map(|(node_id, node)| {
        if node.final_output {
            Some(node_id)
        } else {
            None
        }
    })?;
    nodes
        .iter()
        .find(|node| &node.node_id == final_node_id)
        .and_then(|node| node.artifact_text.clone())
}impl CollaborationRuntime {

    pub(super) async fn complete_run_if_done(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let nodes = self.runs.list_node_runs(&run.run_id).await?;
        if nodes.iter().any(|node| {
            !matches!(
                node.status,
                StateMachineNodeStatus::Completed | StateMachineNodeStatus::Skipped
            )
        }) {
            return Ok(self.run_view(&run.run_id).await?);
        }
        if !self.progression_run_is_active(&run.run_id).await? {
            return self.run_view(&run.run_id).await;
        }
        let output = final_output_text(compiled, &nodes).or_else(|| {
            nodes
                .iter()
                .rev()
                .find_map(|node| node.artifact_text.clone())
        });
        let output_len = output.as_ref().map_or(0, String::len);
        let now = bcs_protocol::now_ms();
        let session = self
            .sessions
            .get(&run.session_id)
            .await
            .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?;
        let is_chat_session = session
            .as_ref()
            .is_some_and(|session| session.session_kind == SessionKind::Chat);
        if let Some(session) = session.as_ref().filter(|_| is_chat_session) {
            let output_at = nodes.iter().filter_map(|node| node.completed_at).max().unwrap_or(run.created_at);
            if !self.ensure_chat_result(run, session, output.as_deref(), output_at).await? {
                return self.run_view(&run.run_id).await;
            }
        }
        let event = self.prepare_public_event(
            "state_machine.run.completed",
            run,
            "state_machine.run",
            &run.run_id,
            "completed",
            None,
            None,
            now,
            BTreeMap::from([
                (
                    "completed_at".to_string(),
                    serde_json::json!(event_timestamp(now)?),
                ),
                (
                    "output".to_string(),
                    content_value(output.clone().map_or(Value::Null, Value::String))?,
                ),
                (
                    "duration_ms".to_string(),
                    serde_json::json!(now.saturating_sub(run.created_at)),
                ),
            ]),
        )?;
        let completed = match event {
            Some(event) => {
                self.runs
                    .commit_eventful_transition(StateMachineEventfulTransition::CompleteRun {
                        run_id: run.run_id.clone(),
                        output: output.clone(),
                        completed_at_ms: now,
                        event,
                    })
                    .await?
            }
            None => {
                self.runs
                    .update_run_status(
                        &run.run_id,
                        StateMachineRunStatus::Completed,
                        output.clone(),
                        None,
                        now,
                        Some(now),
                    )
                    .await?
            }
        };
        self.runs.supersede_inactive_node_dispatches(&run.run_id).await?;
        if !completed {
            return Ok(self.run_view(&run.run_id).await?);
        }
        self.release_instance_association(&run.run_id).await;
        if !is_chat_session {
            self.prepare_terminal_im(run, compiled.definition.name.clone(), StateMachineTerminalStatus::Completed, output.clone()).await?;
        }
        let (session_complete_result, _session_transitioned) = if is_chat_session {
            ("chat_preserved", false)
        } else {
            match self
                .complete_service_session_for_run(run, output.clone().map(Value::String), None)
                .await
            {
                Ok(Some(_)) => ("completed", true),
                Ok(None) => ("not_running", false),
                Err(error) => {
                    warn!(target: RUNTIME_LOG_TARGET,
                        run_id = %run.run_id,
                        group_id = %run.group_id,
                        session_id = %run.session_id,
                        error = %error,
                        "state_machine: failed to complete session for completed run"
                    );
                    return Err(CollaborationRuntimeError::Internal(ServiceError::InternalError(
                        format!("failed to complete Session for completed Run: {error}"),
                    )));
                }
            }
        };
        if !is_chat_session && self.session_channel_outbound.is_some() { self.resume_terminal_im(&run.run_id).await?; }
        info!(target: RUNTIME_LOG_TARGET,
            run_id = %run.run_id,
            group_id = %run.group_id,
            session_id = %run.session_id,
            definition_id = %run.definition_id,
            definition_version = run.definition_version,
            node_count = nodes.len(),
            output_len = output_len,
            session_complete_result = %session_complete_result,
            "state_machine: run completed"
        );
        log_state_machine_run_complete(run, nodes.len(), output_len, session_complete_result);
        self.run_view(&run.run_id).await
    }

    pub(super) async fn is_chat_session(&self, session_id: &str) -> Result<bool, CollaborationRuntimeError> {
        self.sessions
            .get(session_id)
            .await
            .map(|session| session.is_some_and(|session| session.session_kind == SessionKind::Chat))
            .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))
    }

    pub(super) async fn fail_run(
        &self,
        run: &StateMachineRun,
        error: String,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let now = bcs_protocol::now_ms();
        let failed = self.runs
            .update_run_status(
                &run.run_id,
                StateMachineRunStatus::Failed,
                None,
                Some(error.clone()),
                now,
                Some(now),
            )
            .await?;
        self.runs.supersede_inactive_node_dispatches(&run.run_id).await?;
        if !failed { return self.run_view(&run.run_id).await; }
        self.release_instance_association(&run.run_id).await;
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
                        warn!(target: RUNTIME_LOG_TARGET,
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
        warn!(target: RUNTIME_LOG_TARGET,
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

    pub(super) async fn complete_service_session_for_run(
        &self,
        run: &StateMachineRun,
        output: Option<Value>,
        error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        let activation = run.session_activation_count.ok_or_else(|| {
            SessionUseCaseError::InvalidParams("Run has no saved Session activation".into())
        })?;
        self.sessions.complete_running_service_activation(
            &run.session_id, activation, output, error,
        ).await
    }

    pub(super) async fn recover_terminal_run_session(&self, session: &Session) -> Result<bool, CollaborationRuntimeError> {
        if session.session_kind != SessionKind::ServiceInvocation || session.status != SessionStatus::Running {
            return Ok(false);
        }
        let Some(run) = self.runs.get_run_by_session_id(&session.id).await? else { return Ok(false); };
        if !matches!(run.status, StateMachineRunStatus::Completed | StateMachineRunStatus::Failed | StateMachineRunStatus::Aborted)
            || run.session_activation_count != Some(session.activation_count)
        {
            return Ok(false);
        }
        if run.session_id != session.id || run.group_id != session.group_id || run.completed_at.is_none() {
            return Err(CollaborationRuntimeError::InvalidRequest("terminal Run has invalid Session identity or completion facts".into()));
        }
        let Some(snapshot) = self.definitions.get_run_snapshot(&run.run_id).await? else {
            return self.complete_missing_snapshot_session(&run).await;
        };
        let loaded = crate::snapshot::load_state_machine_snapshot(snapshot)?;
        if loaded.plan.is_some() && !self.loop_execution_enabled {
            return Err(CollaborationRuntimeError::InvalidRequest("v2 Session recovery is disabled".into()));
        }
        let (output, error, notification_status) = match run.status {
            StateMachineRunStatus::Completed => (run.output.clone().map(Value::String), None, Some(StateMachineTerminalStatus::Completed)),
            StateMachineRunStatus::Failed => {
                let error = run.error.clone().ok_or_else(|| CollaborationRuntimeError::InvalidRequest("failed Run has no saved error".into()))?;
                (None, Some(error), Some(StateMachineTerminalStatus::Failed))
            }
            StateMachineRunStatus::Aborted => (None, Some("aborted".into()), None),
            _ => return Ok(false),
        };
        if let Some(status) = notification_status {
            self.prepare_terminal_im(&run, loaded.execution.definition.name.clone(), status, run.output.clone()).await?;
        }
        let Some(_) = self.complete_service_session_for_run(&run, output, error).await
            .map_err(|error| CollaborationRuntimeError::Internal(ServiceError::InternalError(error.to_string())))?
        else { return Ok(false); };
        if notification_status.is_some() && self.session_channel_outbound.is_some() { self.resume_terminal_im(&run.run_id).await?; }
        Ok(true)
    }

    pub(super) async fn cancel_state_machine_run_for_human_impl(
        &self,
        cmd: HumanRunAccessCommand,
        reason: Option<String>,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        if run.created_by.as_deref() != Some(cmd.caller_actor_id.as_str()) {
            return Err(CollaborationRuntimeError::Forbidden(
                "only the Human who started the run can cancel it".to_string(),
            ));
        }
        self.cancel_state_machine_run(CancelStateMachineRunCommand {
            run_id: cmd.run_id,
            reason,
        })
        .await
    }

    pub(super) async fn cancel_state_machine_run_with_access_impl(
        &self,
        cmd: StateMachineRunAccessCommand,
        reason: Option<String>,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        let human_access_required = self
            .authorize_run_access(&run, cmd.authenticated_human.as_ref())
            .await?;
        if human_access_required
            && let Some(human) = cmd.authenticated_human.as_ref()
            && run.created_by.as_deref() != Some(human.actor_id.as_str())
        {
            return Err(CollaborationRuntimeError::Forbidden(
                "only the Human who started the run can cancel it".to_string(),
            ));
        }
        self.cancel_state_machine_run(CancelStateMachineRunCommand {
            run_id: cmd.run_id,
            reason,
        })
        .await
    }

    pub(super) async fn cancel_state_machine_run_impl(
        &self,
        cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        let now = bcs_protocol::now_ms();
        let reason = cmd.reason.clone();
        let aborted = self.runs
            .update_run_status(
                &cmd.run_id,
                StateMachineRunStatus::Aborted,
                None,
                cmd.reason,
                now,
                Some(now),
            )
            .await?;
        self.runs.supersede_inactive_node_dispatches(&run.run_id).await?;
        if !aborted {
            return self.run_view(&cmd.run_id).await?
                .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id));
        }
        self.release_instance_association(&cmd.run_id).await;
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
        info!(target: RUNTIME_LOG_TARGET,
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

    pub(super) async fn cancel_session_runs_impl(
        &self,
        session_id: &str,
        reason: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        let mut first_error = None;
        for run in self.runs.list_runs_by_session_id(session_id).await? {
            if !matches!(
                run.status,
                StateMachineRunStatus::Pending | StateMachineRunStatus::Running
            ) {
                continue;
            }
            if let Err(error) = self
                .cancel_state_machine_run(CancelStateMachineRunCommand {
                    run_id: run.run_id,
                    reason: Some(reason.to_string()),
                })
                .await
            {
                if first_error.is_none() {
                    first_error = Some(error);
                }
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    pub(super) async fn cancel_group_runs_impl(
        &self,
        group_id: &str,
        reason: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        let sessions = self
            .sessions
            .list_by_group(group_id, None, 0, RUNTIME_CLEANUP_SESSION_LIMIT, None, None)
            .await
            .map_err(|error| {
                CollaborationRuntimeError::Internal(ServiceError::InternalError(error.to_string()))
            })?;
        let mut first_error = None;
        for session in sessions {
            if let Err(error) = self.cancel_session_runs(&session.id, reason).await {
                if first_error.is_none() {
                    first_error = Some(error);
                }
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    pub(super) async fn delete_group_runtime_state_impl(
        &self,
        group_id: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        let sessions = self
            .sessions
            .list_by_group(group_id, None, 0, RUNTIME_CLEANUP_SESSION_LIMIT, None, None)
            .await
            .map_err(|error| {
                CollaborationRuntimeError::Internal(ServiceError::InternalError(error.to_string()))
            })?;
        for session in sessions {
            self.sessions.delete(&session.id).await.map_err(|error| {
                CollaborationRuntimeError::Internal(ServiceError::InternalError(error.to_string()))
            })?;
        }
        self.bindings.delete(group_id).await?;
        Ok(())
    }
}

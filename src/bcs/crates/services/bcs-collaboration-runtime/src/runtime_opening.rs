use super::*;
use bcs_service_api::StateMachineOpeningPayload;

impl CollaborationRuntime {
    pub(super) fn opening_from_payload(&self, run: &StateMachineRun, payload: &StateMachineOpeningPayload) -> Result<RenderedOpeningMessage, CollaborationRuntimeError> {
        if payload.run_id != run.run_id || payload.group_id != run.group_id || payload.session_id != run.session_id
            || payload.created_at_ms != run.created_at || payload.client_msg_id != format!("{}:000-panel", run.run_id) {
            return Err(CollaborationRuntimeError::InvalidRequest("opening checkpoint does not match Run identity".into()));
        }
        Ok(RenderedOpeningMessage { content: payload.content.clone(), component: payload.component.clone() })
    }

    pub(super) async fn save_opening_payload(&self, run: &StateMachineRun, message: &RenderedOpeningMessage) -> Result<(), CollaborationRuntimeError> {
        if !self.runs.save_run_opening(StateMachineOpeningPayload {
            run_id: run.run_id.clone(), group_id: run.group_id.clone(), session_id: run.session_id.clone(),
            client_msg_id: format!("{}:000-panel", run.run_id), content: message.content.clone(),
            component: message.component.clone(), created_at_ms: run.created_at,
        }).await? {
            return Err(CollaborationRuntimeError::Conflict("Run no longer accepts an opening checkpoint".into()));
        }
        Ok(())
    }

    pub(super) async fn persist_saved_opening(&self, run: &StateMachineRun) -> Result<(), CollaborationRuntimeError> {
        let saved = self.runs.get_run_opening(&run.run_id).await?.ok_or_else(||
            CollaborationRuntimeError::InvalidRequest("Run has no persisted opening checkpoint".into()))?;
        let rendered = self.opening_from_payload(run, &saved.payload)?;
        self.persist_state_machine_panel_message(run, &rendered).await?;
        if saved.delivered_at_ms.is_none() && !self.runs.mark_run_opening_delivered(&run.run_id, bcs_protocol::now_ms()).await? {
            return Err(CollaborationRuntimeError::Conflict("Run no longer accepts the opening history barrier".into()));
        }
        Ok(())
    }

    pub(super) async fn ensure_materialized_run_started(&self, run: &mut StateMachineRun) -> Result<(), CollaborationRuntimeError> {
        if run.status == StateMachineRunStatus::Pending {
            let started_at = bcs_protocol::now_ms();
            let run_mode = if run.session_activation_count.is_some() {
                "configured"
            } else {
                "one_shot"
            };
            let mut lineage_fields = BTreeMap::from([
                (
                    "definition_id".to_string(),
                    serde_json::json!(run.definition_id.clone()),
                ),
                (
                    "definition_version".to_string(),
                    serde_json::json!(run.definition_version),
                ),
                (
                    "root_run_id".to_string(),
                    serde_json::json!(run.root_run_id.clone()),
                ),
                (
                    "rerun_of".to_string(),
                    serde_json::json!(run.rerun_of.clone()),
                ),
                ("run_mode".to_string(), serde_json::json!(run_mode)),
                ("status".to_string(), serde_json::json!("running")),
            ]);
            if run.rerun_of.is_none() { lineage_fields.remove("rerun_of"); }
            if run.root_run_id.is_none() { lineage_fields.remove("root_run_id"); }
            if let Some(activation_count) = run.session_activation_count {
                lineage_fields.insert(
                    "session_activation_count".to_string(),
                    serde_json::json!(activation_count),
                );
            }
            let created_event = self.prepare_public_event(
                "state_machine.run.created",
                &run,
                "state_machine.run",
                &run.run_id,
                "created",
                run.created_by.as_deref(),
                None,
                started_at,
                lineage_fields,
            )?;
            let started_event = self.prepare_public_event(
                "state_machine.run.started",
                &run,
                "state_machine.run",
                &run.run_id,
                "started",
                run.created_by.as_deref(),
                None,
                started_at,
                BTreeMap::from([
                    ("run_mode".to_string(), serde_json::json!(run_mode)),
                    (
                        "started_at".to_string(),
                        serde_json::json!(event_timestamp(started_at)?),
                    ),
                    ("input".to_string(), content_value(run.input.clone())?),
                ]),
            )?;
            let started = match (created_event, started_event) {
                (Some(created_event), Some(started_event)) => {
                    self.runs
                        .commit_eventful_transition(StateMachineEventfulTransition::StartRun {
                            run_id: run.run_id.clone(),
                            started_at_ms: started_at,
                            events: vec![created_event, started_event],
                        })
                        .await?
                }
                (None, None) => {
                    self.runs
                        .update_run_status(
                            &run.run_id,
                            StateMachineRunStatus::Running,
                            None,
                            None,
                            started_at,
                            None,
                        )
                        .await?
                }
                _ => {
                    return Err(CollaborationRuntimeError::Internal(
                        ServiceError::InternalError(
                            "state-machine run Event preparation was inconsistent".to_string(),
                        ),
                    ));
                }
            };
            if started {
                run.status = StateMachineRunStatus::Running;
                run.updated_at = started_at;
            } else {
                *run =
                    self.runs.get_run(&run.run_id).await?.ok_or_else(|| {
                        CollaborationRuntimeError::RunNotFound(run.run_id.clone())
                    })?;
            }
        }

        Ok(())
    }
}

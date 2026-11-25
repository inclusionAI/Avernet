use super::*;
pub(super) fn default_state_machine_opening_message(
    run_id: &str,
    session_title: Option<&str>,
) -> RenderedOpeningMessage {
    let title_suffix = session_title
        .map(str::trim)
        .filter(|title| !title.is_empty())
        .unwrap_or("新会话");
    let title = format!("State Machine - {title_suffix}");
    let tab_json = single_quoted_json_attr(format!(
        "{{\"id\":{},\"title\":{},\"closable\":true}}",
        json_string(&format!("state-machine-run-{run_id}")),
        json_string(&title)
    ));
    let params_json = single_quoted_json_attr(format!("{{\"runId\":{}}}", json_string(run_id)));
    RenderedOpeningMessage {
        content: format!(
            "<AixUI\n  type=\"panel\"\n  component=\"bcsPanel.StateMachineRunView\"\n  tab='{tab_json}'\n  params='{params_json}'\n/>"
        ),
        component: Some("bcsPanel.StateMachineRunView".to_string()),
    }
}

pub(super) fn render_state_machine_opening_message(
    group: &Group,
    run: &StateMachineRun,
    session_title: Option<&str>,
) -> Result<RenderedOpeningMessage, CollaborationRuntimeError> {
    // Chat and Manager-Worker opening messages are Session-scoped UI content.
    // A one-shot StateMachine run inside either strategy must keep its own
    // default panel instead of interpreting that Session template as a Run
    // opening message.
    if group.group_strategy != GroupStrategy::StateMachine {
        if let Some(opening_message) = run.opening_message_override.as_ref() {
            return render_run_opening_message(opening_message, group, run, session_title);
        }
        return Ok(default_state_machine_opening_message(
            &run.run_id,
            session_title,
        ));
    }
    let Some(opening_message) = group.opening_message.as_ref() else {
        return Ok(default_state_machine_opening_message(
            &run.run_id,
            session_title,
        ));
    };
    render_run_opening_message(opening_message, group, run, session_title)
}

fn render_run_opening_message(
    opening_message: &OpeningMessage,
    group: &Group,
    run: &StateMachineRun,
    session_title: Option<&str>,
) -> Result<RenderedOpeningMessage, CollaborationRuntimeError> {
    opening_message
        .render(OpeningMessageRenderContext::StateMachineRun {
            group_id: &group.id,
            session_id: &run.session_id,
            run_id: &run.run_id,
            group_name: group.label.as_deref(),
            session_name: session_title,
        })
        .map_err(|error| {
            CollaborationRuntimeError::InvalidRequest(format!("invalid_opening_message: {error}"))
        })
}

fn json_string(value: &str) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "\"\"".to_string())
}

fn single_quoted_json_attr(value: String) -> String {
    value.replace('\'', "\\u0027")
}impl CollaborationRuntime {

    pub(super) async fn persisted_state_machine_opening_message(
        &self,
        run: &StateMachineRun,
    ) -> Result<Option<RenderedOpeningMessage>, CollaborationRuntimeError> {
        let Some(message_repo) = self.message_repo.as_ref() else {
            return Ok(None);
        };
        let page = message_repo
            .query_messages(MessageQuery {
                group_id: run.group_id.clone(),
                session_id: run.session_id.clone(),
                cursor: None,
                limit: 1_000,
                keyword: None,
                sender_id: Some(BCS_STATE_MACHINE_MESSAGE_SENDER.to_string()),
                message_type: Some(STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string()),
                owner_filter: MessageOwnerFilter::Any,
                time_range: Some((run.created_at, run.created_at)),
                visible_from_seq: None,
                human_view: None,
            })
            .await
            .map_err(|error| {
                CollaborationRuntimeError::Internal(ServiceError::InternalError(format!(
                    "state-machine opening message load failed: {error}"
                )))
            })?;
        let expected_client_msg_id = format!("{}:000-panel", run.run_id);
        Ok(page
            .messages
            .into_iter()
            .find(|message| {
                message.run_id == run.run_id
                    || message.client_msg_id.as_deref() == Some(expected_client_msg_id.as_str())
            })
            .map(|message| {
                let content = message
                    .content
                    .get("text")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                let component = message
                    .content
                    .pointer("/metadata/state_machine/component")
                    .and_then(Value::as_str)
                    .map(str::to_string);
                RenderedOpeningMessage { content, component }
            }))
    }

    pub(super) async fn opening_message_for_existing_run(
        &self,
        run: &StateMachineRun,
    ) -> Result<RenderedOpeningMessage, CollaborationRuntimeError> {
        if let Some(saved) = self.runs.get_run_opening(&run.run_id).await? {
            return self.opening_from_payload(run, &saved.payload);
        }
        if let Some(message) = self.persisted_state_machine_opening_message(run).await? {
            return Ok(message);
        }
        Err(CollaborationRuntimeError::InvalidRequest("Run has no persisted opening payload or history".into()))
    }
}

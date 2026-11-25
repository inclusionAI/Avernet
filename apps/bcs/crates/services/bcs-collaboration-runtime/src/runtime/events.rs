use super::*;
pub(super) fn event_timestamp(milliseconds: u64) -> Result<String, CollaborationRuntimeError> {
    Utc.timestamp_millis_opt(i64::try_from(milliseconds).map_err(|_| {
        CollaborationRuntimeError::InvalidRequest("Event timestamp is out of range".to_string())
    })?)
    .single()
    .map(|value| value.to_rfc3339_opts(SecondsFormat::Millis, true))
    .ok_or_else(|| {
        CollaborationRuntimeError::InvalidRequest("Event timestamp is invalid".to_string())
    })
}

pub(super) fn content_value(value: Value) -> Result<Value, CollaborationRuntimeError> {
    let size = serde_json::to_vec(&value)
        .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?
        .len();
    Ok(serde_json::json!({
        "content_type": "application/json",
        "size_bytes": size,
        "json": value,
        "truncated": false
    }))
}

fn normalize_state_machine_event_payload(
    payload: &Value,
    group_id: &str,
    session_id: &str,
    run_id: &str,
    state: &ChatEventState,
) -> Value {
    let mut payload = payload.clone();
    if let Some(object) = payload.as_object_mut() {
        object
            .entry("run_id".to_string())
            .or_insert_with(|| Value::String(run_id.to_string()));
        object
            .entry("bcs_group_id".to_string())
            .or_insert_with(|| Value::String(group_id.to_string()));
        object
            .entry("bcs_session_id".to_string())
            .or_insert_with(|| Value::String(session_id.to_string()));
        object
            .entry("state".to_string())
            .or_insert_with(|| Value::String(chat_event_state_slug(state).to_string()));
    }
    payload
}

fn workbench_event_name<'a>(event_type: &'a str, state: &ChatEventState) -> &'a str {
    match event_type {
        "agent" => "agent",
        "chat" | "chat.event" => match state {
            ChatEventState::ToolCallStart | ChatEventState::ToolCallEnd => "agent",
            ChatEventState::Delta
            | ChatEventState::Final
            | ChatEventState::Error
            | ChatEventState::Aborted => "chat",
        },
        _ => event_type,
    }
}

pub(super) fn chat_event_state_slug(state: &ChatEventState) -> &'static str {
    match state {
        ChatEventState::Delta => "delta",
        ChatEventState::Final => "final",
        ChatEventState::Aborted => "aborted",
        ChatEventState::Error => "error",
        ChatEventState::ToolCallStart => "tool_call_start",
        ChatEventState::ToolCallEnd => "tool_call_end",
    }
}

pub(super) fn bot_display_name(group: &Group, bot_id: &str) -> Option<String> {
    group
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == bot_id)
        .and_then(|participant| participant.bot_name.clone())
}

pub(super) fn state_machine_message_metadata(
    run: &StateMachineRun,
    node: &StateMachineNodeRun,
    event: &str,
    execution: Option<&bcs_domain::StateMachineNodeExecutionMetadata>,
) -> Value {
    let mut value = serde_json::json!({
        "state_machine": {
            "run_id": run.run_id.clone(),
            "definition_id": run.definition_id.clone(),
            "definition_version": run.definition_version,
            "node_id": node.node_id.clone(),
            "attempt": node.attempt,
            "event": event,
            "status": node.status,
            "assignee_bot_id": node.assignee_bot_id.clone(),
            "delivery_request_id": node.delivery_request_id.clone(),
            "bot_delivery_run_id": node.bot_delivery_run_id.clone(),
        }
    });
    if let Some(execution) = execution { value["state_machine"]["execution"] = serde_json::json!(execution); }
    value
}

pub(super) fn state_machine_human_input_prompt_metadata(
    run: &StateMachineRun,
    node: &StateMachineNodeRun,
    pending: &PendingHumanNodeView,
    assignee_actor_ids: &[String],
) -> Value {
    let mut value = serde_json::json!({
        "state_machine": {
            "run_id": run.run_id.clone(),
            "definition_id": run.definition_id.clone(),
            "definition_version": run.definition_version,
            "node_id": node.node_id.clone(),
            "attempt": node.attempt,
            "event": "human_input_prompt",
            "status": node.status,
            "assignee_actor_ids": assignee_actor_ids,
            "response_ref": pending.response_ref,
            "timeout_deadline_ms": pending.timeout_deadline_ms,
        }
    });
    if let Some(execution) = &pending.execution {
        value["state_machine"]["execution"] = serde_json::json!(execution);
    }
    value
}

pub(super) fn state_machine_panel_metadata(run: &StateMachineRun, component: Option<&str>) -> Value {
    let mut state_machine = serde_json::json!({
        "run_id": run.run_id.clone(),
        "definition_id": run.definition_id.clone(),
        "definition_version": run.definition_version,
        "event": "panel",
    });
    if let Some(component) = component {
        state_machine["component"] = Value::String(component.to_string());
    }
    serde_json::json!({ "state_machine": state_machine })
}impl CollaborationRuntime {

    pub(super) fn prepare_public_event(
        &self,
        event_type: &str,
        run: &StateMachineRun,
        subject_type: &str,
        subject_id: &str,
        producer_key_suffix: &str,
        actor_id: Option<&str>,
        actor_type: Option<EventActorType>,
        occurred_at_ms: u64,
        data: BTreeMap<String, Value>,
    ) -> Result<Option<bcs_service_api::port::repo::AppendEventRecord>, CollaborationRuntimeError>
    {
        let Some(factory) = self.event_record_factory.as_ref() else {
            return Ok(None);
        };
        let occurred_at = event_timestamp(occurred_at_ms)?;
        factory
            .prepare(NewEvent {
                event_id: format!("evt_{}", Uuid::new_v4()),
                event_type: event_type.to_string(),
                schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
                producer: "bcs-state-machine".to_string(),
                producer_key: format!("{event_type}:{}:{producer_key_suffix}", run.run_id),
                occurred_at,
                subject: EventSubject {
                    subject_type: subject_type.to_string(),
                    id: subject_id.to_string(),
                },
                scope: EventScope {
                    group_id: Some(run.group_id.clone()),
                    session_id: Some(run.session_id.clone()),
                    run_id: Some(run.run_id.clone()),
                    ..EventScope::default()
                },
                stream_key: format!("state-machine-run:{}", run.run_id),
                actor: actor_id.zip(actor_type).map(|(id, actor_type)| EventActor {
                    actor_type,
                    id: id.to_string(),
                    display_name: None,
                }),
                correlation_id: Some(run.run_id.clone()),
                causation_event_id: None,
                trace_id: None,
                data,
            })
            .map_err(|error| {
                CollaborationRuntimeError::Internal(ServiceError::InternalError(error.to_string()))
            })
    }

    pub(super) async fn publish_state_machine_panel_event(
        &self,
        group: &Group,
        run: &StateMachineRun,
        opening_message: &RenderedOpeningMessage,
    ) {
        let Some(frontend_delivery) = self.frontend_delivery.as_ref() else {
            return;
        };
        let content = opening_message.content.clone();
        let metadata = state_machine_panel_metadata(run, opening_message.component.as_deref());
        let payload = serde_json::json!({
            "run_id": run.run_id.clone(),
            "bcs_group_id": group.id.clone(),
            "bcs_session_id": run.session_id.clone(),
            "state": "final",
            "role": "assistant",
            "sender": BCS_STATE_MACHINE_MESSAGE_SENDER,
            "content": content.clone(),
            "message_type": "bot",
            "bot_name": BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
            "metadata": metadata,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": content}],
                "timestamp": run.created_at,
            },
        });
        let frame = serde_json::json!({
            "type": "event",
            "event": "chat",
            "payload": payload.clone(),
            "group_id": group.id.clone(),
            "bot_uuid": BCS_STATE_MACHINE_MESSAGE_SENDER,
        });
        let fallback = BcsFrame::Event(EventFrame::new("chat", Some(payload), None));
        let publish_result = tokio::time::timeout(
            Duration::from_millis(500),
            frontend_delivery.publish(FrontendDeliveryCommand {
                target: FrontendDeliveryTarget::Session {
                    session_id: run.session_id.clone(),
                },
                event_json: frame.to_string(),
                delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
                run_fallback: Some(RunFallbackDelivery {
                    run_id: run.run_id.clone(),
                    session_id: run.session_id.clone(),
                    event_json: serde_json::to_string(&fallback)
                        .unwrap_or_else(|_| frame.to_string()),
                }),
                exclude_conn_id: None,
                visibility_domain: MessageVisibilityDomain::StateMachine,
                audience: Some(MessageAudience::Public),
                // State-machine panel events authorize per event
                // against current facts (v1 boundary: SM runs are
                // not yet bound into operation permits).
                operation_id: None,
                terminal: false,
                run_id: None,
            }),
        )
        .await;
        match publish_result {
            Ok(Ok(_)) => {}
            Ok(Err(error)) => {
                warn!(target: RUNTIME_LOG_TARGET,
                    run_id = %run.run_id,
                    session_id = %run.session_id,
                    error = %error,
                    "state_machine: failed to publish panel event to frontend"
                );
            }
            Err(_) => {
                warn!(target: RUNTIME_LOG_TARGET,
                    run_id = %run.run_id,
                    session_id = %run.session_id,
                    "state_machine: timed out publishing panel event to frontend"
                );
            }
        }
    }

    pub(super) async fn persist_state_machine_panel_message(
        &self,
        run: &StateMachineRun,
        opening_message: &RenderedOpeningMessage,
    ) -> Result<(), CollaborationRuntimeError> {
        let Some(message_repo) = self.message_repo.as_ref() else {
            return Err(CollaborationRuntimeError::Internal(
                ServiceError::InvalidOperation {
                    message: "state-machine panel message repository is not configured".to_string(),
                    request_id: None,
                },
            ));
        };
        let message_id = format!("{}:000-panel", run.run_id);
        let expected_content = serde_json::json!({
                    "text": opening_message.content,
                    "bot_name": BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
                    "metadata": state_machine_panel_metadata(
                        run,
                        opening_message.component.as_deref(),
                    ),
                });
        let saved = message_repo
            .append_message_with_id(message_id.clone(), NewMessage {
                group_id: run.group_id.clone(),
                session_id: run.session_id.clone(),
                sender_id: BCS_STATE_MACHINE_MESSAGE_SENDER.to_string(),
                sender_type: SenderType::Bot,
                message_type: STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string(),
                content: expected_content.clone(),
                client_msg_id: Some(message_id.clone()),
                owner_bot_id: None,
                created_at: run.created_at,
                run_id: run.run_id.clone(),
                visibility_domain: MessageVisibilityDomain::StateMachine,
                audience: Some(MessageAudience::Public),
            })
            .await
            .map_err(|error| {
                CollaborationRuntimeError::Internal(ServiceError::InternalError(format!(
                    "state-machine panel history persistence failed: {error}"
                )))
            })?;
        if saved.group_id != run.group_id || saved.session_id != run.session_id || saved.run_id != run.run_id
            || saved.sender_id != BCS_STATE_MACHINE_MESSAGE_SENDER || saved.message_type != STATE_MACHINE_PANEL_MESSAGE_TYPE
            || saved.client_msg_id.as_deref() != Some(message_id.as_str()) || saved.content != expected_content
            || saved.visibility_domain != Some(MessageVisibilityDomain::StateMachine) || saved.audience != Some(MessageAudience::Public)
            || saved.sender_type != SenderType::Bot || saved.created_at != run.created_at {
            return Err(CollaborationRuntimeError::Conflict("opening history does not match its immutable checkpoint".into()));
        }
        Ok(())
    }

    pub(super) async fn publish_state_machine_bot_event(
        &self,
        run: &StateMachineRun,
        bot_id: &str,
        delivery_run_id: &str,
        event_type: &str,
        event_payload: &Value,
        state: &ChatEventState,
    ) {
        let Some(frontend_delivery) = self.frontend_delivery.as_ref() else {
            return;
        };
        let frontend_event = workbench_event_name(event_type, state);
        let payload = normalize_state_machine_event_payload(
            event_payload,
            &run.group_id,
            &run.session_id,
            delivery_run_id,
            state,
        );
        let frame = serde_json::json!({
            "type": "event",
            "event": frontend_event,
            "payload": payload.clone(),
            "group_id": run.group_id.clone(),
            "bot_uuid": bot_id,
        });
        let fallback =
            BcsFrame::Event(EventFrame::new(event_type.to_string(), Some(payload), None));
        if let Err(error) = frontend_delivery
            .publish(FrontendDeliveryCommand {
                target: FrontendDeliveryTarget::Session {
                    session_id: run.session_id.clone(),
                },
                event_json: frame.to_string(),
                delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
                run_fallback: Some(RunFallbackDelivery {
                    run_id: delivery_run_id.to_string(),
                    session_id: run.session_id.clone(),
                    event_json: serde_json::to_string(&fallback)
                        .unwrap_or_else(|_| frame.to_string()),
                }),
                exclude_conn_id: None,
                visibility_domain: MessageVisibilityDomain::StateMachine,
                audience: Some(MessageAudience::FullOnly),
                // State-machine bot events authorize per event
                // against current facts (v1 boundary: SM runs are
                // not yet bound into operation permits).
                operation_id: None,
                terminal: false,
                run_id: None,
            })
            .await
        {
            warn!(target: RUNTIME_LOG_TARGET,
                run_id = %run.run_id,
                bot_delivery_run_id = %delivery_run_id,
                session_id = %run.session_id,
                bot_id = %bot_id,
                error = %error,
                "state_machine: failed to publish bot event to frontend"
            );
        }
    }
}

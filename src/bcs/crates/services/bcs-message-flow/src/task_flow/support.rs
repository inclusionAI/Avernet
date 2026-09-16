use super::*;

pub(super) struct TaskCompleteScope {
    pub(super) group_id: String,
    pub(super) session_id: Option<String>,
}

pub(super) async fn resolve_task_complete_scope(
    flow: &BcsMessageFlow,
    raw_group_id: &str,
    payload: &Value,
) -> ServiceResult<TaskCompleteScope> {
    if let Some(session_id) = task_session_id(payload) {
        let group_id = session_group_id(flow, session_id)
            .await?
            .unwrap_or_else(|| raw_group_id.to_string());
        return Ok(TaskCompleteScope {
            group_id,
            session_id: Some(session_id.to_string()),
        });
    }

    if let Some(group_id) = session_group_id(flow, raw_group_id).await? {
        return Ok(TaskCompleteScope {
            group_id,
            session_id: Some(raw_group_id.to_string()),
        });
    }

    Ok(TaskCompleteScope {
        group_id: raw_group_id.to_string(),
        session_id: None,
    })
}

pub(super) async fn session_group_id(
    flow: &BcsMessageFlow,
    session_id: &str,
) -> ServiceResult<Option<String>> {
    let Some(session_management) = flow.session_management.as_ref() else {
        return Ok(None);
    };
    session_management
        .get(session_id)
        .await
        .map(|session| session.map(|session| session.group_id))
        .map_err(|error| ServiceError::InternalError(error.to_string()))
}

pub(super) async fn complete_session_target(
    flow: &BcsMessageFlow,
    group_id: &str,
    session_id: &str,
    status: &str,
    payload: &Value,
) -> ServiceResult<Option<Session>> {
    let Some(session_management) = flow.session_management.as_ref() else {
        return Err(ServiceError::InvalidOperation {
            message: "task complete targets a session but session management is unavailable"
                .to_string(),
            request_id: Some(session_id.to_string()),
        });
    };
    if !session_belongs_to_group(session_management.as_ref(), session_id, group_id).await? {
        return Err(ServiceError::SessionNotFound(session_id.to_string()));
    }
    complete_session_with_summary(session_management.as_ref(), session_id, status, payload).await
}

pub(super) async fn complete_service_session_if_needed(
    flow: &BcsMessageFlow,
    group: &Group,
    group_id: &str,
    status: &str,
    payload: &Value,
) -> ServiceResult<Option<Session>> {
    if group.service_spec.is_none() {
        return Ok(None);
    }
    let Some(session_management) = flow.session_management.as_ref() else {
        return Ok(None);
    };
    let target_session_id = match task_session_id(payload) {
        Some(session_id)
            if session_belongs_to_group(session_management.as_ref(), session_id, group_id)
                .await? =>
        {
            Some(session_id.to_string())
        }
        _ => latest_running_service_session(session_management.as_ref(), group_id)
            .await?
            .map(|session| session.id),
    };
    let Some(session_id) = target_session_id else {
        return Ok(None);
    };
    complete_session_with_summary(session_management.as_ref(), &session_id, status, payload).await
}

pub(super) async fn complete_session_with_summary(
    session_management: &dyn bcs_service_api::SessionManagementService,
    session_id: &str,
    status: &str,
    payload: &Value,
) -> ServiceResult<Option<Session>> {
    let summary = payload
        .get("summary")
        .and_then(|value| value.as_str())
        .unwrap_or_default();
    let output = if summary.is_empty() {
        None
    } else {
        Some(Value::String(summary.to_string()))
    };
    let error = (status == "error").then(|| summary.to_string());
    session_management
        .complete_if_running(session_id, output, error)
        .await
        .map_err(|error| ServiceError::InternalError(error.to_string()))
}

pub(super) async fn session_belongs_to_group(
    session_management: &dyn bcs_service_api::SessionManagementService,
    session_id: &str,
    group_id: &str,
) -> ServiceResult<bool> {
    session_management
        .get(session_id)
        .await
        .map(|session| session.is_some_and(|session| session.group_id == group_id))
        .map_err(|error| ServiceError::InternalError(error.to_string()))
}

pub(super) async fn latest_running_service_session(
    session_management: &dyn bcs_service_api::SessionManagementService,
    group_id: &str,
) -> ServiceResult<Option<Session>> {
    let mut sessions = session_management
        .list_by_group(group_id, Some(SessionStatus::Running), 0, 100, None, None)
        .await
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    sessions.retain(|session| session.session_kind == SessionKind::ServiceInvocation);
    sessions.sort_by(|a, b| {
        b.updated_at
            .cmp(&a.updated_at)
            .then_with(|| b.created_at.cmp(&a.created_at))
    });
    Ok(sessions.into_iter().next())
}

pub(super) fn ensure_task_dispatch_allowed(group: &Group) -> ServiceResult<()> {
    if group.service_mode.as_deref() == Some("master_slave") {
        return Ok(());
    }
    if group.group_strategy == GroupStrategy::ManagerWorker {
        return Ok(());
    }
    Err(ServiceError::InvalidOperation {
        message: format!(
            "task methods require service_mode=master_slave or manager_worker group, \
             group {} has service_mode={}",
            group.id,
            group.service_mode.as_deref().unwrap_or("none")
        ),
        request_id: None,
    })
}

pub(super) fn is_task_manager(group: &Group, bot_id: &str) -> bool {
    if group.group_strategy == GroupStrategy::ManagerWorker {
        return group.participants.iter().any(|participant| {
            participant.bot_uuid == bot_id && participant.role == group.group_strategy.lead_role()
        });
    }
    group.driver_bot == bot_id
}

pub(super) fn is_task_worker(group: &Group, bot_id: &str) -> bool {
    group.group_strategy == GroupStrategy::ManagerWorker
        && group.participants.iter().any(|participant| {
            participant.bot_uuid == bot_id && participant.role == ParticipantRole::Worker
        })
}

pub(super) fn task_manager_error_message<'a>(group: &Group, action: &'a str) -> String {
    if group.group_strategy == GroupStrategy::ManagerWorker {
        return format!("only the manager bot can {action}");
    }
    format!("only the driver bot can {action}")
}

pub(super) async fn resolve_participant_name(flow: &BcsMessageFlow, participant: &Participant) -> String {
    if let Some(name) = participant.bot_name.clone().filter(|name| !name.is_empty()) {
        return name;
    }
    flow.registry
        .get(&participant.bot_uuid)
        .await
        .and_then(|bot| bot.capabilities.name.clone())
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| participant.bot_uuid.clone())
}

pub(super) fn task_message(payload: &Value) -> String {
    payload
        .get("message")
        .and_then(|value| value.as_str())
        .map(str::to_string)
        .unwrap_or_else(|| payload.to_string())
}

pub(super) fn task_response_mode(group: &Group, payload: &Value) -> ChatResponseMode {
    payload
        .get("response_mode")
        .or_else(|| payload.get("responseMode"))
        .cloned()
        .and_then(|value| serde_json::from_value(value).ok())
        .unwrap_or_else(|| {
            if group.group_strategy == GroupStrategy::ManagerWorker {
                ChatResponseMode::AfterLastToolCall
            } else {
                ChatResponseMode::Full
            }
        })
}

pub(super) async fn publish_task_message_to_workbench(
    flow: &BcsMessageFlow,
    group: &Group,
    manager_session_id: &str,
    worker_bot: &str,
    worker_name: &str,
    message: &str,
) -> Vec<FrontendDeliveryResult> {
    let event_json = build_workbench_task_message_event(
        group,
        manager_session_id,
        worker_bot,
        worker_name,
        message,
    );
    let mut actor_ids = vec![worker_bot.to_string()];
    actor_ids.extend(
        group
            .participants
            .iter()
            .filter(|participant| participant.role == ParticipantRole::Manager)
            .map(|participant| participant.bot_uuid.clone()),
    );
    let audience = MessageAudience::directed(actor_ids).unwrap_or(MessageAudience::FullOnly);
    let delivery = flow
        .frontend_delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: manager_session_id.to_string(),
            },
            event_json,
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::ManagerWorker,
            audience: Some(audience),
        })
        .await;

    match delivery {
        Ok(result) => vec![result],
        Err(error) => {
            warn!(
                group_id = %group.id,
                bcs_session_id = %manager_session_id,
                worker = %worker_bot,
                error = %error,
                "failed to publish task message to workbench"
            );
            Vec::new()
        }
    }
}

pub(super) fn task_dispatch_scope(group_id: &str, payload: &Value) -> (String, Option<String>) {
    let (real_group_id, legacy_session_id) = unwrap_legacy_session_group_id(group_id);
    let session_id = task_session_id(payload)
        .map(str::to_string)
        .or_else(|| legacy_session_id.map(str::to_string));
    (real_group_id.to_string(), session_id)
}

pub(super) fn unwrap_legacy_session_group_id(group_id: &str) -> (&str, Option<&str>) {
    match group_id.split_once(':') {
        Some((real_group_id, _)) if !real_group_id.is_empty() => (real_group_id, Some(group_id)),
        _ => (group_id, None),
    }
}

pub(super) fn task_session_id(payload: &Value) -> Option<&str> {
    payload
        .get("bcs_session_id")
        .or_else(|| payload.get("session_id"))
        .and_then(|value| value.as_str())
        .filter(|value| !value.is_empty())
}

pub(crate) async fn apply_session_participants(
    flow: &BcsMessageFlow,
    group: &mut Group,
    group_id: &str,
    session_id: &str,
) -> ServiceResult<()> {
    let Some(session_management) = flow.session_management.as_ref() else {
        return Ok(());
    };
    let session = session_management
        .get(session_id)
        .await
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
    if session.group_id != group_id {
        return Err(ServiceError::InvalidOperation {
            message: format!(
                "session '{}' does not belong to group '{}'",
                session_id, group_id
            ),
            request_id: None,
        });
    }
    if session.participants.is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: format!("session '{}' has no participants", session_id),
            request_id: None,
        });
    }
    group.participants = session.participants;
    bcs_service_api::backfill_bot_names(flow.registry.as_ref(), group).await;
    Ok(())
}

pub(super) fn build_task_dispatch_frame(
    group: &Group,
    driver_bot: &str,
    driver_name: &str,
    target_bot: &str,
    target_bot_name: &str,
    message: &str,
    task_id: &str,
    manager_session_id: &str,
    tags: &[String],
    now_ms: u64,
) -> BcsFrame {
    let participant_names: Vec<String> = group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
        .map(|participant| {
            participant
                .bot_name
                .clone()
                .unwrap_or_else(|| participant.bot_uuid.clone())
        })
        .collect();
    let group_context = GroupContext {
        session_id: manager_session_id.to_string(),
        participants: participant_names,
        originator: driver_name.to_string(),
        from: driver_name.to_string(),
        you_are_mentioned: true,
        is_sender: false,
        mentions: vec![target_bot_name.to_string()],
        message: message.to_string(),
        response_directive: None,
        recipient: Some(target_bot.to_string()),
        recipient_name: Some(target_bot_name.to_string()),
        recipient_role: Some("worker".to_string()),
        delivery_type: Some(delivery_slug(DeliveryType::Send).to_string()),
        routing_mode: None,
        group_type: group_type_wire(group.group_strategy)
            .or_else(|| group.service_mode.clone())
            .or(Some("task".to_string())),
        from_bot_id: None,
        from_bot_owner: None,
    };
    let mut params = serde_json::json!({
        "session_key": manager_session_id,
        "bcs_group_id": manager_session_id,
        "bcs_session_id": manager_session_id,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": format!("[from:{}] {}", driver_name, message)}],
            "timestamp": now_ms / 1000,
        },
        "channel": {
            "source": "api",
            "user_id": driver_bot,
            "actor_id": driver_bot,
            "actor_name": driver_name,
            "thread_id": group.id,
        },
        "session_context": group_context,
        "timeout_ms": null,
        "idempotency_key": null,
    });
    if !tags.is_empty() {
        params["tags"] = serde_json::json!(tags);
    }

    BcsFrame::Request(RequestFrame::new(
        task_id.to_string(),
        "chat.send",
        Some(params),
    ))
}

pub(super) fn build_task_message_frame(
    group: &Group,
    manager_session_id: &str,
    worker_bot: &str,
    worker_name: &str,
    manager_bot: &str,
    manager_name: &str,
    message: &str,
    run_id: &str,
    tags: &[String],
) -> BcsFrame {
    let participant_names: Vec<String> = group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
        .map(|participant| {
            participant
                .bot_name
                .clone()
                .unwrap_or_else(|| participant.bot_uuid.clone())
        })
        .collect();
    let group_context = GroupContext {
        session_id: manager_session_id.to_string(),
        participants: participant_names,
        originator: manager_name.to_string(),
        from: worker_name.to_string(),
        you_are_mentioned: true,
        is_sender: false,
        mentions: vec![manager_name.to_string()],
        message: message.to_string(),
        response_directive: None,
        recipient: Some(manager_bot.to_string()),
        recipient_name: Some(manager_name.to_string()),
        recipient_role: Some("manager".to_string()),
        delivery_type: Some(delivery_slug(DeliveryType::Send).to_string()),
        routing_mode: None,
        group_type: group_type_wire(group.group_strategy)
            .or_else(|| group.service_mode.clone())
            .or(Some("task".to_string())),
        from_bot_id: Some(worker_bot.to_string()),
        from_bot_owner: None,
    };
    let mut params = serde_json::json!({
        "session_key": manager_session_id,
        "bcs_group_id": manager_session_id,
        "bcs_session_id": manager_session_id,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": format!("[from:{}] {}", worker_name, message)}],
            "timestamp": now_ms() / 1000,
        },
        "channel": {
            "source": "api",
            "user_id": worker_name,
            "actor_id": worker_bot,
            "actor_name": worker_name,
            "thread_id": group.id,
        },
        "session_context": group_context,
        "timeout_ms": null,
        "idempotency_key": null,
    });
    if !tags.is_empty() {
        params["tags"] = serde_json::json!(tags);
    }

    BcsFrame::Request(RequestFrame::new(
        run_id.to_string(),
        "chat.send",
        Some(params),
    ))
}

pub(super) fn build_workbench_task_message_event(
    group: &Group,
    manager_session_id: &str,
    worker_bot: &str,
    worker_name: &str,
    message: &str,
) -> String {
    let event = serde_json::json!({
        "run_id": uuid::Uuid::new_v4().to_string(),
        "session_key": manager_session_id,
        "bcs_session_id": manager_session_id,
        "seq": 0,
        "state": "final",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": message}],
            "from": worker_bot,
            "from_name": worker_name,
            "mentions": [],
        },
    });
    let frame = serde_json::json!({
        "type": "event",
        "event": "chat",
        "payload": event,
        "group_id": group.id,
        "bot_uuid": worker_bot,
        "bot_name": worker_name,
    });
    serde_json::to_string(&frame).unwrap_or_default()
}

pub(super) fn delivery_slug(delivery_type: DeliveryType) -> &'static str {
    match delivery_type {
        DeliveryType::Send => "send",
        DeliveryType::Inject => "inject",
    }
}

pub(super) fn effective_message_log_session_id<'a>(group_id: &'a str, session_id: Option<&'a str>) -> &'a str {
    session_id
        .filter(|value| !value.is_empty())
        .unwrap_or(group_id)
}

pub(super) fn log_task_dispatch_created(
    group_id: &str,
    session_id: &str,
    task_id: &str,
    manager_bot_id: &str,
    worker_bot_id: &str,
    message: &str,
) {
    let content = MessageLogContent::from_text(message);
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::TaskDispatchCreated.as_str(),
        status = MessageLogStatus::Routed.as_str(),
        mode = MessageLogMode::ManagerWorker.as_str(),
        session_id = %effective_message_log_session_id(group_id, Some(session_id)),
        group_id = %group_id,
        task_id = %task_id,
        run_id = %task_id,
        bot_id = %worker_bot_id,
        manager_bot_id = %manager_bot_id,
        worker_bot_id = %worker_bot_id,
        content = %content.content,
        content_length = content.content_length,
        content_truncated = content.content_truncated,
        content_truncated_bytes = content.content_truncated_bytes,
        "task_dispatch_created"
    );
}

pub(super) fn log_task_complete(
    group_id: &str,
    session_id: Option<&str>,
    task_id: &str,
    bot_id: &str,
    task_status: &str,
) {
    let status = if task_status == "completed" {
        MessageLogStatus::Completed
    } else {
        MessageLogStatus::Failed
    };
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::TaskComplete.as_str(),
        status = status.as_str(),
        mode = MessageLogMode::ManagerWorker.as_str(),
        session_id = %effective_message_log_session_id(group_id, session_id),
        group_id = %group_id,
        task_id = %task_id,
        run_id = %task_id,
        bot_id = %bot_id,
        task_status = %task_status,
        "task_complete"
    );
}

pub(super) fn log_manager_worker_deliver_result(
    group_id: &str,
    session_id: Option<&str>,
    run_id: &str,
    bot_id: &str,
    from_bot_id: Option<&str>,
    delivery_type: DeliveryType,
    delivered: bool,
    error: Option<&str>,
    failure_phase: Option<&str>,
) {
    let status = if delivered {
        MessageLogStatus::Delivered
    } else {
        MessageLogStatus::Failed
    };
    if delivered {
        info!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::BotDeliverResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::ManagerWorker.as_str(),
            session_id = %effective_message_log_session_id(group_id, session_id),
            group_id = %group_id,
            run_id = %run_id,
            task_id = %run_id,
            bot_id = %bot_id,
            from_bot_id = %from_bot_id.unwrap_or(""),
            to_bot_id = %bot_id,
            delivery_type = delivery_slug(delivery_type),
            delivered = delivered,
            error = %error.unwrap_or(""),
            failure_phase = %failure_phase.unwrap_or(""),
            "bot_deliver_result"
        );
    } else {
        warn!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::BotDeliverResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::ManagerWorker.as_str(),
            session_id = %effective_message_log_session_id(group_id, session_id),
            group_id = %group_id,
            run_id = %run_id,
            task_id = %run_id,
            bot_id = %bot_id,
            from_bot_id = %from_bot_id.unwrap_or(""),
            to_bot_id = %bot_id,
            delivery_type = delivery_slug(delivery_type),
            delivered = delivered,
            error = %error.unwrap_or(""),
            failure_phase = %failure_phase.unwrap_or(""),
            "bot_deliver_result"
        );
    }
}

pub(super) fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use bcs_service_api::{Group, GroupStrategy, Participant};

    use super::ensure_task_dispatch_allowed;

    fn make_test_group() -> Group {
        Group::new(
            "g1",
            "driver_bot",
            vec![
                Participant::bot("driver_bot", bcs_service_api::ParticipantRole::Driver),
                Participant::bot("worker_bot", bcs_service_api::ParticipantRole::Worker),
            ],
        )
    }

    #[test]
    fn master_slave_service_mode_allows_dispatch() {
        let mut group = make_test_group();
        group.service_mode = Some("master_slave".to_string());
        assert!(
            ensure_task_dispatch_allowed(&group).is_ok(),
            "master_slave service_mode should allow task dispatch"
        );
    }

    #[test]
    fn manager_worker_strategy_allows_dispatch() {
        let mut group = make_test_group();
        group.group_strategy = GroupStrategy::ManagerWorker;
        assert!(
            ensure_task_dispatch_allowed(&group).is_ok(),
            "ManagerWorker strategy should allow task dispatch"
        );
    }

    #[test]
    fn chat_strategy_without_service_mode_rejects_dispatch() {
        let group = make_test_group();
        let err = ensure_task_dispatch_allowed(&group).unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("task methods require"),
            "expected rejection message, got: {msg}"
        );
    }
}

//! Atomic task completion and independent Manager result admission.
use crate::{BcsMessageFlow, queued_task::{self, TaskLeg, TaskIntent}};
use bcs_domain::message_delivery::{MessageDeliveryStatus as Status, PersistedMessageDelivery};
use bcs_service_api::{BotEventCommand, ChatEventState, ServiceResult, DeliveryTransitionCommand, ManagedDeliveryError};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;

pub(crate) async fn commit(flow: &BcsMessageFlow, row: &PersistedMessageDelivery,
    cmd: &BotEventCommand, result_text: &str, normalized: crate::run_reply::RunReply,
) -> ServiceResult<()> {
    let mut task = queued_task::intent(row)?.ok_or_else(|| queued_task::error("task terminal intent missing"))?;
    if task.leg != TaskLeg::Dispatch { return Err(queued_task::error("task terminal is not an assignment")); }
    task.leg = TaskLeg::Result;
    // Completion is a fact about an already authorized run. Membership changes
    // must not strand its lane; current authorization is checked before the
    // independent Manager delivery performs network I/O.
    let session = flow.session_management.as_ref()
        .ok_or_else(|| queued_task::error("task result session service unavailable"))?
        .get(&row.session_id).await.map_err(|e| queued_task::error(&format!("task result session read failed: {e}")))?;
    if session.as_ref().is_some_and(|session| session.group_id != row.group_id) {
        return Err(queued_task::error("task result session scope mismatch"));
    }
    let tags = session.and_then(|session| session.participants.into_iter()
        .find(|p| p.bot_uuid == task.manager).map(|p| p.tags)).unwrap_or_default();
    let projection = crate::queued_group::QueuedGroupProjection::task_result(row, task.clone(), tags)?;
    let now = chrono::Utc::now().timestamp_millis();
    let (visibility_domain, audience) = crate::group_flow::persisted_message_visibility(
        None, &task.worker, bcs_domain::SenderType::Bot, "chat", None)?;
    let reply = bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries {
        display_message:None, message_id:result_message_id(&task.task_id), event:None,
        message:bcs_domain::NewMessage { group_id:row.group_id.clone(), session_id:row.session_id.clone(),
            sender_id:task.worker.clone(), sender_type:bcs_domain::SenderType::Bot, message_type:"run_reply".into(),
            content:serde_json::json!({}), client_msg_id:None, owner_bot_id:None, visibility_domain, audience,
            created_at:now as u64, run_id:cmd.run_id.clone() },
        flow_kind:bcs_domain::message_delivery::DeliveryFlowKind::Task,
        targets:vec![bcs_service_api::port::repo::message_delivery::DeliveryAdmissionTarget {
            rejection:None, target_bot_id:task.manager.clone(), kind:bcs_domain::DeliveryType::Send,
            max_queued:100, semantic_projection_json:serde_json::to_value(projection)? }],
        now_ms:now, expire_at_ms:None,
    };
    let reply = normalize_result(flow, reply, &task, cmd, result_text, normalized)?;
    let service = flow.managed_deliveries.as_ref().ok_or_else(|| queued_task::error("task queue unavailable"))?;
    let mut current = row.clone();
    for _ in 0..3 {
        let transition = DeliveryTransitionCommand { delivery_id:current.delivery_id.clone(), expected_state_version:current.state.state_version,
            event:match cmd.state { ChatEventState::Final => Event::Completed, ChatEventState::Aborted => Event::Aborted, _ => Event::Failed },
            now_ms:reply.now_ms, request_id:None, actor_id:None, reply:Some(reply.clone()), transport_context_json:None, deadline_at_ms:None };
        match crate::storage_retry::retry(crate::storage_retry::shutdown(flow), "task_terminal_commit",
            crate::storage_retry::managed_storage, || service.transition(transition.clone())).await {
            Ok(committed) => { queued_task::restore(flow, &committed).await?; return Ok(()); }
            Err(ManagedDeliveryError::Conflict) | Err(ManagedDeliveryError::Lifecycle(_)) => {},
            Err(error) => return Err(queued_task::error(&format!("task terminal transaction failed: {error}"))),
        }
        current = crate::queued_admission::find_managed_run(flow, cmd).await?
            .ok_or_else(|| queued_task::error("task terminal disappeared"))?;
        if matches!(current.state.status, Status::Completed | Status::Failed | Status::Cancelled) {
            queued_task::restore(flow, &current).await?;
            return Ok(());
        }
    }
    Err(queued_task::error("task terminal changed concurrently"))
}

/// Stable identity lets terminal callback retries read the committed result,
/// never rebuild it from a potentially different final payload.
pub(crate) fn result_message_id(task_id: &str) -> String { format!("task-result:{task_id}") }

async fn result_admission(flow: &BcsMessageFlow, group: &bcs_domain::Group, session: &str,
    task: TaskIntent, cmd: &BotEventCommand, result_text: &str, drain: bool,
) -> ServiceResult<bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries> {
    let reply = queued_task::command(flow, group, session, task.clone(), result_text, None,
        &task.worker_name, drain).await?;
    let normalized = if cmd.state == ChatEventState::Error {
        let text = crate::bot_event::error_display_text(&cmd.event_payload);
        crate::run_reply::RunReply {
            text: text.clone(),
            display: flow
                .message_tracker
                .peek_chat_buf(&crate::run_reply::chat_key(cmd))
                .await
                .unwrap_or_default(),
            method: "error",
            raw_final: text,
            source_ids: Vec::new(),
        }
    } else {
        crate::run_reply::prepare(
            flow,
            cmd,
            &crate::bot_event::extract_message_text(&cmd.event_payload),
            false,
        )
        .await?
    };
    normalize_result(flow, reply, &task, cmd, result_text, normalized)
}

fn normalize_result(flow: &BcsMessageFlow,
    mut reply: bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries,
    task: &TaskIntent, cmd: &BotEventCommand, result_text: &str, normalized: crate::run_reply::RunReply,
) -> ServiceResult<bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries> {
    // The canonical result carries the response-mode projection. The full run
    // body is retained in the same source so recovery never relies on final alone.
    let result_text = match cmd.state {
        ChatEventState::Final => result_text.to_string(),
        ChatEventState::Aborted => format!("[task cancelled] {result_text}"),
        _ => format!("[task failed] {result_text}"),
    };
    reply.message.run_id = cmd.run_id.clone();
    if cmd.state == ChatEventState::Error {
        reply.message.content = serde_json::Value::String(normalized.text.clone());
        reply.message.client_msg_id = Some(format!("chat-error:{}", task.task_id));
        reply.message.message_type = bcs_domain::CHAT_ERROR_MESSAGE_TYPE.into();
    } else {
        reply.message.content["task_result_text"] = serde_json::json!(result_text);
        reply.message.content["text"] = serde_json::json!(normalized.text);
        reply.message.content["task_state"] = serde_json::json!(match cmd.state {
            ChatEventState::Final => "completed",
            ChatEventState::Aborted => "cancelled",
            _ => "failed",
        });
        reply.message.client_msg_id = Some(format!("task-result:{}", task.task_id));
        reply.message.message_type = "run_reply".into();
    }
    reply.event = None;
    if !normalized.display.is_empty() {
        let mut display = reply.message.clone();
        display.message_type = "chat".into();
        display.content = serde_json::Value::String(normalized.display);
        display.client_msg_id = Some(format!("task-display:{}", task.task_id));
        let id = uuid::Uuid::new_v4().to_string();
        let event = crate::queued_admission::prepare_message_event(flow, &id, &display)?;
        reply.display_message = Some(bcs_service_api::port::repo::message_delivery::DeliveryDisplayMessage { message_id:id, message:display, event });
    }
    Ok(reply)
}

pub(crate) async fn admit_legacy_result(flow: &BcsMessageFlow, entry: &crate::task_store::TaskEntry,
    group: &bcs_domain::Group, cmd: &BotEventCommand, result_text: &str,
) -> ServiceResult<bool> {
    let session = entry.session_id.as_deref().unwrap_or(&entry.group_id);
    let Some(drain) = queued_task::admission_mode(flow, group, session, &entry.driver_bot).await? else { return Ok(false); };
    let task = TaskIntent { leg:TaskLeg::Result, task_id:entry.task_id.clone(), manager:entry.driver_bot.clone(),
        worker:entry.target_bot.clone(), worker_name:entry.target_bot_name.clone().unwrap_or_else(|| entry.target_bot.clone()),
        response_mode:entry.response_mode };
    let reply = result_admission(flow, group, session, task, cmd, result_text, drain).await?;
    // Capacity rejection is itself durable. The Worker has finished even if
    // the Manager result cannot be admitted; never re-execute the Worker.
    flow.managed_deliveries.as_ref().ok_or_else(|| queued_task::error("task queue unavailable"))?
        .admit(reply).await.map_err(|e| queued_task::error(&format!("task result admission failed: {e}")))?;
    if cmd.state == ChatEventState::Final { flow.task_store.mark_replied(&entry.task_id).await; }
    else { flow.task_store.mark_failed(&entry.task_id).await; }
    Ok(true)
}

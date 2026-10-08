//! Shared abnormal TaskResult text, atomic projection and legacy completion entry.
use crate::queued_task::{TaskIntent, TaskLeg};
use bcs_domain::message_delivery::{MessageDeliveryStatus as Status, PersistedMessageDelivery};
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget};
use bcs_service_api::ServiceResult;

pub(crate) fn callback_text(task: &TaskIntent, cmd: &bcs_service_api::BotEventCommand, partial: bool) -> String {
    let cancelled = cmd.state == bcs_service_api::ChatEventState::Aborted;
    let reason = if cancelled {
        cmd.event_payload.get("reason").and_then(|v| v.as_str()).unwrap_or_default().to_string()
    } else {
        let text = crate::bot_event::error_display_text(&cmd.event_payload);
        if text == "本次回复失败，请稍后重试。" { String::new() } else { text }
    };
    text(task, cancelled, &reason, partial)
}

pub(crate) fn entry_intent(entry: &crate::task_store::TaskEntry) -> TaskIntent {
    TaskIntent { leg:TaskLeg::Result, task_id:entry.task_id.clone(), manager:entry.driver_bot.clone(),
        worker:entry.target_bot.clone(), worker_name:entry.target_bot_name.clone().unwrap_or_else(|| entry.target_bot.clone()),
        response_mode:entry.response_mode, assignment_intent_id:entry.assignment_intent_id.clone(), summary:entry.summary.clone() }
}

/// Legacy task failure/explicit abort enters the same callback result path,
/// including its per-task event lock. No synthetic normal reply is generated.
pub(crate) async fn finish_legacy(flow: &crate::BcsMessageFlow, task_id: &str, cancelled: bool, reason: &str) -> ServiceResult<()> {
    let Some(entry) = flow.task_store.get(task_id).await else { return Ok(()); };
    let cmd = bcs_service_api::BotEventCommand { bot_id:entry.target_bot, run_id:entry.task_id,
        group_id:entry.group_id, bcs_session_id:entry.session_id, event_type:"chat.event".into(),
        state:if cancelled { bcs_service_api::ChatEventState::Aborted } else { bcs_service_api::ChatEventState::Error },
        event_payload:serde_json::json!({"reason":reason, "errorMessage":reason}) };
    Box::pin(crate::bot_event::handle_bot_event(flow, cmd)).await?;
    Ok(())
}

pub(crate) async fn mark_terminal(flow: &crate::BcsMessageFlow, task_id: &str, state: &bcs_service_api::ChatEventState) {
    match state {
        bcs_service_api::ChatEventState::Final => flow.task_store.mark_replied(task_id).await,
        bcs_service_api::ChatEventState::Aborted => flow.task_store.mark_cancelled(task_id).await,
        _ => flow.task_store.mark_failed(task_id).await,
    }
}

pub(crate) fn text(task: &TaskIntent, cancelled: bool, reason: &str, partial: bool) -> String {
    let heading = if cancelled { "任务中断" } else { "任务失败" };
    let reference = task.assignment_intent_id.as_deref().unwrap_or(&task.task_id);
    let reason = if reason.trim().is_empty() || reason == "[no response]" {
        if cancelled { "本次执行已中断，原因未提供。" } else { "任务执行失败，未提供具体原因。" }
    } else { reason };
    format!("[{heading}]\n派发引用：{reference}\nWorker：{}\n任务：{}\n情况：{}{}",
        task.worker_name, if task.summary.is_empty() { "未记录任务摘要" } else { &task.summary },
        reason, if partial { " 已返回部分内容。" } else { "" })
}

#[cfg(test)]
mod tests {
    #[test]
    fn historical_intent_without_new_fields_uses_task_reference_and_unknown_reason() {
        let task = serde_json::from_value(serde_json::json!({"leg":"dispatch", "task_id":"old-task", "manager":"m",
            "worker":"w", "worker_name":"Worker", "response_mode":"full"})).unwrap();
        let text = super::text(&task, true, "", true);
        assert!(text.contains("派发引用：old-task"));
        assert!(text.contains("原因未提供") && text.contains("已返回部分内容"));
        assert!(!text.contains("用户中断"));
    }
}

/// Called before the same CAS that settles the Worker. The existing stable
/// result identity and transaction prevent callback/control duplicate Sends.
pub(crate) fn fallback(row: &PersistedMessageDelivery, state: bcs_domain::message_delivery::MessageDeliveryState, now: i64,
    actor: Option<&str>, resolution: Option<&serde_json::Value>,
) -> ServiceResult<Option<AdmitMessageDeliveries>> {
    let status = state.status;
    if !matches!(status, Status::Failed | Status::Cancelled) { return Ok(None); }
    let Some(mut task) = crate::queued_task::intent(row)? else { return Ok(None); };
    if task.leg != TaskLeg::Dispatch { return Ok(None); }
    task.leg = TaskLeg::Result;
    let cancelled = status == Status::Cancelled;
    let reason = match (cancelled, state.may_have_been_sent) {
        (true, false) => "任务在开始执行前已取消。",
        (true, true) if actor.or(row.cancel_requested_by.as_deref()).is_some() => "已按停止请求中断本次执行。",
        (true, true) => "本次执行已中断，原因未提供。",
        (false, false) => "任务未能发送给 Worker，尚未开始执行。",
        (false, true) => "任务执行失败，未提供具体原因。",
    };
    let reason = resolution.and_then(|v| v.get("reason").or_else(|| v.get("cancel_reason"))).and_then(|v| v.as_str())
        .or_else(|| row.transport_context_json.as_ref().and_then(|v| v.get("cancel_reason")).and_then(|v| v.as_str()))
        .unwrap_or(reason);
    let text = text(&task, cancelled, reason, false);
    let projection = crate::queued_group::QueuedGroupProjection::task_result(row, task.clone(), Vec::new())?;
    let (visibility_domain, audience) = crate::group_flow::persisted_message_visibility(
        None, &task.worker, bcs_domain::SenderType::Bot, "chat", None)?;
    Ok(Some(AdmitMessageDeliveries {
        message_id:crate::queued_task_terminal::result_message_id(&task.task_id), event:None, display_message:None,
        message:bcs_domain::NewMessage { group_id:row.group_id.clone(), session_id:row.session_id.clone(),
            sender_id:task.worker.clone(), sender_type:bcs_domain::SenderType::Bot, message_type:"run_reply".into(),
            content:serde_json::json!({"task_result_text":text, "text":"", "task_state":if cancelled { "cancelled" } else { "failed" }}),
            client_msg_id:Some(format!("task-result:{}", task.task_id)), owner_bot_id:None, visibility_domain, audience,
            created_at:now as u64, run_id:row.run_id.clone().unwrap_or_default() },
        flow_kind:bcs_domain::message_delivery::DeliveryFlowKind::Task,
        targets:vec![DeliveryAdmissionTarget { rejection:None, target_bot_id:task.manager, kind:bcs_domain::DeliveryType::Send,
            max_queued:100, semantic_projection_json:serde_json::to_value(projection)? }],
        now_ms:now, expire_at_ms:None,
    }))
}

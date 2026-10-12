//! Shared abnormal TaskResult text, atomic projection and legacy completion entry.
use crate::queued_task::{TaskIntent, TaskLeg};
use bcs_domain::message_delivery::{MessageDeliveryStatus as Status, PersistedMessageDelivery};
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget};
use bcs_service_api::ServiceResult;

pub(crate) const TASK_TIMEOUT_REASON: &str = "任务运行已达到设定的超时期限，已按超时规则停止。";

pub(crate) fn is_task_timeout(row: &PersistedMessageDelivery) -> bool {
    row.transport_context_json.as_ref().and_then(|value| value.get("task_timeout"))
        .and_then(|value| value.as_bool()) == Some(true)
}

pub(crate) fn callback_text(task: &TaskIntent, cmd: &bcs_service_api::BotEventCommand, partial: bool) -> String {
    callback_text_with_timeout(task, cmd, partial, false)
}

pub(crate) fn callback_text_with_timeout(task: &TaskIntent, cmd: &bcs_service_api::BotEventCommand,
    partial: bool, timed_out: bool) -> String {
    let cancelled = cmd.state == bcs_service_api::ChatEventState::Aborted;
    let reason = if cancelled {
        cmd.event_payload.get("reason").and_then(|v| v.as_str()).unwrap_or_default().to_string()
    } else {
        let text = crate::bot_event::error_display_text(&cmd.event_payload);
        if text == "本次回复失败，请稍后重试。" { String::new() } else { text }
    };
    text_with_timeout(task, cancelled, timed_out, &reason, partial)
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

fn text_with_timeout(task: &TaskIntent, cancelled: bool, timed_out: bool, reason: &str, partial: bool) -> String {
    let heading = if cancelled && (timed_out || reason == TASK_TIMEOUT_REASON) { "任务超时" }
        else if cancelled { "任务中断" } else { "任务失败" };
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
        let text = super::text_with_timeout(&task, true, false, "", true);
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
    if status == Status::CancelUnknown && is_task_timeout(row) {
        return timeout_unknown_notice(row, now).map(Some);
    }
    if !matches!(status, Status::Failed | Status::Cancelled) { return Ok(None); }
    let Some(mut task) = crate::queued_task::intent(row)? else { return Ok(None); };
    if task.leg != TaskLeg::Dispatch { return Ok(None); }
    task.leg = TaskLeg::Result;
    let cancelled = status == Status::Cancelled;
    let timed_out = cancelled && is_task_timeout(row);
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
    let text = text_with_timeout(&task, cancelled, timed_out, reason, false);
    let projection = crate::queued_group::QueuedGroupProjection::task_result(row, task.clone(), Vec::new())?;
    let (visibility_domain, audience) = crate::group_flow::persisted_message_visibility(
        None, &task.worker, bcs_domain::SenderType::Bot, "chat", None)?;
    Ok(Some(AdmitMessageDeliveries {
        message_id:crate::queued_task_terminal::result_message_id(&task.task_id), event:None, display_message:None,
        // §12.5: failure projection admitted by the WORKER bot of the failed task.
        operation:bcs_service_api::types::BotOperationContext {
            operation_id: format!("task-failure:{:?}", task.task_id),
            actor: bcs_service_api::types::BotOperationActor::Bot { bot_id: task.worker.clone() },
        },
        message:bcs_domain::NewMessage { group_id:row.group_id.clone(), session_id:row.session_id.clone(),
            sender_id:task.worker.clone(), sender_type:bcs_domain::SenderType::Bot, message_type:"run_reply".into(),
            content:serde_json::json!({"task_result_text":text, "text":"", "task_state":if timed_out { "timed_out" } else if cancelled { "cancelled" } else { "failed" }}),
            client_msg_id:Some(format!("task-result:{}", task.task_id)), owner_bot_id:None, visibility_domain, audience,
            created_at:now as u64, run_id:row.run_id.clone().unwrap_or_default() },
        flow_kind:bcs_domain::message_delivery::DeliveryFlowKind::Task,
        targets:vec![DeliveryAdmissionTarget { rejection:None, target_bot_id:task.manager, kind:bcs_domain::DeliveryType::Send,
            max_queued:100, semantic_projection_json:serde_json::to_value(projection)? }],
        now_ms:now, expire_at_ms:None,
    }))
}

fn timeout_unknown_notice(row: &PersistedMessageDelivery, now: i64) -> ServiceResult<AdmitMessageDeliveries> {
    let task = crate::queued_task::intent(row)?.ok_or_else(|| crate::queued_task::error("timeout task intent missing"))?;
    if task.leg != TaskLeg::Dispatch { return Err(crate::queued_task::error("timeout notice requires assignment")); }
    let reference = task.assignment_intent_id.as_deref().unwrap_or(&task.task_id);
    let summary = if task.summary.is_empty() { "未记录任务摘要" } else { &task.summary };
    let message = format!(
        "[任务超时，停止结果未确认]\n派发引用：{reference}\nWorker：{}\n任务：{summary}\n情况：已尝试停止旧任务，但无法确认它已结束；旧任务可能仍在运行。请先核实状态，再决定是否重新派发或结束任务。",
        task.worker_name,
    );
    let projection = crate::queued_group::QueuedGroupProjection::timeout_notice(row, &task.manager)?;
    let (visibility_domain, audience) = bcs_domain::system_message_visibility(
        bcs_domain::GroupStrategy::ManagerWorker, bcs_domain::SystemMessageEventKind::GenericNotification,
        Some(&task.manager));
    Ok(AdmitMessageDeliveries {
        message_id:format!("task-timeout-unknown:{}", task.task_id), event:None,
        display_message:None,
        // §12.5: this diagnostic is authored by the platform's timeout
        // reconciliation itself (no Worker reported it), so the audit lane
        // carries the honest system identity — never a forged Bot/Human.
        operation: bcs_service_api::types::system_lane_operation("task-timeout-unknown-notice"),
        message:bcs_domain::NewMessage { group_id:row.group_id.clone(), session_id:row.session_id.clone(),
            sender_id:"system".into(), sender_type:bcs_domain::SenderType::System,
            message_type:"system".into(), content:serde_json::json!({"text":message}),
            client_msg_id:Some(format!("task-timeout-unknown:{}", task.task_id)),
            owner_bot_id:Some(task.manager.clone()), visibility_domain, audience,
            created_at:now as u64, run_id:String::new() },
        flow_kind:bcs_domain::message_delivery::DeliveryFlowKind::System,
        targets:vec![DeliveryAdmissionTarget { rejection:None, target_bot_id:task.manager,
            kind:bcs_domain::DeliveryType::Send, max_queued:100,
            semantic_projection_json:serde_json::to_value(projection)? }],
        now_ms:now, expire_at_ms:None,
    })
}

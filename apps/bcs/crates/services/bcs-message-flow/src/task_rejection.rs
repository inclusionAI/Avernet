//! Pre-admission rejection/uncertainty notices. No Worker delivery is invented.
use crate::BcsMessageFlow;
use bcs_service_api::{ServiceError, ServiceResult, TaskDispatchCommand};
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget};
use bcs_domain::{DeliveryType, GroupStrategy, NewMessage, SenderType, SystemMessageEventKind};

pub(crate) fn is_rejected(error: &ServiceError) -> bool {
    matches!(error, ServiceError::InvalidOperation { message, .. } if message.starts_with("task_dispatch_rejected:"))
}

pub(crate) async fn reject(flow: &BcsMessageFlow, cmd: &TaskDispatchCommand, original: ServiceError) -> ServiceError {
    if cmd.payload.get("assignment_intent_id").and_then(|v| v.as_str()).is_none() { return original; }
    let reason = original.to_string();
    if let Err(error) = notice(flow, cmd, Some(&reason)).await { return error; }
    ServiceError::InvalidOperation { message: format!("task_dispatch_rejected:{reason}"), request_id: None }
}

/// A terminal-at-admission Worker row already owns the handle. Notify using
/// the same stable notice key, without creating a second indexed pseudo-task.
pub(crate) async fn reject_admitted(flow: &BcsMessageFlow, row: &bcs_domain::message_delivery::PersistedMessageDelivery) -> ServiceError {
    let task = match crate::queued_task::intent(row) {
        Ok(Some(task)) => task,
        _ => return ServiceError::InternalError("rejected task intent missing".into()),
    };
    let reason = if row.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::RejectedCapacity {
        "Worker 任务队列已满，任务未发出"
    } else { "任务准入被拒绝，任务未发出" };
    let cmd = TaskDispatchCommand { caller_delegation:None, driver_bot_id:task.manager, group_id:row.group_id.clone(),
        target_bot_id:task.worker_name, target_bot_name:None,
        payload:serde_json::json!({"assignment_intent_id":task.assignment_intent_id,
            "legacy_task_id":task.task_id, "bcs_session_id":row.session_id}) };
    if let Err(error) = notice_inner(flow, &cmd, Some(reason), false).await { return error; }
    reported_failure(reason.to_string())
}

/// Use only after a definite unsent failure and its Manager outcome have been
/// recorded. The coordination claim must not add a contradictory unknown notice.
pub(crate) fn reported_failure(reason: String) -> ServiceError {
    ServiceError::InvalidOperation { message:format!("task_dispatch_rejected:{reason}"), request_id:None }
}

pub(crate) async fn notice(flow: &BcsMessageFlow, cmd: &TaskDispatchCommand, rejection: Option<&str>) -> ServiceResult<()> {
    notice_inner(flow, cmd, rejection, true).await
}

async fn notice_inner(flow: &BcsMessageFlow, cmd: &TaskDispatchCommand, rejection: Option<&str>, index_rejection: bool) -> ServiceResult<()> {
    let intent = cmd.payload.get("assignment_intent_id").and_then(|v| v.as_str());
    let legacy = cmd.payload.get("legacy_task_id").and_then(|v| v.as_str());
    let Some(handle) = intent.or(legacy) else { return Ok(()); };
    let identity = intent.and_then(|id| id.strip_prefix("bcs_intent_"));
    if intent.is_some() && !identity.is_some_and(|id| id.len() == 32 && id.bytes().all(|b| b.is_ascii_hexdigit())) {
        return Err(ServiceError::InvalidOperation { message: "invalid assignment intent".into(), request_id: None });
    }
    let mut group = flow.group.get(&cmd.group_id).await.ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
    if group.group_strategy != GroupStrategy::ManagerWorker { return Ok(()); }
    let session = cmd.payload.get("bcs_session_id").and_then(|v| v.as_str()).unwrap_or(&group.id).to_string();
    crate::task_flow::apply_session_participants(flow, &mut group, &cmd.group_id, &session).await?;
    if !group.participants.iter().any(|p| p.bot_uuid == cmd.driver_bot_id && p.role == bcs_domain::ParticipantRole::Manager) {
        return Err(ServiceError::Forbidden("task notice requires current Manager".into()));
    }
    let service = flow.managed_deliveries.as_ref().ok_or_else(|| ServiceError::InternalError("task notice queue unavailable".into()))?;
    let reference = crate::task_presentation::reference(intent, legacy.unwrap_or(""));
    let text = match rejection {
        Some(reason) => format!("[任务派发失败] ref={reference} | Worker：{}\n情况：{}。Worker 未执行本次请求，请修正后再决定是否派发。", crate::task_presentation::single_line(&cmd.target_bot_id, 80), crate::task_presentation::single_line(reason, 200)),
        None if cmd.payload["timeout_unknown"] == true => format!("[任务超时，停止结果未确认] ref={reference}\n无法确认是否停止，旧任务可能仍在执行。请用 bcs_task_status 按需查询，不要自动重派。"),
        None => format!("[任务派发结果未确认] ref={reference}\n无法确认是否已派发，请用 bcs_task_status 按需查询；不要自动重派。"),
    };
    let mut projection = serde_json::to_value(crate::queued_group::QueuedGroupProjection::system(&group, &cmd.driver_bot_id, false)?)?;
    // Pre-admission outcomes need an index; admitted failures already have one.
    if rejection.is_some() && index_rejection { projection["task_rejected_id"] = serde_json::json!(identity.unwrap_or("").to_ascii_lowercase()); }
    else if rejection.is_none() {
        if let Some(identity) = identity { projection["task_uncertain_id"] = serde_json::json!(identity.to_ascii_lowercase()); }
    }
    let now = chrono::Utc::now().timestamp_millis();
    let kind = if rejection.is_some() { "task-rejected" } else if cmd.payload["timeout_unknown"] == true { "task-timeout-unknown" } else { "task-dispatch-unknown" };
    let key = format!("{kind}:{handle}");
    projection["task_control_notice"] = serde_json::json!(true);
    let (visibility_domain, audience) = bcs_domain::system_message_visibility(group.group_strategy,
        SystemMessageEventKind::GenericNotification, Some(&cmd.driver_bot_id));
    service.admit(AdmitMessageDeliveries {
        display_message: None, message_id: key.clone(), event: None,
        message: NewMessage { group_id:group.id, session_id:session, sender_id:"system".into(), sender_type:SenderType::System,
            message_type:"system".into(), content:serde_json::json!({"text":text}), client_msg_id:Some(key),
            owner_bot_id:Some(cmd.driver_bot_id.clone()), visibility_domain, audience, created_at:now as u64, run_id:String::new() },
        flow_kind:bcs_domain::message_delivery::DeliveryFlowKind::System,
        targets:vec![DeliveryAdmissionTarget { rejection:None, target_bot_id:cmd.driver_bot_id.clone(), kind:DeliveryType::Send,
            max_queued:100, semantic_projection_json:projection, run_id:None }],
        now_ms:now, expire_at_ms:None,
    }).await.map_err(|_| ServiceError::InternalError("task notice persistence unavailable".into()))?;
    Ok(())
}

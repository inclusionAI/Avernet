//! Durable task intent. Delivery is authoritative; TaskStore is a recoverable
//! projection, not an additional queue or a prerequisite for terminal commit.
use crate::{BcsMessageFlow, queued_group::QueuedGroupProjection, task_store::{new_task_entry, TaskLedgerStatus}};
use bcs_domain::{DeliveryType, Group, GroupStrategy, NewMessage, SenderType};
use bcs_domain::message_delivery::{DeliveryFlowKind, MessageDeliveryStatus as Status, PersistedMessageDelivery};
use bcs_service_api::{ChatResponseMode, ServiceError, ServiceResult, ParticipantRole, ParticipantMode};
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget, DeliveryLookup};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum TaskLeg { Dispatch, Result, Message }

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct TaskIntent {
    pub leg: TaskLeg,
    pub task_id: String,
    pub manager: String,
    pub worker: String,
    pub worker_name: String,
    pub response_mode: ChatResponseMode,
}

pub(crate) fn error(message: &str) -> ServiceError { ServiceError::InternalError(message.into()) }

pub(crate) fn intent(row: &PersistedMessageDelivery) -> ServiceResult<Option<TaskIntent>> {
    if row.flow_kind != DeliveryFlowKind::Task { return Ok(None); }
    let value = row.semantic_projection_json.get("task").ok_or_else(|| error("queued task intent missing"))?;
    let intent: TaskIntent = serde_json::from_value(value.clone()).map_err(|_| error("queued task intent invalid"))?;
    if intent.task_id.is_empty() || intent.manager.is_empty() || intent.worker.is_empty()
        || row.target_bot_id != if intent.leg == TaskLeg::Dispatch { &intent.worker } else { &intent.manager }.as_str() {
        return Err(error("queued task identity mismatch"));
    }
    Ok(Some(intent))
}

pub(crate) fn authorize(group: &Group, intent: &TaskIntent, sender: &str, target: &str) -> ServiceResult<()> {
    if group.group_strategy != GroupStrategy::ManagerWorker { return Err(error("task queue requires manager_worker")); }
    let expected_sender = if intent.leg == TaskLeg::Dispatch { &intent.manager } else { &intent.worker };
    let expected_target = if intent.leg == TaskLeg::Dispatch { &intent.worker } else { &intent.manager };
    if sender != expected_sender || target != expected_target { return Err(error("queued task participant mismatch")); }
    for (id, role) in [(&intent.manager, ParticipantRole::Manager), (&intent.worker, ParticipantRole::Worker)] {
        let participant = group.participants.iter().find(|p| &p.bot_uuid == id && p.is_bot() && p.role == role)
            .ok_or_else(|| error("queued task participant role changed"))?;
        if participant.mode == Some(ParticipantMode::Muted) { return Err(error("queued task participant is muted")); }
    }
    Ok(())
}

/// Legacy callers must not cross an unfinished lane. Retained System context
/// can promote the next Task Send to a queue carrier after policy disable.
pub(crate) async fn admission_mode(flow: &BcsMessageFlow, group: &Group, session: &str, target: &str) -> ServiceResult<Option<bool>> {
    if group.group_strategy != GroupStrategy::ManagerWorker { return Ok(None); }
    let enabled = match &flow.delivery_policy {
        Some(live) => live.snapshot.read().await.policy.manages_task(target),
        None => false,
    };
    if enabled {
        if session == group.id || session.is_empty() { return Err(error("task queue requires an explicit canonical Session")); }
        return Ok(Some(false));
    }
    let Some(service) = &flow.managed_deliveries else { return Ok(None); };
    let scope = if session == group.id { DeliveryLookup::BotPending(target.into()) }
        else { DeliveryLookup::LanePending { bot: target.into(), session: session.into() } };
    if !service.lookup(scope).await.map_err(|_| error("task queue drain lookup failed"))?.is_empty() {
        return Err(ServiceError::InvalidOperation { message: "queue_draining: task target lane must settle before legacy delivery resumes".into(), request_id: None });
    }
    if crate::queued_admission::pending_context_carrier(flow, target, DeliveryType::Send, Some(session)).await? {
        return Ok(Some(true));
    }
    Ok(None)
}

pub(crate) async fn command(flow: &BcsMessageFlow, group: &Group, session: &str,
    task: TaskIntent, text: &str, attachments: Option<&serde_json::Value>, sender_name: &str, drain: bool,
) -> ServiceResult<AdmitMessageDeliveries> {
    let (sender, target) = if task.leg == TaskLeg::Dispatch { (&task.manager, &task.worker) } else { (&task.worker, &task.manager) };
    authorize(group, &task, sender, target)?;
    if task.leg != TaskLeg::Result {
        let sessions = flow.session_management.as_ref().ok_or_else(|| error("task queue session authorization unavailable"))?;
        let current = sessions.get(session).await.map_err(|_| error("task queue session authorization failed"))?
            .ok_or_else(|| error("task queue session missing"))?;
        if current.group_id != group.id || current.status != bcs_service_api::SessionStatus::Running {
            return Err(error("task queue requires a running Session in the same group"));
        }
    }
    let policy = match &flow.delivery_policy { Some(live) => Some(live.snapshot.read().await.clone()), None => None };
    let now = chrono::Utc::now().timestamp_millis();
    let mut content = serde_json::json!({"text":text});
    if let Some(value) = attachments {
        let parsed: Vec<bcs_domain::Attachment> = serde_json::from_value(value.clone())
            .map_err(|_| error("invalid task attachments"))?;
        content["attachments"] = serde_json::to_value(parsed)?;
    }
    let owner = (task.leg == TaskLeg::Dispatch).then(|| target.clone());
    let (visibility_domain, audience) = crate::group_flow::persisted_message_visibility(
        Some(group), sender, SenderType::Bot, "chat", owner.as_deref())?;
    let message = NewMessage { group_id:group.id.clone(), session_id:session.into(), sender_id:sender.clone(),
        sender_type:SenderType::Bot, message_type:"chat".into(), content,
        client_msg_id:Some(format!("task-{:?}:{}", task.leg, task.task_id)), owner_bot_id:owner,
        visibility_domain, audience, created_at:now as u64, run_id:String::new() };
    let mut projection = serde_json::to_value(QueuedGroupProjection::task(group, target, sender_name, task.clone())?)?;
    projection["drain_context"] = serde_json::json!(drain);
    if let Some(policy) = &policy { projection["policy_version"] = serde_json::json!(policy.version); }
    let message_id = uuid::Uuid::new_v4().to_string();
    let event = crate::queued_admission::prepare_message_event(flow, &message_id, &message)?;
    Ok(AdmitMessageDeliveries { display_message:None, message_id, message, flow_kind:DeliveryFlowKind::Task,
        targets:vec![DeliveryAdmissionTarget { rejection:None, target_bot_id:target.clone(), kind:DeliveryType::Send,
            max_queued:policy.as_ref().map_or(100, |p| p.policy.bot(target).max_queued), semantic_projection_json:projection }],
        now_ms:now, expire_at_ms:policy.as_ref().and_then(|p| p.policy.queue_ttl_ms).map(|ttl| now.saturating_add(ttl as i64)), event })
}

pub(crate) async fn admit(flow: &BcsMessageFlow, command: AdmitMessageDeliveries) -> ServiceResult<PersistedMessageDelivery> {
    let service = flow.managed_deliveries.as_ref().ok_or_else(|| error("task queue unavailable"))?;
    let admitted = service.admit(command).await.map_err(|e| error(&format!("task queue admission failed: {e}")))?;
    let row = admitted.deliveries.into_iter().next().ok_or_else(|| error("task queue target missing"))?;
    restore(flow, &row).await?;
    if !matches!(row.state.status, Status::Queued) {
        return Err(ServiceError::InvalidOperation { message:format!("task queue admission rejected: {:?}", row.state.status), request_id:Some(row.delivery_id) });
    }
    Ok(row)
}

/// Rebuild on send-start, callback and scoped ledger reads, including after
/// master takeover. No full-database startup scan or second durable task store.
pub(crate) async fn restore(flow: &BcsMessageFlow, row: &PersistedMessageDelivery) -> ServiceResult<()> {
    let Some(task) = intent(row)? else { return Ok(()); };
    if task.leg != TaskLeg::Dispatch { return Ok(()); }
    let status = match row.state.status {
        Status::Queued => TaskLedgerStatus::Queued,
        Status::Completed => TaskLedgerStatus::Replied,
        Status::Failed | Status::RejectedCapacity | Status::Cancelled => TaskLedgerStatus::Failed,
        Status::Expired => TaskLedgerStatus::TimedOut,
        _ => TaskLedgerStatus::Dispatched, // Unknown/cancelling still owns the task.
    };
    let mut entry = new_task_entry(task.task_id.clone(), row.group_id.clone(), Some(row.session_id.clone()),
        task.manager, task.worker, Some(task.worker_name), row.created_at_ms as u64, task.response_mode);
    entry.status = status;
    entry.managed = true;
    entry.managed_version = row.state.state_version;
    if status == TaskLedgerStatus::Dispatched && row.state.may_have_been_sent
        && task.response_mode == ChatResponseMode::AfterLastToolCall
        && flow.task_store.get(&task.task_id).await.is_none() {
        // Build privately before publishing. Concurrent status notifications
        // must not expose a half-restored response window to a final callback.
        entry = restore_response(flow, row, entry).await?;
    }
    flow.task_store.restore_managed(entry).await;
    if let Some(run) = &row.run_id { flow.task_store.register_alias(run.clone(), task.task_id.clone()).await; }
    Ok(())
}

async fn restore_response(flow: &BcsMessageFlow, row: &PersistedMessageDelivery,
    entry: crate::task_store::TaskEntry,
) -> ServiceResult<crate::task_store::TaskEntry> {
    let task_id = entry.task_id.clone();
    let scratch = crate::task_store::TaskStore::new();
    scratch.register(entry).await;
    let repo = flow.message_repo.as_ref().ok_or_else(|| error("task history unavailable"))?;
    let mut before = None;
    let mut records = Vec::new();
    loop {
        let page = repo.list_session_history(&row.session_id, bcs_domain::MessageOwnerFilter::Any,
            Some(row.source_session_seq + 1), None, before, 200).await?;
        records.extend(page.messages.into_iter().filter(|m| m.sender_id == row.target_bot_id
            && Some(m.run_id.as_str()) == row.run_id.as_deref()));
        if !page.has_more { break; }
        let next = page.next_cursor.ok_or_else(|| error("task history cursor missing"))?;
        if Some(next) == before { return Err(error("task history cursor did not advance")); }
        before = Some(next);
    }
    records.sort_by_key(|m| m.session_seq);
    for record in records {
        if record.message_type == "tool_call" { scratch.record_response_tool_call(&task_id).await; }
        else if record.message_type == "chat" {
            if let Some(text) = record.content.as_str().or_else(|| record.content.get("text").and_then(|v| v.as_str())) {
                scratch.record_response_delta(&task_id, text).await;
            }
        }
    }
    scratch.get(&task_id).await.ok_or_else(|| error("task recovery projection missing"))
}

pub(crate) async fn refresh_session(flow: &BcsMessageFlow, session: Option<&str>) -> ServiceResult<Vec<PersistedMessageDelivery>> {
    let (Some(service), Some(session)) = (&flow.managed_deliveries, session) else { return Ok(Vec::new()); };
    let rows = service.snapshot(Some(session)).await.map_err(|_| error("task ledger refresh failed"))?;
    for row in &rows { restore(flow, row).await?; }
    Ok(rows)
}

pub(crate) async fn refresh_completion_scope(flow: &BcsMessageFlow, group: &str, session: Option<&str>) -> ServiceResult<Vec<PersistedMessageDelivery>> {
    if session.is_some() { return refresh_session(flow, session).await; }
    if flow.managed_deliveries.is_none() { return Ok(Vec::new()); }
    let Some(sessions) = &flow.session_management else { return Ok(Vec::new()); };
    let mut rows = Vec::new();
    let mut offset = 0;
    loop {
        let page = sessions.list_by_group(group, Some(bcs_service_api::SessionStatus::Running), offset, 100, None, None)
            .await.map_err(|_| error("task completion session lookup failed"))?;
        let count = page.len();
        for session in page { rows.extend(refresh_session(flow, Some(&session.id)).await?); }
        if count < 100 { break; }
        offset += count as u64;
    }
    Ok(rows)
}

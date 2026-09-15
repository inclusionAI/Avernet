//! Ephemeral, best-effort IM hints. Durable status queries remain authoritative.
//! No notification replay, outbox, or per-message background task is created.
use crate::BcsMessageFlow;
use bcs_domain::{
    DeliveryType, ParticipantRole,
    message_delivery::{MessageDeliveryStatus as Status, PersistedMessageDelivery},
};
use bcs_service_api::OutboundMessage;
use bcs_service_api::{ChannelOutboundEventKind, ChannelOutboundPurpose, ChannelRenderHint};
use std::{collections::BTreeMap, sync::Weak, time::Duration};

#[derive(Default)]
struct PendingHint {
    rows: BTreeMap<String, PersistedMessageDelivery>,
    sent: Vec<&'static str>,
}

fn hint(row: &PersistedMessageDelivery, now: i64) -> Option<(&'static str, &'static str)> {
    use bcs_domain::message_delivery::DeliveryWaitReason;
    match row.state.status {
        Status::Queued if row.wait_reason == Some(DeliveryWaitReason::BotOffline) => {
            Some(("offline", "Bot 暂时离线，消息已排队"))
        }
        Status::Queued if now.saturating_sub(row.created_at_ms) >= 2_000 => {
            Some(("queued", "消息已排队，正在等待该 Bot 的处理名额"))
        }
        Status::Unknown => Some((
            "unknown",
            "处理状态暂时无法确认，该 Bot 在本会话中的后续请求已暂停",
        )),
        Status::CancelUnknown => Some((
            "cancel_unknown",
            "停止结果暂时无法确认，该 Bot 在本会话中的后续请求已暂停",
        )),
        Status::Cancelling => Some(("cancelling", "正在停止处理")),
        Status::Failed => Some(("failed", "处理失败")),
        Status::Cancelled => Some(("cancelled", "消息已取消")),
        Status::Expired => Some(("expired", "排队消息已过期")),
        Status::RejectedCapacity => {
            Some(("rejected_capacity", "该 Bot 队列已满，本次请求未被接收"))
        }
        _ => None,
    }
}

fn terminal(row: &PersistedMessageDelivery) -> bool {
    matches!(
        row.state.status,
        Status::Completed
            | Status::Failed
            | Status::Cancelled
            | Status::Expired
            | Status::RejectedCapacity
    )
}

pub async fn run(
    flow: Weak<BcsMessageFlow>,
    mut changes: tokio::sync::broadcast::Receiver<Vec<PersistedMessageDelivery>>,
    mut shutdown: tokio::sync::watch::Receiver<bool>,
) {
    let mut pending: BTreeMap<String, PendingHint> = BTreeMap::new();
    let mut tick = tokio::time::interval(Duration::from_millis(100));
    loop {
        tokio::select! {
            biased;
            changed = shutdown.changed() => if changed.is_err() || *shutdown.borrow() { return; },
            changed = changes.recv() => match changed {
                Ok(rows) => for row in rows {
                    let Some(flow) = flow.upgrade() else { return; };
                    if !matches!(tokio::time::timeout(Duration::from_secs(2), publish_status(&flow, &row)).await, Ok(Ok(()))) {
                        tracing::warn!("delivery state event failed; status API remains authoritative");
                    }
                    if row.state.kind != DeliveryType::Send { continue; }
                    let entry = pending.entry(row.source_message_id.clone()).or_default();
                    if entry.rows.get(&row.delivery_id).is_none_or(|old| old.state.state_version < row.state.state_version) {
                        entry.rows.insert(row.delivery_id.clone(), row);
                    }
                },
                Err(tokio::sync::broadcast::error::RecvError::Closed) => return,
                Err(tokio::sync::broadcast::error::RecvError::Lagged(count)) => {
                    tracing::warn!(count, "delivery IM hints dropped; status API remains authoritative");
                }
            },
            _ = tick.tick() => {
                let Some(flow) = flow.upgrade() else { return; };
                let now = chrono::Utc::now().timestamp_millis();
                for entry in pending.values_mut() {
                    let mut grouped: BTreeMap<&'static str, (&'static str, Vec<&PersistedMessageDelivery>)> = BTreeMap::new();
                    for row in entry.rows.values() {
                        if let Some((key, text)) = hint(row, now) {
                            if !entry.sent.contains(&key) {
                                grouped.entry(key).or_insert_with(|| (text, Vec::new())).1.push(row);
                            }
                        }
                    }
                    if grouped.is_empty() { continue; }
                    // One aggregated hint per source message and notification batch.
                    let rows: Vec<_> = grouped.values().flat_map(|(_, rows)| rows.iter().copied()).collect();
                    let text = grouped.values().map(|(text, rows)| format!("{}：{}", rows.iter().map(|r| r.target_bot_id.as_str()).collect::<Vec<_>>().join("、"), text)).collect::<Vec<_>>().join("\n");
                    let keys: Vec<_> = grouped.keys().copied().collect();
                    let result = tokio::time::timeout(Duration::from_secs(2), publish(&flow, &rows, text)).await;
                    if !matches!(result, Ok(Ok(()))) { tracing::warn!("delivery IM hint failed; not replaying an ambiguous external write"); }
                    entry.sent.extend(keys);
                }
                pending.retain(|_, entry| !entry.rows.values().all(terminal));
            }
        }
    }
}

async fn publish_status(
    flow: &BcsMessageFlow,
    row: &PersistedMessageDelivery,
) -> bcs_service_api::ServiceResult<()> {
    use bcs_service_api::{
        FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryTarget, ServiceError,
    };
    let (Some(repository), Some(sessions)) = (&flow.message_repo, &flow.session_management) else {
        return Ok(());
    };
    let message = repository
        .get_message_by_id(&row.session_id, &row.source_message_id)
        .await
        .map_err(|_| ServiceError::InternalError("state event source lookup failed".into()))?;
    let session = sessions
        .get(&row.session_id)
        .await
        .map_err(|_| ServiceError::InternalError("state event access lookup failed".into()))?;
    let (Some(message), Some(session)) = (message, session) else {
        return Ok(());
    };
    if session.group_id != row.group_id || message.group_id != row.group_id {
        return Ok(());
    }
    let actor_ids = session
        .participants
        .iter()
        .filter(|p| {
            message
                .owner_bot_id
                .as_ref()
                .is_none_or(|owner| owner == &p.bot_uuid)
                && session
                    .participant_join_seq
                    .as_ref()
                    .and_then(|v| v.get(&p.bot_uuid))
                    .and_then(|v| v.as_i64())
                    .is_none_or(|joined| message.session_seq >= joined)
        })
        .map(|p| p.bot_uuid.clone())
        .collect();
    flow.frontend_delivery.publish(FrontendDeliveryCommand {
        target: FrontendDeliveryTarget::SessionActors { session_id: row.session_id.clone(), actor_ids },
        event_json: serde_json::to_string(&serde_json::json!({
            "type":"event", "event":"message.delivery.updated", "group_id":row.group_id,
            "session_id":row.session_id, "payload":bcs_service_api::application::message_delivery::DeliveryStatusView::from(row),
        }))?, delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
        run_fallback: None, exclude_conn_id: None,
        visibility_domain: message.visibility_domain.unwrap_or(bcs_domain::MessageVisibilityDomain::ManagerWorker),
        audience: message.audience.clone(),
    }).await?;
    Ok(())
}

async fn publish(
    flow: &BcsMessageFlow,
    rows: &[&PersistedMessageDelivery],
    text: String,
) -> bcs_service_api::ServiceResult<()> {
    let (Some(channel), Some(repository), Some(row)) =
        (flow.channel.get(), flow.message_repo.as_ref(), rows.first())
    else {
        return Ok(());
    };
    let message = repository
        .get_message_by_id(&row.session_id, &row.source_message_id)
        .await
        .map_err(|_| {
            bcs_service_api::ServiceError::InternalError("delivery source lookup failed".into())
        })?;
    let Some(message) = message else {
        return Ok(());
    };
    // A System channel event bypasses participant-role visibility. Never use
    // it to disclose a private message or a non-IM caller's request.
    if message.owner_bot_id.is_some() {
        return Ok(());
    }
    let Some(source) = message
        .content
        .get("source_im_message_id")
        .and_then(|v| v.as_str())
    else {
        return Ok(());
    };
    channel.try_outbound(OutboundMessage {
        group_id: row.group_id.clone(), bcs_session_id: row.session_id.clone(),
        run_id: row.run_id.clone().unwrap_or_default(), sender_actor_id: row.target_bot_id.clone(),
        sender_role: ParticipantRole::Driver, sender_label: "BCS".into(),
        kind: ChannelOutboundEventKind::System, purpose: ChannelOutboundPurpose::Conversation,
        text: Some(text), raw_payload: serde_json::json!({"state":"delivery_status", "message_id":row.source_message_id}),
        render_hint: ChannelRenderHint::Render, source_im_message_id: Some(source.into()), source_is_channel: false,
    }).await.map_err(|_| bcs_service_api::ServiceError::InternalError("delivery IM notification failed".into()))
}

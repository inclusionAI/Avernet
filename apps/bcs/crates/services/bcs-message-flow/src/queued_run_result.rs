//! Private abnormal StateMachine results reuse the ordinary durable System Send.
use crate::BcsMessageFlow;
use bcs_domain::{ActorKind, DeliveryType, MessageAudience, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_domain::message_delivery::DeliveryFlowKind;
use bcs_service_api::{ServiceError, ServiceResult, StateMachineResultPublishCommand, SessionKind, SessionStatus};
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget};

pub(crate) async fn prepare(flow: &BcsMessageFlow, c: StateMachineResultPublishCommand) -> ServiceResult<Option<AdmitMessageDeliveries>> {
    let service = flow.session_management.as_ref().ok_or_else(|| ServiceError::InternalError("Run result Session service unavailable".into()))?;
    let Some(session) = service.get(&c.session_id).await.map_err(|e| ServiceError::InternalError(e.to_string()))? else { return Ok(None); };
    if session.group_id != c.group_id || session.session_kind != SessionKind::Chat || session.status != SessionStatus::Running { return Ok(None); }
    let Some(mut group) = flow.group.get(&c.group_id).await else { return Ok(None); };
    if group.status == bcs_domain::GroupStatus::Closed { return Ok(None); }
    group.participants = session.participants;
    if !group.participants.iter().any(|p| p.bot_uuid == c.sender_bot_id && p.actor_kind == ActorKind::Bot)
        || flow.registry.get(&c.sender_bot_id).await.is_none() { return Ok(None); }
    let mut projection = serde_json::to_value(crate::queued_group::QueuedGroupProjection::system(&group, &c.sender_bot_id, false)?)?;
    // Drain semantics allow this terminal receipt even if ordinary admission is
    // disabled. Capacity rejection remains durable and does not imply a wakeup.
    projection["drain_context"] = serde_json::json!(true);
    projection["state_machine_run_result"] = serde_json::json!(c.run_id);
    let policy = match &flow.delivery_policy { Some(p) => p.snapshot.read().await.policy.clone(), None => Default::default() };
    let id = format!("run-result:{}", c.run_id);
    Ok(Some(AdmitMessageDeliveries {
        display_message: None, message_id: id.clone(), event: None,
        message: NewMessage { group_id: c.group_id, session_id: c.session_id,
            sender_id: "system".into(), sender_type: SenderType::System, message_type: "system".into(),
            content: serde_json::json!({"text":c.content}), client_msg_id: Some(id), owner_bot_id: Some(c.sender_bot_id.clone()),
            visibility_domain: MessageVisibilityDomain::ManagerWorker,
            audience: Some(MessageAudience::directed([c.sender_bot_id.clone()]).map_err(|e| ServiceError::InternalError(e.into()))?),
            created_at: c.created_at_ms, run_id: c.run_id },
        flow_kind: DeliveryFlowKind::System,
        targets: vec![DeliveryAdmissionTarget { rejection: None, target_bot_id: c.sender_bot_id.clone(), kind: DeliveryType::Send,
            max_queued: policy.bot(&c.sender_bot_id).max_queued, semantic_projection_json: projection, run_id: None }],
        now_ms: c.created_at_ms as i64, expire_at_ms: None,
    }))
}

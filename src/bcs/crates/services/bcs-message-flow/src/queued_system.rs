//! System messages use the same canonical admission and Bot/session lanes as
//! group messages. The weak owner is bound by composition after runtime wiring.
use std::sync::{Arc, OnceLock, Weak};
use bcs_domain::{DeliveryType, Group, GroupStrategy, MessageAudience, MessageVisibilityDomain,
    NewMessage, Participant, PersistMode, SenderType, SystemGroupMessage, SystemMessageEventKind};
use bcs_domain::message_delivery::{DeliveryFlowKind, MessageDeliveryStatus};
use bcs_service_api::{SystemMessageQueueService, SystemQueueAdmissionOutcome};
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget};
use bcs_service_api::{ServiceError, ServiceResult, SystemMessageRecipientResult};
use crate::{BcsMessageFlow, queued_group::QueuedGroupProjection};

#[derive(Default)]
pub struct QueuedSystemAdmission {
    owner: OnceLock<Weak<BcsMessageFlow>>,
}

impl QueuedSystemAdmission {
    pub(crate) fn bind(&self, flow: &Arc<BcsMessageFlow>) {
        let _ = self.owner.set(Arc::downgrade(flow));
    }
}

fn invalid(message: &str) -> ServiceError { ServiceError::InternalError(message.into()) }

#[async_trait::async_trait]
impl SystemMessageQueueService for QueuedSystemAdmission {
    async fn admit(&self, group: &Group, session_id: &str, participants: &[Participant],
        kind: SystemMessageEventKind, messages: &[SystemGroupMessage],
    ) -> ServiceResult<Option<SystemQueueAdmissionOutcome>> {
        let flow = self.owner.get().and_then(Weak::upgrade)
            .ok_or_else(|| invalid("system queue owner unavailable"))?;
        if group.group_strategy == GroupStrategy::StateMachine { return Ok(None); }
        let policy = match &flow.delivery_policy {
            Some(live) => Some(live.snapshot.read().await.clone()), None => None,
        };
        let manages = |bot: &str| participants.iter().any(|p| p.bot_uuid == bot && p.is_bot())
            && policy.as_ref().map_or_else(|| flow.group_delivery_limits.contains_key(bot), |p| p.policy.manages_system(bot));
        let targets: Vec<_> = messages.iter().flat_map(|m| m.recipients.iter().map(|bot| bcs_service_api::RoutingTarget {
            bot_uuid: bot.clone(), url: String::new(), is_driver: false, delivery_type: m.delivery_type,
        })).collect();
        crate::queued_admission::guard_legacy_targets(&flow, &targets, Some(session_id)).await?;
        let mut drains = std::collections::BTreeSet::new();
        for target in &targets {
            if !manages(&target.bot_uuid) && crate::queued_admission::pending_context_carrier(
                &flow, &target.bot_uuid, target.delivery_type, Some(session_id)).await? {
                drains.insert(target.bot_uuid.clone());
            }
        }
        if drains.is_empty() && !messages.iter().flat_map(|m| &m.recipients).any(|bot| manages(bot)) { return Ok(None); }
        if session_id.is_empty() { return Err(invalid("system queue requires a canonical Session")); }
        let service = flow.managed_deliveries.as_ref().ok_or_else(|| invalid("system queue service unavailable"))?;
        let mut scope = group.clone();
        scope.participants = participants.to_vec();
        let now_ms = chrono::Utc::now().timestamp_millis();
        let expiry = policy.as_ref().map_or(flow.delivery_queue_ttl_ms, |p| p.policy.queue_ttl_ms.map(|n| n as i64))
            .map(|ttl| now_ms.saturating_add(ttl));
        // Each tuple owns a canonical source and its producer-indexed targets.
        let mut records: Vec<(NewMessage, Vec<(usize, String, DeliveryType)>)> = Vec::new();
        let mut public = Vec::new();
        for (index, message) in messages.iter().enumerate() {
            match message.persist {
                PersistMode::Public => {
                    public.push((message.message.as_str(), records.len()));
                    records.push((new_message(group, session_id, kind, &message.message, None, now_ms),
                        message.recipients.iter().map(|id| (index, id.clone(), message.delivery_type)).collect()));
                }
                PersistMode::PerRecipient => {
                    for bot in &message.recipients {
                        records.push((new_message(group, session_id, kind, &message.message, Some(bot.clone()), now_ms),
                            vec![(index, bot.clone(), message.delivery_type)]));
                    }
                }
                PersistMode::Skip => {}
            }
        }
        for (index, message) in messages.iter().enumerate().filter(|(_, m)| m.persist == PersistMode::Skip) {
            if message.recipients.is_empty() { continue; }
            let matches: Vec<_> = public.iter().filter(|(body, _)| *body == message.message).collect();
            if matches.len() != 1 { return Err(invalid("system Skip delivery requires one matching public source message")); }
            records[matches[0].1].1.extend(message.recipients.iter().map(|id| (index, id.clone(), message.delivery_type)));
        }
        let mut commands = Vec::new();
        let mut origins = Vec::new();
        for (message, recipients) in records {
            let mut targets = Vec::new();
            let mut origin = Vec::new();
            for (index, bot, delivery_type) in recipients {
                let drain = !manages(&bot) && delivery_type == DeliveryType::Send && drains.contains(&bot);
                if !manages(&bot) && !drain { continue; }
                let required = (kind == SystemMessageEventKind::SessionContext
                    || (kind == SystemMessageEventKind::BotJoined && message.owner_bot_id.is_some()))
                    && delivery_type == DeliveryType::Inject;
                let mut projection = serde_json::to_value(QueuedGroupProjection::system(&scope, &bot, required)?)?;
                projection["drain_context"] = serde_json::json!(drain);
                if let Some(policy) = &policy { projection["policy_version"] = serde_json::json!(policy.version); }
                let limit = policy.as_ref().map_or_else(|| flow.group_delivery_limits.get(&bot).copied().unwrap_or(100), |p| p.policy.bot(&bot).max_queued);
                targets.push(DeliveryAdmissionTarget { rejection: None, target_bot_id: bot.clone(), kind: delivery_type,
                    max_queued: limit, semantic_projection_json: projection });
                origin.push((index, bot));
            }
            let message_id = uuid::Uuid::new_v4().to_string();
            let event = crate::queued_admission::prepare_message_event(&flow, &message_id, &message)?;
            commands.push(AdmitMessageDeliveries { display_message: None, message_id, message,
                flow_kind: DeliveryFlowKind::System, targets, now_ms,
                expire_at_ms: expiry, event });
            origins.push(origin);
        }
        let admitted = service.admit_batch(commands).await.map_err(|_| invalid("system queue admission persistence failed"))?;
        let mut recipients = Vec::new();
        for (result, origins) in admitted.into_iter().zip(origins) {
            for delivery in result.deliveries {
                let index = origins.iter().find(|(_, bot)| *bot == delivery.target_bot_id)
                    .map(|(i, _)| *i).ok_or_else(|| invalid("system queue admission target mismatch"))?;
                let accepted = matches!(delivery.state.status, MessageDeliveryStatus::Queued | MessageDeliveryStatus::PendingContext | MessageDeliveryStatus::Bound);
                recipients.push((index, SystemMessageRecipientResult {
                    recipient_id: delivery.target_bot_id, run_id: delivery.run_id.unwrap_or_default(),
                    delivery_type: delivery.state.kind, delivery_id: Some(delivery.delivery_id), delivered: false,
                    error: (!accepted).then(|| invalid("system queue target rejected")),
                }));
            }
        }
        Ok(Some(SystemQueueAdmissionOutcome { recipients }))
    }
}

fn new_message(group: &Group, session: &str, kind: SystemMessageEventKind, text: &str,
    owner_bot_id: Option<String>, now_ms: i64,
) -> NewMessage {
    let visibility_domain = match group.group_strategy {
        GroupStrategy::Chat => MessageVisibilityDomain::Chat,
        GroupStrategy::ManagerWorker => MessageVisibilityDomain::ManagerWorker,
        GroupStrategy::StateMachine => MessageVisibilityDomain::StateMachine,
    };
    let audience = if visibility_domain == MessageVisibilityDomain::Chat { None }
        else if kind == SystemMessageEventKind::SessionContext { Some(MessageAudience::FullOnly) }
        else { Some(match &owner_bot_id {
            Some(id) => MessageAudience::Directed { actor_ids: vec![id.clone()] },
            None => MessageAudience::Public,
        }) };
    NewMessage { group_id: group.id.clone(), session_id: session.into(), sender_id: "system".into(),
        sender_type: SenderType::System, message_type: "system".into(), content: serde_json::json!({"text":text}),
        client_msg_id: None, owner_bot_id, created_at: now_ms as u64, run_id: String::new(),
        visibility_domain, audience }
}

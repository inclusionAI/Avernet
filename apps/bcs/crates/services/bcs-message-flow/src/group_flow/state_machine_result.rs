//! One-shot results share Group routing/lane policy, with producer-owned history.
use super::*;
use bcs_domain::{PersistedMessage, state_machine_history::physical_message_id};
use bcs_service_api::{BotActor, StateMachineResultPublishCommand};
use bcs_service_api::port::repo::message_delivery::DeliveryAdmissionResult;
use bcs_domain::message_delivery::MessageDeliveryStatus as Status;

pub(super) struct ResultMessage {
    pub id: String,
    pub message: NewMessage,
}

impl ResultMessage {
    pub fn validate(&self, saved: &PersistedMessage) -> ServiceResult<()> {
        let wanted = &self.message;
        if saved.group_id != wanted.group_id || saved.session_id != wanted.session_id
            || saved.run_id != wanted.run_id || saved.sender_id != wanted.sender_id
            || saved.sender_type != wanted.sender_type || saved.message_type != wanted.message_type
            || saved.content != wanted.content || saved.client_msg_id != wanted.client_msg_id
            || saved.created_at != wanted.created_at || saved.owner_bot_id != wanted.owner_bot_id
            || saved.visibility_domain != Some(wanted.visibility_domain) || saved.audience != wanted.audience {
            return Err(ServiceError::Conflict("Chat result history conflicts with saved publication".into()));
        }
        Ok(())
    }

    pub async fn persist(&self, flow: &BcsMessageFlow) -> ServiceResult<()> {
        let repo = flow.message_repo.as_ref().ok_or_else(||
            ServiceError::InternalError("state-machine result history repository missing".into()))?;
        let event = crate::queued_admission::prepare_message_event(flow, &self.id, &self.message)?;
        let saved = match event {
            Some(event) => repo.append_message_with_event(AppendMessageWithEvent {
                message_id: self.id.clone(), message: self.message.clone(), event,
            }).await,
            None => repo.append_message_with_id(self.id.clone(), self.message.clone()).await,
        }.map_err(|e| ServiceError::InternalError(format!("persist state-machine result: {e}")))?;
        self.validate(&saved)
    }

    pub fn validate_admission(&self, result: &DeliveryAdmissionResult, decision: &RoutingDecision) -> ServiceResult<()> {
        self.validate(&result.message)?;
        if result.deliveries.is_empty() {
            return Err(ServiceError::Conflict("Chat result admission has no deliveries".into()));
        }
        if result.deliveries.iter().any(|row| matches!(row.state.status,
            Status::Failed | Status::RejectedCapacity | Status::Cancelled | Status::Expired
                | Status::DiscardedContext | Status::Cancelling | Status::CancelUnknown | Status::Unknown)) {
            return Err(ServiceError::InvalidOperation {
                message: "Chat result delivery was rejected, failed or is uncertain".into(), request_id: None,
            });
        }
        // A queued duplicate proves only its durable recipients. There is no
        // legacy delivery ledger from which to reconstruct a partial send.
        if result.duplicate && decision.targets.iter().any(|target|
            !result.deliveries.iter().any(|row| row.target_bot_id == target.bot_uuid)) {
            return Err(ServiceError::Conflict("Cannot replay an ambiguous mixed Chat result publication".into()));
        }
        Ok(())
    }
}

pub(super) async fn publish(flow: &BcsMessageFlow, cmd: StateMachineResultPublishCommand) -> ServiceResult<()> {
    let key = format!("state-machine-result:{}", cmd.run_id);
    let result = ResultMessage {
        id: physical_message_id(&key),
        message: NewMessage {
            group_id: cmd.group_id.clone(), session_id: cmd.session_id.clone(),
            sender_id: cmd.sender_bot_id.clone(), sender_type: SenderType::Bot,
            message_type: "chat".into(), content: Value::String(cmd.content.clone()),
            client_msg_id: Some(key.clone()), owner_bot_id: None, created_at: cmd.created_at_ms,
            run_id: cmd.run_id, visibility_domain: MessageVisibilityDomain::StateMachine,
            audience: Some(MessageAudience::Public),
        },
    };
    let outcome = web_send::handle_web_send_with_result(flow, WebSendCommand {
        caller_execution: None,
        caller: CallerContext::Bot(BotActor { bot_uuid: cmd.sender_bot_id.clone() }),
        group_id: cmd.group_id, session_id: Some(cmd.session_id), from_actor_id: cmd.sender_bot_id,
        from_name: None, message: cmd.content, mentions: Vec::new(), attachments: None,
        thinking: None, idempotency_key: Some(key), source_im_message_id: None,
        channel_sender_identity: None, sender_conn_id: None, provider_bypass_headers: Vec::new(),
    }, Some(&result)).await?;
    if outcome.bot_deliveries.iter().any(|target| !target.delivered || target.error.is_some()) {
        return Err(ServiceError::InternalError("Chat result legacy delivery was not accepted".into()));
    }
    Ok(())
}

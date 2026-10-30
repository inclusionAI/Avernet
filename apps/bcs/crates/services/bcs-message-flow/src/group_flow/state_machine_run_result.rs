//! Bot-only RunResult for the manager of a failed or aborted Run. It reuses the
//! one-shot result routing (queue admission for managed targets, synchronous
//! delivery otherwise) with three differences: the manager is the only target,
//! nothing is published to the Workbench, and no message Event is recorded.
use super::*;
use bcs_domain::{RUN_RESULT_MESSAGE_TYPE, state_machine_history::physical_message_id};
use bcs_service_api::{BotActor, StateMachineRunResultOutcome as Outcome, StateMachineRunResultPayload as Payload};

fn skipped(reason: &str) -> Outcome {
    Outcome::Skipped { reason: reason.to_string() }
}

/// Boundary checks shared with TaskResult: the Session is still Running in the
/// same Group, the Group is Active and the manager is still a Session Bot.
async fn boundary(flow: &BcsMessageFlow, payload: &Payload) -> Result<(), Outcome> {
    let sessions = flow.session_management.as_ref()
        .ok_or_else(|| Outcome::Retry { error: "RunResult session service unavailable".into() })?;
    let session = match sessions.get(&payload.session_id).await {
        Ok(Some(session)) => session,
        Ok(None) => return Err(skipped("session no longer exists")),
        Err(error) => return Err(Outcome::Retry { error: format!("RunResult session read failed: {error}") }),
    };
    if session.group_id != payload.group_id {
        return Err(skipped("session belongs to another group"));
    }
    if session.status != SessionStatus::Running {
        return Err(skipped("session is not running"));
    }
    if !session.participants.iter().any(|p| p.is_bot() && p.bot_uuid == payload.target_bot_id) {
        return Err(skipped("manager is no longer a session bot"));
    }
    match flow.group.get(&payload.group_id).await {
        Some(group) if group.status == GroupStatus::Active => Ok(()),
        Some(_) => Err(skipped("group is not active")),
        None => Err(skipped("group no longer exists")),
    }
}

pub(super) fn result_message(payload: &Payload) -> state_machine_result::ResultMessage {
    let key = format!("run-result:{}", payload.run_id);
    state_machine_result::ResultMessage {
        id: physical_message_id(&key),
        message: NewMessage {
            group_id: payload.group_id.clone(),
            session_id: payload.session_id.clone(),
            sender_id: payload.target_bot_id.clone(),
            sender_type: SenderType::Bot,
            message_type: RUN_RESULT_MESSAGE_TYPE.into(),
            content: Value::String(payload.render_text()),
            client_msg_id: Some(key),
            owner_bot_id: None,
            created_at: payload.terminated_at_ms,
            run_id: payload.run_id.clone(),
            visibility_domain: MessageVisibilityDomain::StateMachine,
            audience: Some(MessageAudience::FullOnly),
        },
    }
}

pub(super) async fn publish(flow: &BcsMessageFlow, payload: Payload) -> ServiceResult<Outcome> {
    if let Err(outcome) = boundary(flow, &payload).await {
        return Ok(outcome);
    }
    let result = result_message(&payload);
    let manager = payload.target_bot_id.clone();
    let sent = web_send::handle_web_send_with_result(flow, WebSendCommand {
        caller_execution: None,
        caller: CallerContext::Bot(BotActor { bot_uuid: manager.clone() }),
        group_id: payload.group_id.clone(), session_id: Some(payload.session_id.clone()),
        from_actor_id: manager.clone(), from_name: None,
        message: payload.render_text(), mentions: Vec::new(), attachments: None, thinking: None,
        idempotency_key: result.message.client_msg_id.clone(), source_im_message_id: None,
        channel_sender_identity: None, sender_conn_id: None, provider_bypass_headers: Vec::new(),
    }, Some(&result)).await;
    match sent {
        // Durable queue admission; validated against empty or rejected deliveries.
        Ok(outcome) if outcome.queue_admission.is_some() => Ok(Outcome::Delivered),
        Ok(outcome) if outcome.bot_deliveries.iter()
            .any(|target| target.target_bot_id == manager && target.delivered && target.error.is_none()) => Ok(Outcome::Delivered),
        // A legacy delivery that was not accepted never reached the manager.
        Ok(outcome) => Ok(Outcome::Retry { error: outcome.bot_deliveries.iter()
            .find_map(|target| target.error.as_ref().map(ToString::to_string)).unwrap_or_else(|| "manager did not accept the RunResult".into()) }),
        // Rejected before admission or delivery, for example a draining lane.
        Err(ServiceError::InvalidOperation { message, .. }) => Ok(Outcome::Retry { error: message }),
        Err(error) => Ok(Outcome::Uncertain { error: error.to_string() }),
    }
}

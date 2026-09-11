//! Bridge the existing scoped chat.abort API to durable delivery ownership.
use crate::{BcsMessageFlow, queued_group::QueuedTransportContext};
use bcs_domain::{
    DeliveryType,
    message_delivery::{MessageDeliveryStatus as Status, PersistedMessageDelivery},
};
use bcs_service_api::DeliveryTransitionCommand;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::{
    ActiveBotRunContext, BotRunScope, BotRunTransportOwner, ChatAbortCommand, ChatAbortFailure,
    ServiceError, ServiceResult,
};
use std::collections::BTreeMap;

pub struct AbortSelection {
    pub owned: BTreeMap<String, PersistedMessageDelivery>,
    pub failures: Vec<ChatAbortFailure>,
}

pub async fn select(
    flow: &BcsMessageFlow,
    command: &ChatAbortCommand,
    active: &mut Vec<ActiveBotRunContext>,
) -> ServiceResult<AbortSelection> {
    let mut selection = AbortSelection {
        owned: BTreeMap::new(),
        failures: Vec::new(),
    };
    let Some(service) = &flow.managed_deliveries else {
        return Ok(selection);
    };
    let rows = service
        .lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Lane { bot: command.bot_id.clone(), session: command.session_id.clone() })
        .await
        .map_err(|_| ServiceError::InternalError("abort delivery lookup failed".into()))?;
    let rows: Vec<_> = rows
        .into_iter()
        .filter(|r| {
            r.target_bot_id == command.bot_id
                && r.group_id == command.group_id
                && r.state.kind == DeliveryType::Send
                && matches!(
                    r.state.status,
                    Status::Dispatching
                        | Status::Running
                        | Status::Unknown
                        | Status::Cancelling
                        | Status::CancelUnknown
                )
        })
        .collect();
    if !rows.is_empty()
        && command.run_id.is_some()
        && active.iter().any(|context| {
            matches!(
                context.transport_owner,
                BotRunTransportOwner::HttpProvider { .. }
            ) && command.run_id.as_ref().is_some_and(|id| {
                id == &context.canonical_run_id || id == &context.downstream_run_id
            })
        })
    {
        return Err(ServiceError::InvalidOperation {
            message: "exact_abort_not_supported: Provider scope also contains managed work".into(),
            request_id: command.run_id.clone(),
        });
    }
    // Managed runs must never fall through to an unpinned, non-durable abort,
    // even if their original in-memory context has expired.
    active.retain(|context| {
        !rows
            .iter()
            .any(|row| row.run_id.as_ref() == Some(&context.canonical_run_id))
    });
    for row in rows {
        let metadata: QueuedTransportContext =
            serde_json::from_value(row.transport_context_json.clone().ok_or_else(|| {
                ServiceError::InternalError("abort original transport missing".into())
            })?)?;
        let run_id = row
            .run_id
            .clone()
            .ok_or_else(|| ServiceError::InternalError("abort logical run missing".into()))?;
        let downstream = metadata
            .downstream_run_id
            .clone()
            .unwrap_or_else(|| run_id.clone());
        if command.run_id.as_ref().is_some_and(|id| {
            id != &run_id && id != &downstream && Some(id) != row.request_id.as_ref()
        }) {
            continue;
        }
        if command.run_id.is_some()
            && matches!(metadata.owner, BotRunTransportOwner::HttpProvider { .. })
        {
            // Provider abort is scope-wide. Do not turn a run selector into
            // collateral cancellation of unrelated legacy or managed work.
            return Err(ServiceError::InvalidOperation {
                message: "exact_abort_not_supported: use an explicit Provider scope abort".into(),
                request_id: command.run_id.clone(),
            });
        }
        if row.state.status != Status::CancelUnknown && row.abort_request_id.is_some() {
            selection.failures.push(ChatAbortFailure {
                run_id,
                code: "chat_abort_unconfirmed".into(),
                message: "a prior abort is still unconfirmed; no automatic resend".into(),
            });
            continue;
        }
        let now = chrono::Utc::now().timestamp_millis();
        let mut intent = transition(&row, Event::ScopeAbortRequested, now);
        intent.actor_id = match &command.caller {
            bcs_service_api::CallerContext::Human(actor) => Some(actor.actor_id.clone()),
            bcs_service_api::CallerContext::Bot(actor) => Some(actor.bot_uuid.clone()),
            _ => None,
        };
        let started = match service.transition(intent).await {
            Ok(started) => started,
            Err(bcs_service_api::application::message_delivery::ManagedDeliveryError::Repository(bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Storage(_))) => return Err(ServiceError::InternalError("abort start persistence failed".into())),
            Err(_) => {
                selection.failures.push(ChatAbortFailure { run_id, code: "chat_abort_pending".into(), message: "cancellation was accepted and another worker owns its completion".into() });
                continue;
            }
        };
        active.push(ActiveBotRunContext {
            canonical_run_id: run_id.clone(),
            downstream_run_id: downstream,
            downstream_session_key: Some(metadata.downstream_session_key),
            scope: BotRunScope {
                group_id: row.group_id,
                session_id: row.session_id,
                bot_id: row.target_bot_id,
            },
            transport_owner: metadata.owner,
            provider_bypass_headers: metadata.provider_route_headers,
            deadline_ms: now.saturating_add(60_000) as u64,
        });
        selection.owned.insert(run_id, started);
    }
    Ok(selection)
}

fn transition(row: &PersistedMessageDelivery, event: Event, now: i64) -> DeliveryTransitionCommand {
    DeliveryTransitionCommand {
        delivery_id: row.delivery_id.clone(),
        expected_state_version: row.state.state_version,
        event,
        now_ms: now,
        request_id: None,
        actor_id: None,
        reply: None,
        transport_context_json: None,
        deadline_at_ms: Some(now.saturating_add(60_000)),
    }
}

pub async fn finish(
    flow: &BcsMessageFlow,
    row: &PersistedMessageDelivery,
    confirmed: bool,
) -> ServiceResult<bool> {
    let service = flow
        .managed_deliveries
        .as_ref()
        .ok_or_else(|| ServiceError::InternalError("abort service missing".into()))?;
    // ACK/final/runtime timeout may have changed the version while I/O was in
    // flight. Re-read durable state; terminal states remain absorbing.
    for _ in 0..3 {
        let rows = service
            .lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Id(row.delivery_id.clone()))
            .await
            .map_err(|_| ServiceError::InternalError("abort result lookup failed".into()))?;
        let current = rows
            .iter()
            .find(|r| r.delivery_id == row.delivery_id)
            .ok_or_else(|| ServiceError::InternalError("abort delivery missing".into()))?;
        if !matches!(
            current.state.status,
            Status::Cancelling | Status::CancelUnknown
        ) {
            return Ok(current.state.status == Status::Cancelled);
        }
        // A user can explicitly retry an uncertain abort. An older I/O result
        // must not complete or downgrade that newer control attempt.
        if !same_abort_attempt(
            current.abort_request_id.as_deref(),
            row.abort_request_id.as_deref(),
        ) {
            return Ok(false);
        }
        match service.transition(transition(current, if confirmed { Event::Aborted } else { Event::AbortUnconfirmed }, chrono::Utc::now().timestamp_millis())).await {
            Ok(updated) => return Ok(updated.state.status == Status::Cancelled),
            Err(bcs_service_api::application::message_delivery::ManagedDeliveryError::Repository(bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Storage(_))) => return Err(ServiceError::InternalError("abort result persistence failed".into())),
            Err(_) => continue,
        }
    }
    Err(ServiceError::InternalError(
        "abort result changed concurrently; query its durable status".into(),
    ))
}

fn same_abort_attempt(current: Option<&str>, completed: Option<&str>) -> bool {
    matches!((current, completed), (Some(current), Some(completed)) if current == completed)
}

#[cfg(test)]
mod tests {
    use super::same_abort_attempt;

    #[test]
    fn late_abort_result_cannot_settle_a_newer_attempt() {
        assert!(same_abort_attempt(Some("abort-1"), Some("abort-1")));
        assert!(!same_abort_attempt(Some("abort-2"), Some("abort-1")));
        assert!(!same_abort_attempt(None, Some("abort-1")));
        assert!(!same_abort_attempt(Some("abort-1"), None));
        assert!(!same_abort_attempt(None, None));
    }
}

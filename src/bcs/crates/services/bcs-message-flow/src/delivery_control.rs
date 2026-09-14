//! Authorized public projections and per-target cancellation.
use crate::BcsMessageFlow;
use bcs_domain::{PersistedMessage, Session};
use bcs_service_api::{CallerContext, ServiceError, ServiceResult};
use bcs_service_api::{
    CancelMessageDeliveryCommand, CancelMessageDeliveryResult, DeliveryStatusQuery,
    DeliveryStatusView, DeliveryTransitionCommand, ManagedDeliveryError,
};

async fn authorize_session(
    flow: &BcsMessageFlow,
    caller: &CallerContext,
    id: &str,
) -> ServiceResult<(Session, String)> {
    let actor = match caller {
        CallerContext::Human(human) => human.actor_id.clone(),
        CallerContext::Bot(bot) => bot.bot_uuid.clone(),
        _ => {
            return Err(ServiceError::Unauthorized(
                "an authenticated session participant is required".into(),
            ));
        }
    };
    let session = flow
        .session_management
        .as_ref()
        .ok_or_else(|| ServiceError::InternalError("session access service unavailable".into()))?
        .get(id)
        .await
        .map_err(|_| ServiceError::InternalError("session access lookup failed".into()))?
        .ok_or_else(|| ServiceError::SessionNotFound(id.into()))?;
    if !session.participants.iter().any(|p| p.bot_uuid == actor) {
        return Err(ServiceError::Forbidden(
            "session membership required".into(),
        ));
    }
    Ok((session, actor))
}

async fn visible(
    flow: &BcsMessageFlow,
    caller: &CallerContext,
    session: &Session,
    actor: &str,
    message: &PersistedMessage,
) -> ServiceResult<()> {
    if message.session_id != session.id || message.group_id != session.group_id {
        return Err(ServiceError::Forbidden(
            "message does not belong to this session".into(),
        ));
    }
    if session
        .participant_join_seq
        .as_ref()
        .and_then(|v| v.get(actor))
        .and_then(|v| v.as_i64())
        .is_some_and(|joined| message.session_seq < joined)
    {
        return Err(ServiceError::Forbidden(
            "message predates session access".into(),
        ));
    }
    if let Some(owner) = &message.owner_bot_id {
        let allowed = owner == actor
            || match caller {
                CallerContext::Human(human) => {
                    flow.registry.get(owner).await.is_some_and(|bot| {
                        bot.created_by.as_deref() == Some(human.staff_no.as_str())
                    })
                }
                _ => false,
            };
        if !allowed {
            return Err(ServiceError::Forbidden(
                "private message is not visible".into(),
            ));
        }
    }
    if matches!(caller, CallerContext::Human(_)) {
        let participant = session.participants.iter().find(|p| p.bot_uuid == actor)
            .ok_or_else(|| ServiceError::Forbidden("session membership required".into()))?;
        let group = flow.group.try_get(&session.group_id).await?;
        let view = bcs_domain::HumanMessageView {
            actor_id: actor.to_owned(),
            scope: participant.message_view_scope,
            allow_legacy_unclassified_chat: group.is_some_and(|g| g.group_strategy == bcs_domain::GroupStrategy::Chat),
        };
        if !view.allows(message) {
            return Err(ServiceError::Forbidden("message audience is not visible".into()));
        }
    }
    Ok(())
}

pub async fn query(
    flow: &BcsMessageFlow,
    query: DeliveryStatusQuery,
) -> ServiceResult<Vec<DeliveryStatusView>> {
    if query.message_ids.len() > 100
        || query.message_ids.iter().any(String::is_empty)
        || query
            .client_msg_id
            .as_ref()
            .is_some_and(|id| id.trim().is_empty())
        || (query.message_ids.is_empty() == query.client_msg_id.is_none())
    {
        return Err(ServiceError::InvalidOperation {
            message: "supply 1..100 message IDs or a client message ID".into(),
            request_id: None,
        });
    }
    let (session, actor) = authorize_session(flow, &query.caller, &query.session_id).await?;
    let Some(service) = &flow.managed_deliveries else {
        return Ok(Vec::new());
    };
    let repository = flow
        .message_repo
        .as_ref()
        .ok_or_else(|| ServiceError::InternalError("message store unavailable".into()))?;
    let rows = if query.client_msg_id.is_some() {
        service.snapshot(Some(&session.id)).await.map_err(|_| ServiceError::InternalError("delivery status read failed".into()))?
    } else {
        let mut rows = Vec::new();
        for id in &query.message_ids { rows.extend(service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Message(id.clone())).await.map_err(|_| ServiceError::InternalError("delivery status read failed".into()))?.into_iter().filter(|d| d.session_id == session.id)); }
        rows
    };
    let mut checked = std::collections::BTreeMap::new();
    let mut result = Vec::new();
    for row in &rows {
        if query.client_msg_id.is_none() && !query.message_ids.contains(&row.source_message_id) {
            continue;
        }
        if !checked.contains_key(&row.source_message_id) {
            let message = repository
                .get_message_by_id(&session.id, &row.source_message_id)
                .await
                .map_err(|_| {
                    ServiceError::InternalError("message visibility lookup failed".into())
                })?
                .ok_or_else(|| {
                    ServiceError::InternalError("delivery canonical message missing".into())
                })?;
            let selected = query.client_msg_id.as_ref().map_or(true, |client| {
                message.sender_id == actor && message.client_msg_id.as_ref() == Some(client)
            });
            if selected {
                visible(flow, &query.caller, &session, &actor, &message).await?;
            }
            checked.insert(row.source_message_id.clone(), selected);
        }
        if checked[&row.source_message_id] {
            result.push(row.into());
        }
    }
    Ok(result)
}

pub async fn cancel(
    flow: &BcsMessageFlow,
    command: CancelMessageDeliveryCommand,
) -> ServiceResult<Vec<CancelMessageDeliveryResult>> {
    let (session, actor) = authorize_session(flow, &command.caller, &command.session_id).await?;
    let repo = flow
        .message_repo
        .as_ref()
        .ok_or_else(|| ServiceError::InternalError("message store unavailable".into()))?;
    let message = repo
        .get_message_by_id(&session.id, &command.message_id)
        .await
        .map_err(|_| ServiceError::InternalError("message ownership lookup failed".into()))?
        .ok_or_else(|| ServiceError::InvalidOperation {
            message: "message not found".into(),
            request_id: None,
        })?;
    visible(flow, &command.caller, &session, &actor, &message).await?;
    if message.sender_id != actor {
        return Err(ServiceError::Forbidden(
            "only the message sender can cancel its delivery".into(),
        ));
    }
    let service =
        flow.managed_deliveries
            .as_ref()
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: "message is not queue-managed".into(),
                request_id: None,
            })?;
    let rows = service
        .lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Message(command.message_id.clone()))
        .await
        .map_err(|_| ServiceError::InternalError("delivery cancellation lookup failed".into()))?;
    let mut results = Vec::new();
    for row in rows.into_iter().filter(|row| {
        row.source_message_id == command.message_id
            && command
                .delivery_id
                .as_ref()
                .is_none_or(|id| id == &row.delivery_id)
    }) {
        if row.state.may_have_been_sent
            && row
                .transport_context_json
                .as_ref()
                .and_then(|v| v.get("owner"))
                .and_then(|v| v.get("kind"))
                .and_then(|v| v.as_str())
                == Some("http_provider")
        {
            results.push(CancelMessageDeliveryResult {
                delivery: (&row).into(),
                error: Some("exact_abort_not_supported".into()),
            });
            continue;
        }
        let now_ms = chrono::Utc::now().timestamp_millis();
        match service.transition(DeliveryTransitionCommand { delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
            event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CancelRequested, now_ms,
            request_id: None, actor_id: Some(actor.clone()), reply: None, transport_context_json: None,
            deadline_at_ms: Some(now_ms.saturating_add(30_000)) }).await {
            Ok(updated) => results.push(CancelMessageDeliveryResult { delivery: (&updated).into(), error: None }),
            Err(error) => {
                if matches!(error, ManagedDeliveryError::Repository(bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Storage(_))) {
                    return Err(ServiceError::InternalError("delivery cancellation persistence failed".into()));
                }
                results.push(CancelMessageDeliveryResult { delivery: (&row).into(), error: Some("delivery_state_changed".into()) });
            }
        }
    }
    Ok(results)
}

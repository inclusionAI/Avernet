//! Authorized public projections and per-target cancellation.
use crate::BcsMessageFlow;
use bcs_domain::{PersistedMessage, Session};
use bcs_service_api::{CallerContext, ServiceError, ServiceResult};
use bcs_service_api::{
    CancelLatestQueuedMessageCommand, CancelLatestQueuedMessageOutcome,
    CancelMessageDeliveryCommand, CancelMessageDeliveryResult, DeliveryStatusQuery,
    DeliveryStatusView, DeliveryTransitionCommand, ManagedDeliveryError,
    ManagedMessageDeliveryService,
};

pub async fn cancel_latest_queued(
    flow: &BcsMessageFlow,
    command: CancelLatestQueuedMessageCommand,
) -> ServiceResult<CancelLatestQueuedMessageOutcome> {
    use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
    use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
    use bcs_service_api::port::repo::message_delivery::{
        DeliveryLookup, MessageDeliveryRepoError,
    };

    let (session, actor) = authorize_session(flow, &command.caller, &command.session_id).await?;
    if session.group_id != command.group_id {
        return Err(ServiceError::Forbidden(
            "session does not belong to the requested group".into(),
        ));
    }
    let Some(service) = &flow.managed_deliveries else {
        return Ok(CancelLatestQueuedMessageOutcome {
            message_id: None,
            cancelled: Vec::new(),
        });
    };
    let repository = flow
        .message_repo
        .as_ref()
        .ok_or_else(|| ServiceError::InternalError("message store unavailable".into()))?;
    // Reuse the indexed Bot/session lane lookup instead of scanning every
    // queued delivery in the environment. Session membership bounds the
    // number of reads and keeps removed/non-participant targets fail-closed.
    let mut rows = Vec::new();
    for bot_id in session
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
        .map(|participant| participant.bot_uuid.clone())
    {
        rows.extend(
            service
                .lookup(DeliveryLookup::Lane {
                    bot: bot_id,
                    session: session.id.clone(),
                })
                .await
                .map_err(|_| ServiceError::InternalError("queued delivery lookup failed".into()))?,
        );
    }
    rows.sort_by(|a, b| {
        (b.source_session_seq, &b.delivery_id).cmp(&(a.source_session_seq, &a.delivery_id))
    });

    let mut message_ids = Vec::new();
    for row in &rows {
        if row.group_id == session.group_id
            && row.state.kind == bcs_domain::DeliveryType::Send
            && row.state.status == Status::Queued
            && !message_ids.contains(&row.source_message_id)
        {
            message_ids.push(row.source_message_id.clone());
        }
    }
    let messages = repository
        .get_messages_by_ids(&session.id, &message_ids)
        .await
        .map_err(|_| ServiceError::InternalError("queued message lookup failed".into()))?;
    let by_id: std::collections::HashMap<_, _> = messages
        .into_iter()
        .map(|message| (message.message_id.clone(), message))
        .collect();
    let selected = message_ids.into_iter().find_map(|message_id| {
        by_id.get(&message_id).filter(|message| {
            message.sender_id == actor
                && message
                    .content
                    .get("source_im_message_id")
                    .and_then(serde_json::Value::as_str)
                    .is_some_and(|id| !id.is_empty())
        }).map(|message| (message_id, message))
    });
    let Some((message_id, message)) = selected else {
        return Ok(CancelLatestQueuedMessageOutcome {
            message_id: None,
            cancelled: Vec::new(),
        });
    };
    visible(flow, &command.caller, &session, &actor, message, SequenceAccess::Joined).await?;

    let mut cancelled = Vec::new();
    for row in rows.into_iter().filter(|row| {
        row.source_message_id == message_id
            && row.state.kind == bcs_domain::DeliveryType::Send
            && row.state.status == Status::Queued
    }) {
        match service
            .transition(DeliveryTransitionCommand {
                caller_dispatch_commit: None,
                caller_cancel: None,
                caller_terminal: None,
                delivery_id: row.delivery_id,
                expected_state_version: row.state.state_version,
                event: Event::CancelRequested,
                now_ms: chrono::Utc::now().timestamp_millis(),
                request_id: None,
                actor_id: Some(actor.clone()),
                reply: None,
                transport_context_json: None,
                deadline_at_ms: None,
            })
            .await
        {
            Ok(updated) if updated.state.status == Status::Cancelled => {
                cancelled.push((&updated).into());
            }
            Ok(_) | Err(ManagedDeliveryError::Conflict | ManagedDeliveryError::NotFound) => {}
            Err(ManagedDeliveryError::Repository(MessageDeliveryRepoError::Storage(_))) => {
                return Err(ServiceError::InternalError(
                    "queued cancellation persistence failed".into(),
                ));
            }
            Err(_) => {}
        }
    }
    Ok(CancelLatestQueuedMessageOutcome {
        message_id: Some(message_id),
        cancelled,
    })
}

pub async fn resolve(
    flow: &BcsMessageFlow,
    command: bcs_service_api::ResolveMessageDeliveryCommand,
) -> ServiceResult<DeliveryStatusView> {
    use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
    use bcs_service_api::{DeliveryResolution, core::message_delivery::DeliveryLifecycleEvent as Event};
    let CallerContext::Human(human) = &command.caller else {
        return Err(ServiceError::Forbidden("manual resolution requires a Human".into()));
    };
    if command.reason.trim().is_empty() || command.reason.len() > 1024 {
        return Err(ServiceError::InvalidOperation {
            message: "resolution reason must contain 1..1024 bytes".into(), request_id: None,
        });
    }
    let (session, actor) = authorize_session(flow, &command.caller, &command.session_id).await?;
    let repo = flow.message_repo.as_ref()
        .ok_or_else(|| ServiceError::InternalError("message store unavailable".into()))?;
    let message = repo.get_message_by_id(&session.id, &command.message_id).await
        .map_err(|_| ServiceError::InternalError("message ownership lookup failed".into()))?
        .ok_or_else(|| ServiceError::InvalidOperation { message: "message not found".into(), request_id: None })?;
    visible(flow, &command.caller, &session, &actor, &message, SequenceAccess::Joined).await?;
    let service = flow.managed_deliveries.as_ref()
        .ok_or_else(|| ServiceError::Conflict("message is not queue-managed".into()))?;
    let row = service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Message(command.message_id.clone())).await
        .map_err(|_| ServiceError::InternalError("delivery resolution lookup failed".into()))?
        .into_iter().find(|row| row.delivery_id == command.delivery_id && row.session_id == session.id)
        .ok_or_else(|| ServiceError::Conflict("delivery not found".into()))?;
    if message.sender_id != actor && !flow.registry.get(&row.target_bot_id).await
        .is_some_and(|bot| bot.created_by.as_deref() == Some(human.staff_no.as_str())) {
        return Err(ServiceError::Forbidden("only the sender or target Bot owner can resolve delivery".into()));
    }
    if row.state.kind != bcs_domain::DeliveryType::Send
        || !matches!(row.state.status, Status::Unknown | Status::CancelUnknown)
        || row.state.state_version != command.expected_state_version {
        return Err(ServiceError::Conflict("delivery state changed; refresh before resolving".into()));
    }
    let event = match command.resolution {
        DeliveryResolution::ConfirmedNotSent => Event::ResolveNotSent,
        DeliveryResolution::ConfirmedStopped => Event::ResolveStopped,
    };
    let updated = service.transition(DeliveryTransitionCommand {
        caller_dispatch_commit: None,
        caller_cancel: None,
        caller_terminal: None,
        delivery_id: row.delivery_id.clone(), expected_state_version: command.expected_state_version,
        event, now_ms: chrono::Utc::now().timestamp_millis(), request_id: row.request_id.clone(),
        actor_id: Some(actor.clone()), reply: None,
        transport_context_json: Some(serde_json::json!({"reason": command.reason.trim()})),
        deadline_at_ms: None,
    }).await.map_err(|error| match error {
        ManagedDeliveryError::Repository(bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Storage(_)) =>
            ServiceError::InternalError("delivery resolution persistence failed".into()),
        _ => ServiceError::Conflict("delivery state changed; refresh before resolving".into()),
    })?;
    if let (Some(contexts), Some(run_id)) = (&flow.bot_run_context, &updated.run_id) {
        let _ = contexts.mark_terminal(run_id).await;
        contexts.mark_provider_transport_terminal(run_id).await;
    }
    tracing::info!(actor_id = %actor, delivery_id = %updated.delivery_id, session_id = %updated.session_id,
        resolution = ?command.resolution, state_version = updated.state.state_version, "delivery manually resolved");
    Ok(DeliveryStatusView::from(&updated).with_content_preview(Some(&message.content)))
}

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

#[derive(Clone, Copy)]
enum SequenceAccess {
    // Mutations retain the original authorization boundary.
    Joined,
    History { from_seq: Option<i64> },
}

fn joined_sequence(session: &Session, actor: &str) -> Option<i64> {
    session.participant_join_seq.as_ref()
        .and_then(|value| value.get(actor))
        .and_then(serde_json::Value::as_i64)
}

async fn history_sequence_access(
    flow: &BcsMessageFlow,
    session: &Session,
    actor: &str,
) -> ServiceResult<SequenceAccess> {
    let Some(anchor) = joined_sequence(session, actor) else {
        // Preserve legacy Sessions without a recorded join boundary.
        return Ok(SequenceAccess::Joined);
    };
    let Some(group) = flow.group.try_get(&session.group_id).await? else {
        return Err(ServiceError::GroupNotFound(session.group_id.clone()));
    };
    let from_seq = match group.group_strategy {
        bcs_domain::GroupStrategy::Chat => {
            let repo = flow.message_repo.as_ref()
                .ok_or_else(|| ServiceError::InternalError("message store unavailable".into()))?;
            Some(repo.resolve_history_window_start(
                &session.id, anchor, flow.new_participant_visible_limit,
            ).await.map_err(|_| ServiceError::InternalError("message history window lookup failed".into()))?)
        }
        // These history projections use owner/audience, not a Chat join window.
        bcs_domain::GroupStrategy::ManagerWorker | bcs_domain::GroupStrategy::StateMachine => None,
    };
    Ok(SequenceAccess::History { from_seq })
}

async fn visible(
    flow: &BcsMessageFlow,
    caller: &CallerContext,
    session: &Session,
    actor: &str,
    message: &PersistedMessage,
    sequence_access: SequenceAccess,
) -> ServiceResult<()> {
    if message.session_id != session.id || message.group_id != session.group_id {
        return Err(ServiceError::Forbidden(
            "message does not belong to this session".into(),
        ));
    }
    let from_seq = match sequence_access {
        SequenceAccess::Joined => joined_sequence(session, actor),
        SequenceAccess::History { from_seq } => from_seq,
    };
    let supplemental = matches!(sequence_access, SequenceAccess::History { .. })
        && bcs_domain::state_machine_history::is_supplemental_history(message);
    if from_seq.is_some_and(|from| message.session_seq < from) && !supplemental {
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
    let mut history_access = None;
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
                let access = match history_access {
                    Some(access) => access,
                    None => {
                        let access = history_sequence_access(flow, &session, &actor).await?;
                        history_access = Some(access);
                        access
                    }
                };
                visible(flow, &query.caller, &session, &actor, &message, access).await?;
            }
            checked.insert(row.source_message_id.clone(), (selected, message.content.clone()));
        }
        let (visible, ref content) = checked[&row.source_message_id];
        if visible {
            result.push(DeliveryStatusView::from(row).with_content_preview(Some(content)));
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
    visible(flow, &command.caller, &session, &actor, &message, SequenceAccess::Joined).await?;
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
                delivery: DeliveryStatusView::from(&row).with_content_preview(Some(&message.content)),
                error: Some("exact_abort_not_supported".into()),
            });
            continue;
        }
        let now_ms = chrono::Utc::now().timestamp_millis();
        match service.transition(DeliveryTransitionCommand { caller_dispatch_commit: None, delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
            event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CancelRequested, now_ms,
            request_id: None, actor_id: Some(actor.clone()), reply: None, transport_context_json: None,
            deadline_at_ms: Some(now_ms.saturating_add(30_000)), caller_cancel: None, caller_terminal: None }).await {
            Ok(updated) => results.push(CancelMessageDeliveryResult { delivery: DeliveryStatusView::from(&updated).with_content_preview(Some(&message.content)), error: None }),
            Err(error) => {
                if matches!(error, ManagedDeliveryError::Repository(bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Storage(_))) {
                    return Err(ServiceError::InternalError("delivery cancellation persistence failed".into()));
                }
                results.push(CancelMessageDeliveryResult { delivery: DeliveryStatusView::from(&row).with_content_preview(Some(&message.content)), error: Some("delivery_state_changed".into()) });
            }
        }
    }
    Ok(results)
}

/// Credential-free execution control (spec §7.8 "execution.status /
/// execution.cancel"): locates a caller dispatch strictly by its original
/// eight-field association and only ever reads or extends the durable typed
/// intents — no container ensure, no identity re-acquisition, no Provider
/// re-targeting. Shares the SAME Managed service instance as the scheduler
/// and the callbacks: control decisions and lifecycle CAS commit against
/// one state machine.
pub struct CallerExecutionControl {
    managed: std::sync::Arc<crate::managed_delivery::ManagedMessageDelivery>,
}

impl CallerExecutionControl {
    pub fn new(managed: std::sync::Arc<crate::managed_delivery::ManagedMessageDelivery>) -> Self {
        Self { managed }
    }

    /// Locate the committed dispatch row by its network idempotency id and
    /// verify the association. A missing row or a mismatched association is
    /// reported as not-found without leaking whether a row exists.
    async fn locate(
        &self,
        query: &bcs_service_api::CallerExecutionQuery,
    ) -> Result<Option<bcs_domain::message_delivery::PersistedMessageDelivery>, ManagedDeliveryError> {
        let rows = self
            .managed
            .lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Request(
                query.dispatch_id.clone(),
            ))
            .await?;
        let row = rows.into_iter().find(|row| {
            row.env == query.environment
                && row.target_bot_id == query.target_bot_id
                && row.session_id == query.session_id
                && row.run_id.as_deref() == Some(query.run_id.as_str())
                && row.attempt_no == query.attempt
                && row.request_id.as_deref() == Some(query.dispatch_id.as_str())
        });
        Ok(row)
    }
}

fn status_wire_name(status: bcs_domain::message_delivery::MessageDeliveryStatus) -> String {
    serde_json::to_value(status)
        .ok()
        .and_then(|value| value.as_str().map(str::to_string))
        .unwrap_or_else(|| "unknown".to_string())
}

fn view_for(
    status: bcs_service_api::CallerExecutionStatus,
    row: Option<&bcs_domain::message_delivery::PersistedMessageDelivery>,
) -> bcs_service_api::CallerExecutionStatusView {
    match row {
        None => bcs_service_api::CallerExecutionStatusView {
            status,
            execution_state: "not_found".to_string(),
            control_revision: None,
            cancel_state: None,
        },
        Some(row) => bcs_service_api::CallerExecutionStatusView {
            status,
            execution_state: status_wire_name(row.state.status),
            control_revision: row
                .semantic_projection_json
                .get("caller_dispatch")
                .and_then(|v| v.get("caller_control_revision"))
                .and_then(serde_json::Value::as_u64),
            cancel_state: row
                .semantic_projection_json
                .get("caller_cancel")
                .and_then(|v| v.get("cancel_state"))
                .and_then(serde_json::Value::as_str)
                .map(str::to_string),
        },
    }
}

#[async_trait::async_trait]
impl bcs_service_api::CallerExecutionControlService for CallerExecutionControl {
    async fn execution_status(
        &self,
        query: bcs_service_api::CallerExecutionQuery,
    ) -> Result<bcs_service_api::CallerExecutionStatusView, ManagedDeliveryError> {
        let row = self.locate(&query).await?;
        let Some(row) = row else {
            return Ok(view_for(bcs_service_api::CallerExecutionStatus::NotFound, None));
        };
        // v1: an uncertain attempt reports Unknown — never a fabricated
        // terminal block, and not-found is never concluded from silence.
        let status = match row.state.status {
            bcs_domain::message_delivery::MessageDeliveryStatus::Unknown
            | bcs_domain::message_delivery::MessageDeliveryStatus::CancelUnknown => {
                bcs_service_api::CallerExecutionStatus::Unknown
            }
            _ => bcs_service_api::CallerExecutionStatus::Accepted,
        };
        Ok(view_for(status, Some(&row)))
    }

    async fn execution_cancel(
        &self,
        command: bcs_service_api::CallerExecutionCancelCommand,
    ) -> Result<bcs_service_api::CallerExecutionCancelOutcome, ManagedDeliveryError> {
        let row = self.locate(&command.query).await?;
        let Some(row) = row else {
            // NotFound is not stop evidence: the honest v1 answer is an
            // unconfirmed intent, never a release claim.
            return Ok(bcs_service_api::CallerExecutionCancelOutcome {
                cancel_state: "unconfirmed".to_string(),
                execution_state: "not_found".to_string(),
                last_event_seq: None,
            });
        };
        let dispatch = row.semantic_projection_json.get("caller_dispatch");
        let Some(dispatch) = dispatch else {
            // The row matched the association but has no committed
            // dispatch facts to freeze the intent against — refuse to lie.
            return Ok(bcs_service_api::CallerExecutionCancelOutcome {
                cancel_state: "unconfirmed".to_string(),
                execution_state: status_wire_name(row.state.status),
                last_event_seq: None,
            });
        };
        let plan = bcs_service_api::application::message_delivery::CancelCallerDispatchCommand {
            delivery_id: row.delivery_id.clone(),
            expected_state_version: row.state.state_version,
            now_ms: command.now_ms,
            dispatch: bcs_service_api::port::CallerDispatchAssociation {
                environment: command.query.environment.clone(),
                target_bot_id: command.query.target_bot_id.clone(),
                provider_id: command.query.provider_id.clone(),
                provider_bot_ref: command.query.provider_bot_ref.clone(),
                session_id: command.query.session_id.clone(),
                run_id: command.query.run_id.clone(),
                attempt: command.query.attempt,
                dispatch_id: command.query.dispatch_id.clone(),
            },
            cancel: bcs_service_api::application::message_delivery::CallerCancelIntent {
                cancel_request_id: command.cancel_request_id,
                control_revision: dispatch
                    .get("caller_control_revision")
                    .and_then(serde_json::Value::as_u64)
                    .unwrap_or(0),
                reason: command.reason,
                not_after_ms: dispatch
                    .get("caller_not_after_ms")
                    .and_then(serde_json::Value::as_i64)
                    .unwrap_or(i64::MAX),
            },
        };
        // Replay-first: an already-durable identical intent is a no-write
        // success; a different one conflicts; a fresh row transitions.
        let applied = self.managed.cancel_caller_dispatch(plan.clone()).await;
        match applied {
            Ok(_) => {}
            Err(ManagedDeliveryError::Conflict) => {
                // Concurrent writer advanced the row: re-read once and retry
                // the SAME command; still-conflicting state is a real
                // conflict, not a retry loop.
                let reloaded = self
                    .locate(&command.query)
                    .await?
                    .ok_or(ManagedDeliveryError::NotFound)?;
                let mut retry = plan;
                retry.expected_state_version = reloaded.state.state_version;
                self.managed.cancel_caller_dispatch(retry).await?;
            }
            Err(error) => return Err(error),
        }
        // Read-your-own-write receipt: the durable intent and the row's
        // post-transition state come back from the same managed view.
        let settled = self
            .managed
            .lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Request(
                command.query.dispatch_id.clone(),
            ))
            .await?
            .into_iter()
            .find(|row| row.request_id.as_deref() == Some(command.query.dispatch_id.as_str()))
            .ok_or(ManagedDeliveryError::NotFound)?;
        Ok(bcs_service_api::CallerExecutionCancelOutcome {
            cancel_state: settled
                .semantic_projection_json
                .get("caller_cancel")
                .and_then(|v| v.get("cancel_state"))
                .and_then(serde_json::Value::as_str)
                .unwrap_or("requested")
                .to_string(),
            execution_state: status_wire_name(settled.state.status),
            last_event_seq: Some(settled.state.state_version),
        })
    }
}

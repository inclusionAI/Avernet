//! Canonical Group admission shared by ingress and reply routing.
use crate::{
    BcsMessageFlow,
    queued_group::{QueuedGroupProjection, queued_inbound_content},
};
use bcs_domain::{GroupStrategy, NewMessage, SenderType, message_delivery::DeliveryFlowKind};
use bcs_service_api::port::repo::message_delivery::{
    AdmitMessageDeliveries, DeliveryAdmissionTarget,
};
use bcs_service_api::{Group, RoutingDecision, ServiceError, ServiceResult, WebSendCommand};

pub async fn manages_any(flow: &BcsMessageFlow, group: &Group) -> bool {
    if let Some(live) = &flow.delivery_policy {
        let policy = live.snapshot.read().await;
        group.participants.iter().any(|p| policy.policy.manages_group(&p.bot_uuid))
    } else {
        group.participants.iter().any(|p| flow.group_delivery_limits.contains_key(&p.bot_uuid))
    }
}

/// Disabling admission must not turn a pending lane into a legacy bypass.
/// Existing work keeps draining; callers can retry after it has settled.
pub async fn guard_legacy_targets(
    flow: &BcsMessageFlow,
    targets: &[bcs_service_api::RoutingTarget],
) -> ServiceResult<()> {
    let Some(service) = &flow.managed_deliveries else {
        return Ok(());
    };
    let policy = match &flow.delivery_policy { Some(live) => Some(live.snapshot.read().await.clone()), None => None };
    let legacy: Vec<_> = targets
        .iter()
        .filter(|t| !policy.as_ref().map_or_else(|| flow.group_delivery_limits.contains_key(&t.bot_uuid), |p| p.policy.manages_group(&t.bot_uuid)))
        .collect();
    if legacy.is_empty() {
        return Ok(());
    }
    let mut rows = Vec::new();
    for target in &legacy {
        rows.extend(service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::BotPending(target.bot_uuid.clone())).await.map_err(|_| ServiceError::InternalError("queue drain lookup failed".into()))?);
    }
    use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
    if rows.iter().any(|row| {
        legacy
            .iter()
            .any(|target| target.bot_uuid == row.target_bot_id)
            && !matches!(
                row.state.status,
                Status::Completed
                    | Status::Failed
                    | Status::Cancelled
                    | Status::Expired
                    | Status::RejectedCapacity
                    | Status::Consumed
                    | Status::DiscardedContext
            )
    }) {
        return Err(ServiceError::InvalidOperation {
            message: "queue_draining: existing Bot work must settle before legacy delivery resumes"
                .into(),
            request_id: None,
        });
    }
    // No old Send can carry these contexts. Settle them instead of waiting
    // for a managed Send that disabled admission will never create.
    for target in legacy {
        loop {
            let contexts = service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::BotPendingContexts(target.bot_uuid.clone())).await
                .map_err(|_| ServiceError::InternalError("queue context drain lookup failed".into()))?;
            if contexts.is_empty() { break; }
            for row in contexts {
                service.transition(bcs_service_api::DeliveryTransitionCommand {
                    delivery_id: row.delivery_id, expected_state_version: row.state.state_version,
                    event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CancelRequested,
                    now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: None,
                    reply: None, transport_context_json: None, deadline_at_ms: None,
                }).await.map_err(|_| ServiceError::InternalError("queue context drain failed".into()))?;
            }
        }
    }
    Ok(())
}

pub async fn resolve_group_session(
    flow: &BcsMessageFlow,
    group: &Group,
    session: Option<String>,
) -> ServiceResult<Option<String>> {
    if session.is_some()
        || group.group_strategy == GroupStrategy::StateMachine
        || !manages_any(flow, group).await
    {
        return Ok(session);
    }
    let sessions = flow.session_management.as_ref().ok_or_else(|| {
        ServiceError::InternalError("queue Session management unavailable".into())
    })?;
    let candidates = sessions
        .list_by_group(
            &group.id,
            Some(bcs_service_api::SessionStatus::Running),
            0,
            2,
            None,
            None,
        )
        .await
        .map_err(|_| ServiceError::InternalError("queue Session lookup failed".into()))?;
    if candidates.len() != 1 {
        return Err(ServiceError::InvalidOperation { message: "queue admission requires an explicit canonical Session when no unique running Session exists".into(), request_id: None });
    }
    Ok(Some(candidates[0].id.clone()))
}

pub async fn find_managed_run(
    flow: &BcsMessageFlow,
    command: &bcs_service_api::BotEventCommand,
) -> ServiceResult<Option<bcs_domain::message_delivery::PersistedMessageDelivery>> {
    let Some(service) = &flow.managed_deliveries else {
        return Ok(None);
    };
    let matches_run = |row: &bcs_domain::message_delivery::PersistedMessageDelivery| {
        row.target_bot_id == command.bot_id
            && row.group_id == command.group_id
            && command
                .bcs_session_id
                .as_ref()
                .is_none_or(|session| session == &row.session_id)
            && (row.run_id.as_deref() == Some(command.run_id.as_str())
                || row.request_id.as_deref() == Some(command.run_id.as_str())
                || row
                    .transport_context_json
                    .as_ref()
                    .and_then(|v| v.get("downstream_run_id"))
                    .and_then(|v| v.as_str())
                    == Some(command.run_id.as_str()))
    };
    let lookup = || service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Run {
        bot: command.bot_id.clone(), alias: command.run_id.clone(),
    });
    // Retain a received terminal even if the first DB lookup fails. Never
    // replay the whole event pipeline (which may contain external effects).
    let rows = if matches!(command.state, bcs_service_api::ChatEventState::Final
        | bcs_service_api::ChatEventState::Error | bcs_service_api::ChatEventState::Aborted)
        && matches!(command.event_type.as_str(), "chat" | "chat.event") {
        crate::storage_retry::retry(crate::storage_retry::shutdown(flow), "terminal_lookup",
            crate::storage_retry::managed_storage, lookup).await
    } else { lookup().await }
        .map_err(|_| ServiceError::InternalError("managed run lookup failed".into()))?;
    if let Some(row) = rows.into_iter().find(&matches_run) {
        return Ok(Some(row));
    }
    Ok(None)
}

pub(crate) struct QueuedReply {
    pub target_ids: Vec<String>,
    /// A newly admitted logical relay, not a count of recipients or attempts.
    pub relayed: bool,
}

/// Returns handled target ids, including capacity rejections, so they cannot
/// bypass admission via legacy delivery. Only fresh accepted work counts.
pub(crate) async fn commit_routed_reply(
    flow: &BcsMessageFlow,
    group: &Group,
    event: &bcs_service_api::BotEventCommand,
    text: &str,
    decision: &RoutingDecision,
    contexts: Vec<(bcs_service_api::RoutingTarget, bcs_protocol::GroupContext)>,
    sender_name: &str,
    sender_owner: Option<String>,
    forward_hop: Option<u32>,
    strip_mentions: bool,
    normalized: Option<&crate::run_reply::RunReply>,
) -> ServiceResult<Option<QueuedReply>> {
    if group.group_strategy == GroupStrategy::StateMachine {
        return Ok(None);
    }
    let original = find_managed_run(flow, event).await?;
    let build_timing = crate::reply_timing::Timer::new("final.reply_build_and_admission_reads");
    if original.is_none() {
        guard_legacy_targets(flow, &decision.targets).await?;
    }
    if original.as_ref().is_some_and(|row| {
        matches!(
            row.state.status,
            bcs_domain::message_delivery::MessageDeliveryStatus::Completed
                | bcs_domain::message_delivery::MessageDeliveryStatus::Failed
                | bcs_domain::message_delivery::MessageDeliveryStatus::Cancelled
                | bcs_domain::message_delivery::MessageDeliveryStatus::Expired
        )
    }) {
        return Ok(Some(QueuedReply { relayed: false, target_ids: decision
                .targets
                .iter()
                .map(|t| t.bot_uuid.clone())
                .collect() }));
    }
    let policy = match &flow.delivery_policy { Some(live) => Some(live.snapshot.read().await.clone()), None => None };
    let reply_limits = if original.is_some() {
        &flow.group_reply_delivery_limits
    } else {
        &flow.group_delivery_limits
    };
    let selected: Vec<_> = contexts
        .into_iter()
        .filter_map(|(target, context)| {
            let limit = if let Some(p) = &policy {
                (original.is_some() || p.policy.manages_group(&target.bot_uuid)).then(|| p.policy.bot(&target.bot_uuid).max_queued)
            } else { reply_limits.get(&target.bot_uuid).copied() };
            limit.map(|limit| (target, context, limit))
        })
        .collect();
    if original.is_none() && selected.is_empty() {
        return Ok(None);
    }
    let service = flow
        .managed_deliveries
        .as_ref()
        .ok_or_else(|| ServiceError::InternalError("queue reply service unavailable".into()))?;
    let session_id = original
        .as_ref()
        .map(|r| r.session_id.clone())
        .or_else(|| event.bcs_session_id.clone())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| ServiceError::InternalError("queue reply Session missing".into()))?;
    let command = WebSendCommand {
        caller: bcs_service_api::CallerContext::Public,
        group_id: group.id.clone(),
        session_id: Some(session_id.clone()),
        from_actor_id: event.bot_id.clone(),
        from_name: Some(sender_name.into()),
        message: text.into(),
        mentions: decision.mentions.clone(),
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    };
    let mut targets = Vec::new();
    for (target, context, limit) in selected {
        let projection = QueuedGroupProjection::capture(
            flow,
            group,
            &command,
            decision,
            &target,
            sender_name.into(),
            sender_owner.clone(),
        )
        .await?
        .with_reply_context(context, forward_hop, strip_mentions);
        targets.push(DeliveryAdmissionTarget {
            target_bot_id: target.bot_uuid,
            kind: target.delivery_type,
            max_queued: limit,
            semantic_projection_json: serde_json::to_value(projection)?,
        });
    }
    let target_ids = targets.iter().map(|t| t.target_bot_id.clone()).collect();
    let now_ms = chrono::Utc::now().timestamp_millis();
    let message_id = uuid::Uuid::new_v4().to_string();
    let run_id = original
        .as_ref()
        .and_then(|r| r.run_id.clone())
        .unwrap_or_else(|| event.run_id.clone());
    let successful = event.state == bcs_service_api::ChatEventState::Final;
    let mut content = serde_json::json!({"text":text});
    if let Some(reply) = normalized {
        content["normalization"] = serde_json::json!({"version":1,"method":reply.method,
            "raw_final":reply.raw_final,"source_message_ids":reply.source_ids});
    }
    let owner_bot_id = crate::group_flow::manager_worker_self_owner(
        flow,
        &group.id,
        Some(&session_id),
        &event.bot_id,
    )
    .await;
    let (visibility_domain, audience) = crate::group_flow::persisted_message_visibility(Some(&group), &event.bot_id, SenderType::Bot, "chat", owner_bot_id.as_deref())?;
    let message = NewMessage {
        group_id: group.id.clone(),
        session_id,
        sender_id: event.bot_id.clone(),
        sender_type: SenderType::Bot,
        message_type: if successful { "run_reply" } else { "chat" }.into(),
        content,
        client_msg_id: Some(format!("run-reply:{run_id}")),
        visibility_domain,
        audience,
        owner_bot_id,
        created_at: now_ms as u64,
        run_id,
    };
    let display_text = normalized.map(|r| r.display.as_str()).unwrap_or(text);
    let display_message = if successful && !display_text.is_empty() {
        let id = uuid::Uuid::new_v4().to_string();
        let mut display = message.clone();
        display.message_type = "chat".into();
        display.content = serde_json::Value::String(display_text.into());
        display.client_msg_id = Some(format!("run-display:{}", display.run_id));
        let event = prepare_message_event(flow, &id, &display)?;
        Some(bcs_service_api::port::repo::message_delivery::DeliveryDisplayMessage { message_id: id, message: display, event })
    } else { None };
    let record = if successful { None } else { prepare_message_event(flow, &message_id, &message)? };
    let reply = AdmitMessageDeliveries {
        display_message,
        message_id,
        message,
        flow_kind: DeliveryFlowKind::Group,
        targets,
        now_ms,
        expire_at_ms: policy.as_ref().map_or(flow.delivery_queue_ttl_ms, |p| p.policy.queue_ttl_ms.map(|v| v as i64))
            .map(|ttl| now_ms.saturating_add(ttl)),
        event: record,
    };
    drop(build_timing);
    let reply_message_id = reply.message_id.clone();
    let mut fresh = true;
    if let Some(mut row) = original {
        use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent;
        use bcs_service_api::{DeliveryTransitionCommand, ManagedDeliveryError};
        let mut committed = false;
        let _timing = crate::reply_timing::Timer::new("final.transition_including_retries");
        for _ in 0..3 {
            let command = DeliveryTransitionCommand { delivery_id: row.delivery_id.clone(),
            expected_state_version: row.state.state_version, event: match event.state {
                bcs_service_api::ChatEventState::Error => DeliveryLifecycleEvent::Failed,
                bcs_service_api::ChatEventState::Aborted => DeliveryLifecycleEvent::Aborted,
                _ => DeliveryLifecycleEvent::Completed,
            },
            now_ms, request_id: None, actor_id: None, reply: Some(reply.clone()), transport_context_json: None,
            deadline_at_ms: None };
            // Keep reply IDs, normalized text and expected version stable. If
            // commit succeeded but its response was lost, CAS conflicts and the
            // existing reread below confirms terminal without inserting twice.
            match crate::storage_retry::retry(crate::storage_retry::shutdown(flow), "terminal_commit",
                crate::storage_retry::managed_storage, || service.transition(command.clone())).await {
            Ok(_) => { committed = true; break; }
            Err(ManagedDeliveryError::Repository(bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Storage(_))) => return Err(ServiceError::InternalError("queue terminal transaction failed".into())),
            Err(_) => {
                row = find_managed_run(flow, event).await?.ok_or_else(|| ServiceError::InternalError("queue terminal run disappeared".into()))?;
                if matches!(row.state.status, bcs_domain::message_delivery::MessageDeliveryStatus::Completed
                    | bcs_domain::message_delivery::MessageDeliveryStatus::Failed | bcs_domain::message_delivery::MessageDeliveryStatus::Cancelled) {
                    return Ok(Some(QueuedReply { relayed: false, target_ids: decision.targets.iter().map(|t| t.bot_uuid.clone()).collect() }));
                }
            }
        }
        }
        if !committed {
            return Err(ServiceError::InternalError(
                "queue terminal changed concurrently".into(),
            ));
        }
    } else {
        let admitted = service
            .admit(reply)
            .await
            .map_err(|_| ServiceError::InternalError("queue reply admission failed".into()))?;
        fresh = !admitted.duplicate;
    }
    let rows = crate::storage_retry::retry(crate::storage_retry::shutdown(flow), "relay_admission_lookup",
        crate::storage_retry::managed_storage, || service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Message(reply_message_id.clone())))
        .await.map_err(|_| ServiceError::InternalError("queue relay admission lookup failed".into()))?;
    let relayed = fresh && rows.iter().any(|row| row.state.status != bcs_domain::message_delivery::MessageDeliveryStatus::RejectedCapacity);
    Ok(Some(QueuedReply { target_ids, relayed }))
}

pub(crate) async fn settle_without_relay(
    flow: &BcsMessageFlow,
    command: &bcs_service_api::BotEventCommand,
    text: &str,
    normalized: Option<&crate::run_reply::RunReply>,
) -> ServiceResult<()> {
    let Some(row) = find_managed_run(flow, command).await? else {
        return Ok(());
    };
    let group = flow
        .group
        .get(&row.group_id)
        .await
        .unwrap_or_else(|| Group::new(&row.group_id, &row.target_bot_id, Vec::new()));
    let decision = RoutingDecision {
        targets: Vec::new(),
        mentions: Vec::new(),
        cleaned_message: text.into(),
        hidden_mentions: Vec::new(),
    };
    commit_routed_reply(
        flow,
        &group,
        command,
        text,
        &decision,
        Vec::new(),
        &command.bot_id,
        None,
        None,
        false,
        normalized,
    )
    .await?;
    Ok(())
}

pub async fn prepare_group_admission(
    flow: &BcsMessageFlow,
    group: &Group,
    command: &WebSendCommand,
    decision: &RoutingDecision,
    sender_name: &str,
    sender_owner: Option<String>,
) -> ServiceResult<Option<AdmitMessageDeliveries>> {
    // State-machine is an independently gated business flow, even when its
    // transport happens to be the group endpoint.
    if group.group_strategy == GroupStrategy::StateMachine {
        return Ok(None);
    }
    guard_legacy_targets(flow, &decision.targets).await?;
    let policy = match &flow.delivery_policy { Some(live) => Some(live.snapshot.read().await.clone()), None => None };
    let selected: Vec<_> = decision
        .targets
        .iter()
        .filter_map(|target| {
            let limit = if let Some(p) = &policy {
                p.policy.manages_group(&target.bot_uuid).then(|| p.policy.bot(&target.bot_uuid).max_queued)
            } else { flow.group_delivery_limits.get(&target.bot_uuid).copied() };
            limit.map(|limit| (target, limit))
        })
        .collect();
    if selected.is_empty() {
        return Ok(None);
    }
    if flow.managed_deliveries.is_none() || flow.message_repo.is_none() {
        return Err(ServiceError::InternalError(
            "queue admission is not configured".into(),
        ));
    }
    let session_id = command
        .session_id
        .as_deref()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| ServiceError::InvalidOperation {
            message: "queue admission requires the actual canonical Session".into(),
            request_id: None,
        })?;
    let mut targets = Vec::new();
    for (target, limit) in selected {
        let projection = QueuedGroupProjection::capture(
            flow,
            group,
            command,
            decision,
            target,
            sender_name.to_owned(),
            sender_owner.clone(),
        )
        .await?;
        let mut projection = serde_json::to_value(projection)?;
        if let Some(policy) = &policy { projection["policy_version"] = serde_json::json!(policy.version); }
        targets.push(DeliveryAdmissionTarget {
            target_bot_id: target.bot_uuid.clone(),
            kind: target.delivery_type,
            max_queued: limit,
            semantic_projection_json: projection,
        });
    }
    let now_ms = chrono::Utc::now().timestamp_millis();
    let message_id = uuid::Uuid::new_v4().to_string();
    let sender_type = if command.from_actor_id.starts_with("human_") { SenderType::Human } else { SenderType::Bot };
    let (visibility_domain, audience) = crate::group_flow::persisted_message_visibility(Some(group), &command.from_actor_id, sender_type, "chat", None)?;
    let message = NewMessage {
        group_id: group.id.clone(),
        session_id: session_id.into(),
        sender_id: command.from_actor_id.clone(),
        sender_type: if command.from_actor_id.starts_with("human_") {
            SenderType::Human
        } else {
            SenderType::Bot
        },
        message_type: "chat".into(),
        content: queued_inbound_content(command),
        client_msg_id: command.idempotency_key.clone(),
        visibility_domain,
        audience,
        owner_bot_id: None,
        created_at: now_ms as u64,
        run_id: String::new(),
    };
    let event = prepare_message_event(flow, &message_id, &message)?;
    Ok(Some(AdmitMessageDeliveries {
        display_message: None,
        message_id,
        message,
        flow_kind: DeliveryFlowKind::Group,
        targets,
        now_ms,
        expire_at_ms: policy.as_ref().map_or(flow.delivery_queue_ttl_ms, |p| p.policy.queue_ttl_ms.map(|v| v as i64))
            .map(|ttl| now_ms.saturating_add(ttl)),
        event,
    }))
}

pub fn prepare_message_event(
    flow: &BcsMessageFlow,
    message_id: &str,
    message: &NewMessage,
) -> ServiceResult<Option<bcs_service_api::port::repo::AppendEventRecord>> {
    use bcs_service_api::port::NewEvent;
    use bcs_service_api::types::{
        EVENT_SCHEMA_VERSION_V1, EventActor, EventActorType, EventScope, EventSubject,
    };
    let Some(factory) = flow.event_record_factory.as_ref() else {
        return Ok(None);
    };
    let mut content = message.content.clone();
    if let Some(attachments) = content
        .get_mut("attachments")
        .and_then(|v| v.as_array_mut())
    {
        for attachment in attachments {
            if let Some(object) = attachment.as_object_mut() {
                object.remove("url");
                object.remove("expires_at");
            }
        }
    }
    // Source attribution is consumed internally for IM reply routing, not
    // copied into public event streams.
    if let Some(object) = content.as_object_mut() {
        object.remove("channel_sender_identity");
        object.remove("source_im_message_id");
        if let Some(display) = object.remove("queue_display_text") {
            object.insert("text".into(), display);
        }
    }
    let (sender_type, actor_type) = match message.sender_type {
        SenderType::Human => ("human", EventActorType::Human),
        SenderType::Bot => ("bot", EventActorType::Bot),
        SenderType::System => ("system", EventActorType::System),
    };
    let mut data = std::collections::BTreeMap::from([
        ("logical_message_id".into(), serde_json::json!(message_id)),
        (
            "message_type".into(),
            serde_json::json!(message.message_type),
        ),
        (
            "sender".into(),
            serde_json::json!({"id":message.sender_id,"type":sender_type}),
        ),
        (
            "content".into(),
            serde_json::json!({"content_type":"application/json", "size_bytes":serde_json::to_vec(&content)?.len(),
            "json":content, "truncated":false}),
        ),
        ("attachments".into(), serde_json::json!([])),
    ]);
    if !message.run_id.is_empty() {
        data.insert("run_id".into(), serde_json::json!(message.run_id));
    }
    factory
        .prepare(NewEvent {
            event_id: format!("evt_{}", uuid::Uuid::new_v4()),
            event_type: "message.created".into(),
            schema_version: EVENT_SCHEMA_VERSION_V1.into(),
            producer: "bcs-message-flow".into(),
            producer_key: format!("message.created:{message_id}"),
            occurred_at: chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            subject: EventSubject {
                subject_type: "message".into(),
                id: message_id.into(),
            },
            scope: EventScope {
                group_id: Some(message.group_id.clone()),
                session_id: Some(message.session_id.clone()),
                ..Default::default()
            },
            stream_key: format!("session:{}", message.session_id),
            actor: Some(EventActor {
                actor_type,
                id: message.sender_id.clone(),
                display_name: None,
            }),
            correlation_id: (!message.run_id.is_empty()).then(|| message.run_id.clone()),
            causation_event_id: None,
            trace_id: None,
            data,
        })
        .map_err(|error| ServiceError::InternalError(error.to_string()))
}

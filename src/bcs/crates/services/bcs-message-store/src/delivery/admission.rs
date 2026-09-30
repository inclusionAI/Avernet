//! Canonical message and delivery admission planning.
use super::*;

pub(crate) fn validate_admission(
    command: &AdmitMessageDeliveries,
) -> Result<(), MessageDeliveryRepoError> {
    let direct = command.flow_kind == bcs_domain::message_delivery::DeliveryFlowKind::DirectA2a;
    if direct && (!command.message.group_id.is_empty() || command.targets.len() != 1
        || command.targets[0].kind != DeliveryType::Send || command.message.run_id.is_empty()
        || command.message.message_type != "direct_a2a_request" || command.event.is_some() || command.display_message.is_some()
        || command.message.visibility_domain != bcs_domain::MessageVisibilityDomain::DirectA2a
        || command.message.audience.as_ref() != Some(&bcs_domain::MessageAudience::directed([
            command.message.sender_id.clone(), command.targets[0].target_bot_id.clone(),
        ]).map_err(|e| MessageDeliveryRepoError::Invalid(e.into()))?)
        || command.message.owner_bot_id.as_ref() != Some(&command.targets[0].target_bot_id)) {
        return Err(MessageDeliveryRepoError::Invalid("invalid Direct A2A admission".into()));
    }
    crate::mysql::serialize_visibility(&command.message).map_err(|e| MessageDeliveryRepoError::Invalid(e.to_string()))?;
    if let Some(display) = &command.display_message {
        let m = &display.message;
        crate::mysql::serialize_visibility(m).map_err(|e| MessageDeliveryRepoError::Invalid(e.to_string()))?;
        if !matches!(command.message.message_type.as_str(), "run_reply" | bcs_domain::CHAT_ERROR_MESSAGE_TYPE) || command.event.is_some()
            || m.message_type != "chat" || m.session_id != command.message.session_id
            || m.group_id != command.message.group_id || m.sender_id != command.message.sender_id
            || m.sender_type != command.message.sender_type || m.owner_bot_id != command.message.owner_bot_id
            || m.run_id != command.message.run_id || display.message_id == command.message_id
            || m.visibility_domain != command.message.visibility_domain || m.audience != command.message.audience
            || display.message_id.is_empty()
        {
            return Err(MessageDeliveryRepoError::Invalid("invalid run reply display companion".into()));
        }
        if let Some(event) = &display.event {
            if event.event.subject.id != display.message_id
                || event.event.scope.session_id.as_deref() != Some(m.session_id.as_str())
                || event.event.scope.group_id.as_deref() != Some(m.group_id.as_str())
            { return Err(MessageDeliveryRepoError::Invalid("display event scope mismatch".into())); }
        }
    }
    if command.message.message_type == "run_reply" && command.event.is_some() {
        return Err(MessageDeliveryRepoError::Invalid("run reply must not publish a message event".into()));
    }
    let task_error_target = command.flow_kind
        == bcs_domain::message_delivery::DeliveryFlowKind::Task
        && command.targets.len() == 1
        && command.targets[0].kind == DeliveryType::Send
        && command.targets[0].semantic_projection_json["task"]["leg"] == "result";
    if command.message.message_type == bcs_domain::CHAT_ERROR_MESSAGE_TYPE
        && (command.event.is_some()
            || (!command.targets.is_empty() && !task_error_target)
            || command.message.run_id.is_empty()
            || !command.message.content.is_string())
    {
        return Err(MessageDeliveryRepoError::Invalid(
            "chat error requires a run and display text; only a single Task result Send target is allowed"
                .into(),
        ));
    }
    if command.message.message_type == "run_reply" && command.message.run_id.is_empty() {
        return Err(MessageDeliveryRepoError::Invalid("run reply requires a run identity".into()));
    }
    if let Some(event) = &command.event {
        if event.event.subject.id != command.message_id
            || event.event.scope.session_id.as_deref() != Some(command.message.session_id.as_str())
            || event.event.scope.group_id.as_deref() != Some(command.message.group_id.as_str())
        {
            return Err(MessageDeliveryRepoError::Invalid(
                "message event scope mismatch".into(),
            ));
        }
    }
    if command.message_id.is_empty()
        || command.message.session_id.is_empty()
        || (!direct && command.message.group_id.is_empty())
        || command.now_ms < 0
        || (command.message.message_type != bcs_domain::CHAT_ERROR_MESSAGE_TYPE && command
            .message
            .content
            .get("text")
            .and_then(serde_json::Value::as_str)
            .is_none())
    {
        return Err(MessageDeliveryRepoError::Invalid(
            "canonical message identity/text is required".into(),
        ));
    }
    if let Some(attachments) = command.message.content.get("attachments") {
        serde_json::from_value::<Vec<bcs_domain::Attachment>>(attachments.clone()).map_err(
            |_| MessageDeliveryRepoError::Invalid("invalid canonical attachments".into()),
        )?;
    }
    let mut bots = BTreeSet::new();
    for target in &command.targets {
        if target.target_bot_id.is_empty()
            || !bots.insert(&target.target_bot_id)
            || !target.semantic_projection_json.is_object()
        {
            return Err(MessageDeliveryRepoError::Invalid(
                "invalid or duplicate target".into(),
            ));
        }
    }
    Ok(())
}

pub(crate) fn canonical(command: &AdmitMessageDeliveries, seq: i64) -> PersistedMessage {
    let m = &command.message;
    PersistedMessage {
        message_id: command.message_id.clone(),
        group_id: m.group_id.clone(),
        session_id: m.session_id.clone(),
        session_seq: seq,
        sender_id: m.sender_id.clone(),
        sender_type: m.sender_type,
        message_type: m.message_type.clone(),
        content: m.content.clone(),
        client_msg_id: m.client_msg_id.clone(),
        owner_bot_id: m.owner_bot_id.clone(),
        status: PersistedMessageStatus::Normal,
        created_at: m.created_at,
        run_id: m.run_id.clone(),
        visibility_domain: Some(m.visibility_domain),
        audience: m.audience.clone(),
    }
}

pub(crate) fn canonical_display(command: &AdmitMessageDeliveries, seq: i64) -> Option<PersistedMessage> {
    command.display_message.as_ref().map(|display| {
        let mut cloned = command.clone();
        cloned.message_id = display.message_id.clone();
        cloned.message = display.message.clone();
        canonical(&cloned, seq)
    })
}

/// Plan per-target admission and causal context binding under the writer lock.
/// The returned changed context rows must commit with the new rows and message.
pub(crate) fn plan_admission(
    env: &str,
    command: &AdmitMessageDeliveries,
    seq: i64,
    existing: &[PersistedMessageDelivery],
    queued_counts: Option<&std::collections::BTreeMap<String, usize>>,
) -> Result<(Vec<PersistedMessageDelivery>, Vec<DeliveryCompareAndSet>), MessageDeliveryRepoError> {
    validate_admission(command)?;
    let mut admitted = Vec::new();
    let mut changes = Vec::new();
    for target in &command.targets {
        let queued = queued_counts.and_then(|counts| counts.get(&target.target_bot_id).copied()).unwrap_or_else(|| existing
            .iter()
            .filter(|d| {
                d.env == env
                    && d.target_bot_id == target.target_bot_id
                    && d.state.kind == DeliveryType::Send
                    && d.state.status == Status::Queued
            })
            .count());
        let status = if target.rejection.is_some() {
            Status::Failed
        } else if target.kind == DeliveryType::Inject {
            Status::PendingContext
        } else if queued >= target.max_queued as usize {
            Status::RejectedCapacity
        } else {
            Status::Queued
        };
        let run_id = (target.kind == DeliveryType::Send).then(|| {
            if command.flow_kind == bcs_domain::message_delivery::DeliveryFlowKind::DirectA2a { command.message.run_id.clone() }
            else { uuid::Uuid::new_v4().to_string() }
        });
        let row = PersistedMessageDelivery {
            delivery_id: if command.flow_kind == bcs_domain::message_delivery::DeliveryFlowKind::DirectA2a {
                command.message.run_id.clone()
            } else { uuid::Uuid::new_v4().to_string() },
            env: env.into(),
            source_message_id: command.message_id.clone(),
            target_bot_id: target.target_bot_id.clone(),
            session_id: command.message.session_id.clone(),
            group_id: command.message.group_id.clone(),
            source_session_seq: seq,
            flow_kind: command.flow_kind,
            state: MessageDeliveryState {
                kind: target.kind,
                status,
                state_version: 1,
                may_have_been_sent: false,
            },
            wait_reason: None,
            available_at_ms: command.now_ms,
            expire_at_ms: if target.kind == DeliveryType::Inject && target.semantic_projection_json.get("required_context").and_then(|v| v.as_bool()) == Some(true) { None } else { command.expire_at_ms },
            created_at_ms: command.now_ms,
            updated_at_ms: command.now_ms,
            idempotency_key: run_id.clone(),
            run_id,
            attempt_no: 0,
            request_id: None,
            send_started_at_ms: None,
            submitted_at_ms: None,
            accepted_at_ms: None,
            run_deadline_at_ms: None,
            terminal_at_ms: matches!(status, Status::RejectedCapacity | Status::Failed).then_some(command.now_ms),
            bound_to_delivery_id: None,
            cancel_requested_at_ms: None,
            cancel_requested_by: None,
            cancel_reason: None,
            abort_request_id: None,
            abort_started_at_ms: None,
            cancel_deadline_at_ms: None,
            last_error_code: target.rejection.map(|r| r.code().to_owned()),
            semantic_projection_json: target.semantic_projection_json.clone(),
            transport_context_json: None,
            context_selection_json: None,
        };
        if status == Status::Queued {
            for context in existing.iter().filter(|d| {
                d.env == env
                    && d.target_bot_id == target.target_bot_id
                    && d.session_id == row.session_id
                    && d.state.kind == DeliveryType::Inject
                    && d.state.status == Status::PendingContext
                    && d.source_session_seq < seq
                    && d.expire_at_ms.is_none_or(|t| t > command.now_ms)
            }) {
                let mut bound = context.clone();
                bound.state.status = Status::Bound;
                bound.state.state_version =
                    context.state.state_version.checked_add(1).ok_or_else(|| {
                        MessageDeliveryRepoError::Invalid("state version exhausted".into())
                    })?;
                bound.bound_to_delivery_id = Some(row.delivery_id.clone());
                bound.updated_at_ms = command.now_ms;
                changes.push(DeliveryCompareAndSet {
                    expected_state_version: context.state.state_version,
                    delivery: bound,
                });
            }
        }
        admitted.push(row);
    }
    Ok((admitted, changes))
}

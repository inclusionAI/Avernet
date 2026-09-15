//! Event store validation helpers.

use super::*;

pub(super) fn validate_claim(
    worker_id: &str,
    now_ms: u64,
    lease_until_ms: u64,
    limit: u32,
    env: &str,
) -> Result<(), EventRepoError> {
    if worker_id.is_empty() || worker_id.len() > 200 || env.is_empty() {
        return Err(EventRepoError::InvalidInput(
            "claim worker id and env must be non-empty".into(),
        ));
    }
    if limit == 0 || limit > 1_000 {
        return Err(EventRepoError::InvalidInput(
            "claim limit must be between 1 and 1000".into(),
        ));
    }
    if lease_until_ms <= now_ms {
        return Err(EventRepoError::InvalidInput(
            "claim lease must expire after now".into(),
        ));
    }
    Ok(())
}

pub(super) fn validate_lease_renewal(command: &RenewEventDeliveryLease) -> Result<(), EventRepoError> {
    if command.delivery_id.is_empty()
        || command.expected_lease_owner.is_empty()
        || command.attempt_no == 0
        || command.env.is_empty()
        || command.lease_until_ms <= command.now_ms
    {
        return Err(EventRepoError::InvalidInput(
            "Delivery lease renewal is invalid".to_string(),
        ));
    }
    Ok(())
}

pub(super) fn claim_owner(worker_id: &str) -> String {
    format!("{worker_id}#{}", uuid::Uuid::new_v4())
}

pub(super) fn validate_materialization(command: &MaterializeFanoutTarget) -> Result<(), EventRepoError> {
    let delivery = &command.delivery;
    if command.target_id.is_empty()
        || command.expected_lease_owner.is_empty()
        || delivery.delivery_id.is_empty()
        || delivery.env.is_empty()
    {
        return Err(EventRepoError::InvalidInput(
            "materialization identifiers and env must be non-empty".into(),
        ));
    }
    let valid_initial_status = delivery.status == EventDeliveryStatus::Pending
        || (delivery.status == EventDeliveryStatus::DeadLettered
            && delivery.dead_lettered_at_ms.is_some()
            && delivery.last_error_category.is_some());
    if !valid_initial_status
        || delivery.attempt_count != 0
        || delivery.lease_owner.is_some()
        || delivery.lease_until_ms.is_some()
    {
        return Err(EventRepoError::InvalidInput(
            "new Delivery must be pending or a projection dead letter, unattempted, and unleased"
                .into(),
        ));
    }
    let actual_sha = format!("{:x}", Sha256::digest(&delivery.payload_bytes));
    if delivery.payload_sha256 != actual_sha {
        return Err(EventRepoError::InvalidInput(
            "Delivery payload SHA-256 does not match payload bytes".into(),
        ));
    }
    Ok(())
}

pub(super) fn validate_completion(command: &CompleteEventDeliveryAttempt) -> Result<(), EventRepoError> {
    if command.delivery_id.is_empty()
        || command.expected_lease_owner.is_empty()
        || command.attempt_no == 0
    {
        return Err(EventRepoError::InvalidInput(
            "completion identifiers and attempt number must be non-empty".into(),
        ));
    }
    if command.completed_at_ms < command.started_at_ms {
        return Err(EventRepoError::InvalidInput(
            "attempt completion cannot precede its start".into(),
        ));
    }
    if command
        .error_summary
        .as_ref()
        .is_some_and(|summary| summary.len() > 2_048)
    {
        return Err(EventRepoError::InvalidInput(
            "error summary exceeds 2048 bytes".into(),
        ));
    }
    if command
        .error_category
        .as_ref()
        .is_some_and(|category| category.len() > 128)
    {
        return Err(EventRepoError::InvalidInput(
            "error category exceeds 128 bytes".into(),
        ));
    }
    let valid = match command.result {
        EventDeliveryAttemptRecordResult::Success => {
            command.next_status == EventDeliveryStatus::Succeeded
                && command.next_attempt_at_ms.is_none()
        }
        EventDeliveryAttemptRecordResult::Retryable => {
            (command.next_status == EventDeliveryStatus::RetryWait
                && command
                    .next_attempt_at_ms
                    .is_some_and(|next| next > command.completed_at_ms))
                || (command.next_status == EventDeliveryStatus::DeadLettered
                    && command.next_attempt_at_ms.is_none())
        }
        EventDeliveryAttemptRecordResult::Terminal => {
            command.next_status == EventDeliveryStatus::DeadLettered
                && command.next_attempt_at_ms.is_none()
        }
    };
    if !valid {
        return Err(EventRepoError::InvalidInput(
            "Attempt result and next Delivery state are inconsistent".into(),
        ));
    }
    Ok(())
}

pub(super) fn validate_list_limit(limit: u32, kind: &str) -> Result<(), EventRepoError> {
    if limit == 0 || limit > 100 {
        return Err(EventRepoError::InvalidInput(format!(
            "{kind} list limit must be between 1 and 100"
        )));
    }
    Ok(())
}

pub(super) fn validate_replay(command: &CreateEventReplayTarget) -> Result<(), EventRepoError> {
    if command.original_delivery_id.is_empty()
        || command.subscription_id.is_empty()
        || command.replay_request_id.is_empty()
        || command.target_id.is_empty()
        || command.actor.id.is_empty()
        || command.env.is_empty()
        || command.subscription_revision == 0
    {
        return Err(EventRepoError::InvalidInput(
            "replay identifiers, revision, and env must be non-empty".into(),
        ));
    }
    if command
        .reason
        .as_ref()
        .is_some_and(|reason| reason.len() > 128)
    {
        return Err(EventRepoError::InvalidInput(
            "replay reason exceeds 128 bytes".into(),
        ));
    }
    Ok(())
}

pub(super) fn deterministic_target_id(
    env: &str,
    event_id: &str,
    subscription_id: &str,
    revision: u64,
    purpose: EventFanoutTargetPurpose,
) -> String {
    let digest = Sha256::digest(
        format!("{env}\0{event_id}\0{subscription_id}\0{revision}\0{purpose:?}").as_bytes(),
    );
    format!("evtgt_{digest:x}")
}
pub(super) fn validate_new_subscription(record: &CreateEventSubscriptionRecord) -> Result<(), EventRepoError> {
    validate_scope(&record.subscription.scope).map_err(EventRepoError::InvalidInput)?;
    if record.subscription.subscription_id.is_empty() || record.subscription.env.is_empty() {
        return Err(EventRepoError::InvalidInput(
            "subscription id and env must be non-empty".to_string(),
        ));
    }
    if record.subscription.current_revision != 1
        || record.revision.revision != 1
        || record.revision.subscription_id != record.subscription.subscription_id
    {
        return Err(EventRepoError::InvalidInput(
            "new subscription must contain matching immutable revision 1".to_string(),
        ));
    }
    if record.scope_limit == 0 {
        return Err(EventRepoError::InvalidInput(
            "subscription scope limit must be non-zero".to_string(),
        ));
    }
    Ok(())
}

pub(super) fn validate_replacement(command: &ReplaceEventSubscriptionRevision) -> Result<(), EventRepoError> {
    if command.expected_revision == u64::MAX
        || command.revision.revision != command.expected_revision + 1
        || command.revision.subscription_id != command.subscription_id
    {
        return Err(EventRepoError::InvalidInput(
            "replacement must contain the next immutable revision".to_string(),
        ));
    }
    Ok(())
}

pub(super) fn validate_event_command(command: &AppendEventRecord) -> Result<(), EventRepoError> {
    for (name, value) in [
        ("env", command.env.as_str()),
        ("event_id", command.event.event_id.as_str()),
        ("event_type", command.event.event_type.as_str()),
        ("producer", command.event.producer.as_str()),
        ("producer_key", command.event.producer_key.as_str()),
        ("stream_key", command.event.stream_key.as_str()),
    ] {
        if value.is_empty() {
            return Err(EventRepoError::InvalidInput(format!(
                "{name} must be non-empty"
            )));
        }
    }
    Ok(())
}

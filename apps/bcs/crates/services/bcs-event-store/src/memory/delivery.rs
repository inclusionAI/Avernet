//! In-memory Event delivery claim, completion and replay.

use super::*;

impl MemoryEventStore {
    pub(super) async fn claim_deliveries(
        &self,
        command: ClaimEventDeliveries,
    ) -> Result<Vec<EventDeliveryRecord>, EventRepoError> {
        validate_claim(
            &command.worker_id,
            command.now_ms,
            command.lease_until_ms,
            command.limit,
            &command.env,
        )?;
        let _claim_guard = self
            .lock_named(format!("delivery-claim:{}", command.env))
            .await;
        let lease_owner = claim_owner(&command.worker_id);
        let mut state = self.state.write().await;
        let mut candidate_ids = state
            .deliveries
            .values()
            .filter(|delivery| delivery.env == command.env)
            .filter(|delivery| delivery_is_due(delivery, command.now_ms))
            .map(|delivery| {
                (
                    delivery.created_at_ms,
                    delivery.sequence,
                    delivery.delivery_id.clone(),
                )
            })
            .collect::<Vec<_>>();
        candidate_ids.sort();

        let mut claimed = Vec::new();
        for (_, _, delivery_id) in candidate_ids {
            if claimed.len() == command.limit as usize {
                break;
            }
            if !delivery_is_eligible(&state, &delivery_id, command.now_ms)? {
                continue;
            }
            let next_attempt_no = state
                .deliveries
                .get(&delivery_id)
                .ok_or_else(|| {
                    EventRepoError::Storage(
                        "candidate Delivery disappeared while state was write locked".into(),
                    )
                })?
                .attempt_count
                .checked_add(1)
                .ok_or_else(|| {
                    EventRepoError::Conflict("Delivery attempt counter overflow".into())
                })?;
            if state
                .attempts
                .get(&delivery_id)
                .is_some_and(|attempts| attempts.contains_key(&next_attempt_no))
            {
                return Err(EventRepoError::Conflict(format!(
                    "attempt {next_attempt_no} already exists for Delivery {delivery_id}"
                )));
            }
            let (abandoned_attempt_no, claimed_delivery) = {
                let delivery = state.deliveries.get_mut(&delivery_id).ok_or_else(|| {
                    EventRepoError::Storage(
                        "candidate Delivery disappeared while state was write locked".into(),
                    )
                })?;
                let abandoned_attempt_no = (delivery.status == EventDeliveryStatus::InFlight)
                    .then_some(delivery.attempt_count);
                delivery.status = EventDeliveryStatus::InFlight;
                delivery.attempt_count = next_attempt_no;
                delivery.first_attempt_at_ms.get_or_insert(command.now_ms);
                delivery.last_attempt_at_ms = Some(command.now_ms);
                delivery.next_attempt_at_ms = None;
                delivery.lease_owner = Some(lease_owner.clone());
                delivery.lease_until_ms = Some(command.lease_until_ms);
                (abandoned_attempt_no, delivery.clone())
            };
            let attempts = state.attempts.entry(delivery_id.clone()).or_default();
            if let Some(attempt_no) = abandoned_attempt_no
                && let Some(attempt) = attempts.get_mut(&attempt_no)
                && attempt.completed_at_ms.is_none()
            {
                attempt.completed_at_ms = Some(command.now_ms);
                attempt.result = Some(EventDeliveryAttemptRecordResult::Retryable);
                attempt.error_category = Some("lease_expired".to_string());
                attempt.error_summary = Some(
                    "Delivery lease expired before completion; remote outcome is unknown"
                        .to_string(),
                );
                attempt.response_bytes_observed = Some(0);
            }
            attempts.insert(
                claimed_delivery.attempt_count,
                EventDeliveryAttemptRecord {
                    delivery_id: delivery_id.clone(),
                    attempt_no: claimed_delivery.attempt_count,
                    started_at_ms: command.now_ms,
                    completed_at_ms: None,
                    latency_ms: None,
                    result: None,
                    http_status: None,
                    error_category: None,
                    error_summary: None,
                    response_bytes_observed: None,
                    worker_id: command.worker_id.clone(),
                },
            );
            claimed.push(claimed_delivery);
        }
        Ok(claimed)
    }

    pub(super) async fn complete_delivery_attempt(
        &self,
        command: CompleteEventDeliveryAttempt,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        validate_completion(&command)?;
        let _delivery_guard = self
            .lock_named(format!("delivery:{}", command.delivery_id))
            .await;
        let mut state = self.state.write().await;
        let existing = state
            .deliveries
            .get(&command.delivery_id)
            .cloned()
            .ok_or_else(|| EventRepoError::NotFound(command.delivery_id.clone()))?;
        if existing.status != EventDeliveryStatus::InFlight
            || existing.lease_owner.as_deref() != Some(command.expected_lease_owner.as_str())
            || existing
                .lease_until_ms
                .is_none_or(|lease_until| lease_until <= command.completed_at_ms)
            || existing.attempt_count != command.attempt_no
        {
            return Err(EventRepoError::LeaseLost(command.delivery_id));
        }
        let attempts = state
            .attempts
            .get_mut(&command.delivery_id)
            .ok_or_else(|| {
                EventRepoError::Storage(format!(
                    "active attempt {} is missing for Delivery {}",
                    command.attempt_no, command.delivery_id
                ))
            })?;
        let attempt = attempts.get_mut(&command.attempt_no).ok_or_else(|| {
            EventRepoError::Storage(format!(
                "active attempt {} is missing for Delivery {}",
                command.attempt_no, command.delivery_id
            ))
        })?;
        if attempt.completed_at_ms.is_some() {
            return Err(EventRepoError::Conflict(format!(
                "attempt {} is already complete for Delivery {}",
                command.attempt_no, command.delivery_id
            )));
        }
        attempt.started_at_ms = command.started_at_ms;
        attempt.completed_at_ms = Some(command.completed_at_ms);
        attempt.latency_ms = Some(command.completed_at_ms - command.started_at_ms);
        attempt.result = Some(command.result);
        attempt.http_status = command.http_status;
        attempt.error_category = command.error_category.clone();
        attempt.error_summary = command.error_summary.clone();
        attempt.response_bytes_observed = Some(command.response_bytes_observed);
        let result = {
            let delivery = state
                .deliveries
                .get_mut(&command.delivery_id)
                .ok_or_else(|| {
                    EventRepoError::Storage(
                        "Delivery disappeared while state was write locked".into(),
                    )
                })?;
            delivery.status = command.next_status;
            delivery.last_attempt_at_ms = Some(command.completed_at_ms);
            delivery.next_attempt_at_ms = command.next_attempt_at_ms;
            delivery.lease_owner = None;
            delivery.lease_until_ms = None;
            delivery.last_http_status = command.http_status;
            delivery.last_error_category = command.error_category;
            delivery.last_error_summary = command.error_summary;
            if command.next_status == EventDeliveryStatus::Succeeded {
                delivery.succeeded_at_ms = Some(command.completed_at_ms);
            }
            if command.next_status == EventDeliveryStatus::DeadLettered {
                delivery.dead_lettered_at_ms = Some(command.completed_at_ms);
            }
            delivery.clone()
        };
        if command.next_status == EventDeliveryStatus::Succeeded
            && let Some(original_id) = result.replay_of_delivery_id.as_ref()
            && let Some(original) = state.deliveries.get_mut(original_id)
            && original.status == EventDeliveryStatus::DeadLettered
            && original.resolved_by_delivery_id.is_none()
        {
            original.resolved_by_delivery_id = Some(result.delivery_id.clone());
            original.resolved_at_ms = Some(command.completed_at_ms);
        }
        Ok(result)
    }

    pub(super) async fn create_replay_target(
        &self,
        command: CreateEventReplayTarget,
    ) -> Result<EventFanoutTargetRecord, EventRepoError> {
        validate_replay(&command)?;
        let _delivery_guard = self
            .lock_named(format!("delivery:{}", command.original_delivery_id))
            .await;
        let mut state = self.state.write().await;
        if let Some(existing) = replay_target(&state, &command) {
            return Ok(existing);
        }
        let original = state
            .deliveries
            .get(&command.original_delivery_id)
            .filter(|delivery| delivery.env == command.env)
            .cloned()
            .ok_or_else(|| EventRepoError::NotFound(command.original_delivery_id.clone()))?;
        if original.subscription_id != command.subscription_id
            || original.status != EventDeliveryStatus::DeadLettered
            || original.resolved_by_delivery_id.is_some()
        {
            return Err(EventRepoError::Conflict(
                "only an unresolved dead-lettered Delivery can be replayed".into(),
            ));
        }
        if let Some(existing) = unresolved_replay_target(&state, &command.original_delivery_id) {
            return Ok(existing);
        }
        let subscription = state
            .subscriptions
            .get(&command.subscription_id)
            .filter(|stored| stored.record.env == command.env)
            .ok_or_else(|| EventRepoError::NotFound(command.subscription_id.clone()))?;
        if subscription.record.current_revision != command.subscription_revision
            || subscription.record.status != EventSubscriptionStatus::Active
        {
            return Err(EventRepoError::Conflict(
                "replay revision must be the current active revision".into(),
            ));
        }
        let event = state
            .events
            .get(&original.event_id)
            .cloned()
            .ok_or_else(|| EventRepoError::NotFound(original.event_id.clone()))?;
        if event.retention_until_ms <= command.created_at_ms {
            return Err(EventRepoError::Conflict(
                "Event payload retention has expired".into(),
            ));
        }
        let depends_on_target_id = resolve_causal_target(
            &mut state,
            &command.env,
            event.envelope.causation_event_id.as_deref(),
            &command.subscription_id,
            command.subscription_revision,
            command.created_at_ms,
        )?;
        if state.targets.contains_key(&command.target_id) {
            return Err(EventRepoError::Conflict(format!(
                "fanout target id {} already exists",
                command.target_id
            )));
        }
        let target = EventFanoutTargetRecord {
            target_id: command.target_id,
            event_id: original.event_id.clone(),
            subscription_id: command.subscription_id,
            subscription_revision: command.subscription_revision,
            purpose: EventFanoutTargetPurpose::ManualReplay,
            replay_request_id: Some(command.replay_request_id),
            replay_of_delivery_id: Some(original.delivery_id),
            depends_on_target_id,
            status: EventFanoutTargetStatus::Pending,
            created_at_ms: command.created_at_ms,
            materialized_at_ms: None,
            cancelled_at_ms: None,
            lease_owner: None,
            lease_until_ms: None,
            env: command.env,
        };
        state
            .targets
            .insert(target.target_id.clone(), target.clone());
        if let Some(event) = state.events.get_mut(&target.event_id) {
            event.fanout_status = EventFanoutStatus::Pending;
        }
        Ok(target)
    }
}

pub(super) fn delivery_is_due(delivery: &EventDeliveryRecord, now_ms: u64) -> bool {
    let lease_expired = delivery
        .lease_until_ms
        .is_none_or(|lease_until| lease_until <= now_ms);
    match delivery.status {
        EventDeliveryStatus::Pending => lease_expired,
        EventDeliveryStatus::RetryWait => {
            lease_expired
                && delivery
                    .next_attempt_at_ms
                    .is_some_and(|next_attempt| next_attempt <= now_ms)
        }
        EventDeliveryStatus::InFlight => lease_expired,
        EventDeliveryStatus::Succeeded
        | EventDeliveryStatus::DeadLettered
        | EventDeliveryStatus::Cancelled
        | EventDeliveryStatus::Skipped => false,
    }
}

pub(super) fn delivery_is_eligible(
    state: &MemoryState,
    delivery_id: &str,
    now_ms: u64,
) -> Result<bool, EventRepoError> {
    let delivery = state
        .deliveries
        .get(delivery_id)
        .ok_or_else(|| EventRepoError::Storage("candidate Delivery disappeared".into()))?;
    if !delivery_is_due(delivery, now_ms) || !delivery_dependency_satisfied(state, delivery) {
        return Ok(false);
    }
    let revision_exists = state
        .subscriptions
        .get(&delivery.subscription_id)
        .and_then(|subscription| subscription.revisions.get(&delivery.subscription_revision))
        .is_some();
    if !revision_exists {
        return Err(EventRepoError::Storage(
            "Delivery Subscription revision is missing".into(),
        ));
    }
    Ok(!state.deliveries.values().any(|previous| {
        previous.env == delivery.env
            && previous.subscription_id == delivery.subscription_id
            && previous.stream_key == delivery.stream_key
            && previous.sequence < delivery.sequence
            && !delivery_unblocks_lane(previous)
    }))
}

pub(super) fn delivery_dependency_satisfied(state: &MemoryState, delivery: &EventDeliveryRecord) -> bool {
    let Some(dependency_id) = state
        .targets
        .get(&delivery.fanout_target_id)
        .and_then(|target| target.depends_on_target_id.as_ref())
    else {
        return true;
    };
    let Some(dependency_delivery) = state
        .delivery_by_target
        .get(dependency_id)
        .and_then(|delivery_id| state.deliveries.get(delivery_id))
    else {
        return false;
    };
    matches!(
        dependency_delivery.status,
        EventDeliveryStatus::Succeeded | EventDeliveryStatus::Skipped
    ) || (dependency_delivery.status == EventDeliveryStatus::DeadLettered
        && dependency_delivery.resolved_by_delivery_id.is_some())
}

pub(super) fn delivery_unblocks_lane(delivery: &EventDeliveryRecord) -> bool {
    delivery.status.unblocks_strict_lane()
        || (delivery.status == EventDeliveryStatus::DeadLettered
            && delivery.resolved_by_delivery_id.is_some())
}

pub(super) fn replay_target(
    state: &MemoryState,
    command: &CreateEventReplayTarget,
) -> Option<EventFanoutTargetRecord> {
    state
        .targets
        .values()
        .find(|target| {
            target.env == command.env
                && target.subscription_id == command.subscription_id
                && target.subscription_revision == command.subscription_revision
                && target.purpose == EventFanoutTargetPurpose::ManualReplay
                && target.replay_request_id.as_deref() == Some(&command.replay_request_id)
                && target.replay_of_delivery_id.as_deref() == Some(&command.original_delivery_id)
        })
        .cloned()
}

pub(super) fn unresolved_replay_target(
    state: &MemoryState,
    original_delivery_id: &str,
) -> Option<EventFanoutTargetRecord> {
    state
        .targets
        .values()
        .find(|target| {
            if target.purpose != EventFanoutTargetPurpose::ManualReplay
                || target.replay_of_delivery_id.as_deref() != Some(original_delivery_id)
            {
                return false;
            }
            match target.status {
                EventFanoutTargetStatus::Pending => true,
                EventFanoutTargetStatus::Materialized => state
                    .delivery_by_target
                    .get(&target.target_id)
                    .and_then(|delivery_id| state.deliveries.get(delivery_id))
                    .is_some_and(|delivery| !delivery.status.is_attempt_terminal()),
                EventFanoutTargetStatus::Cancelled | EventFanoutTargetStatus::Failed => false,
            }
        })
        .cloned()
}

//! In-memory Event fanout, causality and target creation helpers.

use sha2::{Digest, Sha256};

use super::*;

pub(super) fn append_event_to_state(
    state: &mut MemoryState,
    command: &AppendEventRecord,
) -> Result<AppendEventRecordResult, EventRepoError> {
    let producer_key = (
        command.env.clone(),
        command.event.producer.clone(),
        command.event.producer_key.clone(),
        command.event.event_type.clone(),
    );
    if let Some(existing) = existing_by_producer(state, &producer_key) {
        return Ok(AppendEventRecordResult {
            fanout_target_ids: target_ids_for_event(
                state,
                &command.env,
                &existing.envelope.event_id,
            ),
            event: existing,
            deduplicated: true,
        });
    }
    if state.events.contains_key(&command.event.event_id) {
        return Err(EventRepoError::Conflict(format!(
            "event id {} already exists",
            command.event.event_id
        )));
    }
    validate_causation(state, command)?;
    let recorded_at_ms = parse_timestamp_ms(&command.recorded_at)?;
    let stream_key = (command.env.clone(), command.event.stream_key.clone());
    let sequence = state
        .stream_sequences
        .get(&stream_key)
        .copied()
        .unwrap_or(0)
        + 1;
    let envelope = EventEnvelope {
        spec_version: EVENT_SPEC_VERSION.to_string(),
        event_id: command.event.event_id.clone(),
        event_type: command.event.event_type.clone(),
        schema_version: command.event.schema_version.clone(),
        source: EVENT_SOURCE.to_string(),
        occurred_at: command.event.occurred_at.clone(),
        recorded_at: command.recorded_at.clone(),
        subject: command.event.subject.clone(),
        scope: command.event.scope.clone(),
        stream: EventStream {
            key: command.event.stream_key.clone(),
            sequence,
        },
        actor: command.event.actor.clone(),
        correlation_id: command.event.correlation_id.clone(),
        causation_event_id: command.event.causation_event_id.clone(),
        trace_id: command.event.trace_id.clone(),
        data: command.event.data.clone(),
    };
    let matching = state
        .subscriptions
        .values()
        .filter_map(|stored| {
            let revision = stored.revisions.get(&stored.record.current_revision)?;
            (stored.record.env == command.env
                && stored.record.status == EventSubscriptionStatus::Active
                && subscription_scope_matches(&stored.record.scope, &command.event.scope)
                && revision
                    .event_filters
                    .iter()
                    .any(|filter| event_filter_matches(filter, &command.event.event_type)))
            .then(|| (stored.record.subscription_id.clone(), revision.revision))
        })
        .collect::<Vec<_>>();
    let event = EventRecord {
        envelope,
        producer: command.event.producer.clone(),
        producer_key: command.event.producer_key.clone(),
        fanout_status: if matching.is_empty() {
            EventFanoutStatus::Completed
        } else {
            EventFanoutStatus::Pending
        },
        retention_until_ms: command.retention_until_ms,
        env: command.env.clone(),
    };
    let mut fanout_target_ids = Vec::with_capacity(matching.len());
    for (subscription_id, subscription_revision) in matching {
        let depends_on_target_id = resolve_causal_target(
            state,
            &command.env,
            command.event.causation_event_id.as_deref(),
            &subscription_id,
            subscription_revision,
            recorded_at_ms,
        )?;
        let target_id = target_id(
            &command.env,
            &event.envelope.event_id,
            &subscription_id,
            subscription_revision,
            EventFanoutTargetPurpose::Normal,
        );
        fanout_target_ids.push(target_id.clone());
        state.targets.insert(
            target_id.clone(),
            EventFanoutTargetRecord {
                target_id,
                event_id: event.envelope.event_id.clone(),
                subscription_id,
                subscription_revision,
                purpose: EventFanoutTargetPurpose::Normal,
                replay_request_id: None,
                replay_of_delivery_id: None,
                depends_on_target_id,
                status: EventFanoutTargetStatus::Pending,
                created_at_ms: recorded_at_ms,
                materialized_at_ms: None,
                cancelled_at_ms: None,
                lease_owner: None,
                lease_until_ms: None,
                env: command.env.clone(),
            },
        );
    }
    state.stream_sequences.insert(stream_key, sequence);
    state
        .producers
        .insert(producer_key, event.envelope.event_id.clone());
    state
        .events
        .insert(event.envelope.event_id.clone(), event.clone());
    Ok(AppendEventRecordResult {
        event,
        fanout_target_ids,
        deduplicated: false,
    })
}

pub(super) fn validate_causation(
    state: &MemoryState,
    command: &AppendEventRecord,
) -> Result<(), EventRepoError> {
    let Some(cause_id) = command.event.causation_event_id.as_ref() else {
        return Ok(());
    };
    if cause_id == &command.event.event_id {
        return Err(EventRepoError::CausationViolation(
            "event cannot cause itself".to_string(),
        ));
    }
    if state
        .events
        .get(cause_id)
        .is_none_or(|cause| cause.env != command.env)
    {
        return Err(EventRepoError::CausationViolation(format!(
            "causation event {cause_id} must already exist in the same environment"
        )));
    }
    Ok(())
}

pub(super) fn existing_by_producer(state: &MemoryState, key: &ProducerKey) -> Option<EventRecord> {
    let event_id = state.producers.get(key)?;
    state
        .events
        .get(event_id)
        .filter(|event| event.env == key.0)
        .cloned()
}

pub(super) fn target_ids_for_event(state: &MemoryState, env: &str, event_id: &str) -> Vec<String> {
    let mut ids = state
        .targets
        .values()
        .filter(|target| target.env == env && target.event_id == event_id)
        .map(|target| target.target_id.clone())
        .collect::<Vec<_>>();
    ids.sort();
    ids
}

pub(super) fn target_id(
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

pub(super) fn backfill_group_provisioning_targets(
    state: &mut MemoryState,
    group_id: &str,
    subscription_ids: &[String],
    finalized_at_ms: u64,
    env: &str,
) -> Result<(), EventRepoError> {
    let subscriptions = subscription_ids
        .iter()
        .map(|subscription_id| {
            let stored = state.subscriptions.get(subscription_id).ok_or_else(|| {
                EventRepoError::NotFound(format!(
                    "active subscription {subscription_id} was not found"
                ))
            })?;
            let revision = stored
                .revisions
                .get(&stored.record.current_revision)
                .ok_or_else(|| {
                    EventRepoError::Storage(format!(
                        "subscription {subscription_id} current revision is missing"
                    ))
                })?;
            Ok((
                subscription_id.clone(),
                revision.revision,
                stored.record.created_at_ms,
                revision.event_filters.clone(),
            ))
        })
        .collect::<Result<Vec<_>, EventRepoError>>()?;
    let mut events = state
        .events
        .values()
        .filter(|event| {
            event.env == env
                && event.envelope.scope.group_id.as_deref() == Some(group_id)
                && event.retention_until_ms > finalized_at_ms
        })
        .map(|event| {
            Ok((
                parse_timestamp_ms(&event.envelope.recorded_at)?,
                event.clone(),
            ))
        })
        .collect::<Result<Vec<_>, EventRepoError>>()?;
    events.sort_by(|left, right| {
        left.0
            .cmp(&right.0)
            .then_with(|| left.1.envelope.stream.key.cmp(&right.1.envelope.stream.key))
            .then_with(|| {
                left.1
                    .envelope
                    .stream
                    .sequence
                    .cmp(&right.1.envelope.stream.sequence)
            })
            .then_with(|| left.1.envelope.event_id.cmp(&right.1.envelope.event_id))
    });

    for (subscription_id, revision, created_at_ms, filters) in subscriptions {
        let matching_events = events
            .iter()
            .filter(|(recorded_at_ms, event)| {
                *recorded_at_ms >= created_at_ms
                    && *recorded_at_ms <= finalized_at_ms
                    && filters
                        .iter()
                        .any(|filter| event_filter_matches(filter, &event.envelope.event_type))
            })
            .collect::<Vec<_>>();
        for (recorded_at_ms, event) in &matching_events {
            let normal_target_id = target_id(
                env,
                &event.envelope.event_id,
                &subscription_id,
                revision,
                EventFanoutTargetPurpose::Normal,
            );
            if state.targets.contains_key(&normal_target_id) {
                continue;
            }
            state.targets.insert(
                normal_target_id.clone(),
                EventFanoutTargetRecord {
                    target_id: normal_target_id,
                    event_id: event.envelope.event_id.clone(),
                    subscription_id: subscription_id.clone(),
                    subscription_revision: revision,
                    purpose: EventFanoutTargetPurpose::Normal,
                    replay_request_id: None,
                    replay_of_delivery_id: None,
                    depends_on_target_id: None,
                    status: EventFanoutTargetStatus::Pending,
                    created_at_ms: *recorded_at_ms,
                    materialized_at_ms: None,
                    cancelled_at_ms: None,
                    lease_owner: None,
                    lease_until_ms: None,
                    env: env.to_string(),
                },
            );
            if let Some(stored_event) = state.events.get_mut(&event.envelope.event_id) {
                stored_event.fanout_status = EventFanoutStatus::Pending;
            }
        }
        for (recorded_at_ms, event) in matching_events {
            let depends_on_target_id = resolve_causal_target(
                state,
                env,
                event.envelope.causation_event_id.as_deref(),
                &subscription_id,
                revision,
                *recorded_at_ms,
            )?;
            let normal_target_id = target_id(
                env,
                &event.envelope.event_id,
                &subscription_id,
                revision,
                EventFanoutTargetPurpose::Normal,
            );
            let normal_target = state.targets.get_mut(&normal_target_id).ok_or_else(|| {
                EventRepoError::Storage(format!(
                    "backfilled normal target {normal_target_id} is missing"
                ))
            })?;
            if normal_target.depends_on_target_id.is_none() {
                normal_target.depends_on_target_id = depends_on_target_id;
            }
        }
    }
    Ok(())
}

pub(super) fn resolve_causal_target(
    state: &mut MemoryState,
    env: &str,
    cause_event_id: Option<&str>,
    subscription_id: &str,
    subscription_revision: u64,
    created_at_ms: u64,
) -> Result<Option<String>, EventRepoError> {
    let Some(cause_event_id) = cause_event_id else {
        return Ok(None);
    };
    let cause = state
        .events
        .get(cause_event_id)
        .filter(|event| event.env == env)
        .cloned()
        .ok_or_else(|| {
            EventRepoError::CausationViolation(format!(
                "causation event {cause_event_id} must exist in the same environment"
            ))
        })?;
    let revision = state
        .subscriptions
        .get(subscription_id)
        .and_then(|subscription| subscription.revisions.get(&subscription_revision))
        .ok_or_else(|| EventRepoError::Storage("causal Subscription revision is missing".into()))?;
    if !revision
        .event_filters
        .iter()
        .any(|filter| event_filter_matches(filter, &cause.envelope.event_type))
    {
        return Ok(None);
    }

    let mut existing = state
        .targets
        .values()
        .filter(|target| {
            target.env == env
                && target.event_id == cause_event_id
                && target.subscription_id == subscription_id
                && target.subscription_revision == subscription_revision
                && target.status != EventFanoutTargetStatus::Cancelled
        })
        .cloned()
        .collect::<Vec<_>>();
    existing.sort_by_key(|target| match target.purpose {
        EventFanoutTargetPurpose::Normal => 0,
        EventFanoutTargetPurpose::CausalPrerequisite => 1,
        EventFanoutTargetPurpose::ManualReplay => 2,
    });
    if let Some(target) = existing.first() {
        return Ok(Some(target.target_id.clone()));
    }

    let target_id = target_id(
        env,
        cause_event_id,
        subscription_id,
        subscription_revision,
        EventFanoutTargetPurpose::CausalPrerequisite,
    );
    if state.targets.contains_key(&target_id) {
        return Err(EventRepoError::Conflict(
            "causal prerequisite was previously cancelled for this revision".into(),
        ));
    }
    state.targets.insert(
        target_id.clone(),
        EventFanoutTargetRecord {
            target_id: target_id.clone(),
            event_id: cause_event_id.to_string(),
            subscription_id: subscription_id.to_string(),
            subscription_revision,
            purpose: EventFanoutTargetPurpose::CausalPrerequisite,
            replay_request_id: None,
            replay_of_delivery_id: None,
            depends_on_target_id: None,
            status: EventFanoutTargetStatus::Pending,
            created_at_ms,
            materialized_at_ms: None,
            cancelled_at_ms: None,
            lease_owner: None,
            lease_until_ms: None,
            env: env.to_string(),
        },
    );
    if let Some(cause) = state.events.get_mut(cause_event_id) {
        cause.fanout_status = EventFanoutStatus::Pending;
    }
    Ok(Some(target_id))
}

pub(super) fn complete_event_fanout_if_settled(state: &mut MemoryState, event_id: &str, env: &str) {
    let settled = state.targets.values().all(|target| {
        target.env != env
            || target.event_id != event_id
            || target.status != EventFanoutTargetStatus::Pending
    });
    if settled
        && let Some(event) = state.events.get_mut(event_id)
        && event.env == env
    {
        event.fanout_status = EventFanoutStatus::Completed;
    }
}

//! Input validation shared by the memory and SQL Event repositories.

use bcs_service_api::port::repo::{
    AppendEventRecord, CompleteEventDeliveryAttempt, CreateEventReplayTarget,
    CreateEventSubscriptionRecord, EventDeliveryAttemptRecordResult, EventRepoError,
    MaterializeFanoutTarget, RenewEventDeliveryLease, ReplaceEventSubscriptionRevision,
};
use bcs_service_api::types::EventDeliveryStatus;
use sha2::{Digest, Sha256};

use crate::validate_scope;

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

#[cfg(test)]
mod tests {
    use bcs_service_api::port::NewEvent;
    use bcs_service_api::port::repo::{
        EventDeliveryRecord, EventSubscriptionRecord, EventSubscriptionRevisionRecord,
    };
    use bcs_service_api::types::{
        EVENT_SCHEMA_VERSION_V1, EventActor, EventActorType, EventPayloadMode, EventScope,
        EventSubject, EventSubscriptionScope, EventSubscriptionScopeType, EventSubscriptionStatus,
    };

    use super::*;

    const ENV: &str = "validation";

    fn actor() -> EventActor {
        EventActor {
            actor_type: EventActorType::App,
            id: "app".to_string(),
            display_name: None,
        }
    }

    fn revision_record(subscription_id: &str, revision: u64) -> EventSubscriptionRevisionRecord {
        EventSubscriptionRevisionRecord {
            subscription_id: subscription_id.to_string(),
            revision,
            event_filters: vec!["group.*".to_string()],
            payload_mode: EventPayloadMode::MetadataOnly,
            endpoint_url: "https://events.example.com/validation".to_string(),
            request_timeout_ms: 10_000,
            activated_at_ms: 1_000,
            retired_at_ms: None,
        }
    }

    fn subscription_record() -> CreateEventSubscriptionRecord {
        CreateEventSubscriptionRecord {
            subscription: EventSubscriptionRecord {
                subscription_id: "sub".to_string(),
                name: "group events".to_string(),
                scope: EventSubscriptionScope {
                    scope_type: EventSubscriptionScopeType::Group,
                    id: "group".to_string(),
                },
                status: EventSubscriptionStatus::Active,
                current_revision: 1,
                created_by: actor(),
                created_at_ms: 1_000,
                updated_at_ms: 1_000,
                deleted_at_ms: None,
                env: ENV.to_string(),
            },
            revision: revision_record("sub", 1),
            scope_limit: 10,
        }
    }

    fn replacement() -> ReplaceEventSubscriptionRevision {
        ReplaceEventSubscriptionRevision {
            subscription_id: "sub".to_string(),
            expected_revision: 1,
            name: "group events".to_string(),
            status: EventSubscriptionStatus::Active,
            revision: revision_record("sub", 2),
            cancel_retired_pending_deliveries: false,
            actor: actor(),
            reason: None,
            updated_at_ms: 2_000,
            env: ENV.to_string(),
        }
    }

    fn append_record() -> AppendEventRecord {
        AppendEventRecord {
            event: NewEvent {
                event_id: "event".to_string(),
                event_type: "group.updated".to_string(),
                schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
                producer: "group-service".to_string(),
                producer_key: "group:key".to_string(),
                occurred_at: "2026-08-19T00:00:00.000Z".to_string(),
                subject: EventSubject {
                    subject_type: "group".to_string(),
                    id: "group".to_string(),
                },
                scope: EventScope {
                    group_id: Some("group".to_string()),
                    ..EventScope::default()
                },
                stream_key: "group:group".to_string(),
                actor: Some(actor()),
                correlation_id: None,
                causation_event_id: None,
                trace_id: None,
                data: std::collections::BTreeMap::new(),
            },
            recorded_at: "2026-08-19T00:00:01.000Z".to_string(),
            retention_until_ms: 2_000_000_000_000,
            env: ENV.to_string(),
        }
    }

    fn renewal() -> RenewEventDeliveryLease {
        RenewEventDeliveryLease {
            delivery_id: "delivery".to_string(),
            expected_lease_owner: "owner".to_string(),
            attempt_no: 1,
            now_ms: 1_000,
            lease_until_ms: 2_000,
            env: ENV.to_string(),
        }
    }

    fn delivery_record() -> EventDeliveryRecord {
        let payload_bytes = b"payload".to_vec();
        EventDeliveryRecord {
            delivery_id: "delivery".to_string(),
            fanout_target_id: "target".to_string(),
            event_id: "event".to_string(),
            event_type: "group.updated".to_string(),
            subscription_id: "sub".to_string(),
            subscription_revision: 1,
            stream_key: "group:group".to_string(),
            sequence: 1,
            payload_sha256: format!("{:x}", Sha256::digest(&payload_bytes)),
            payload_bytes,
            status: EventDeliveryStatus::Pending,
            attempt_count: 0,
            first_attempt_at_ms: None,
            last_attempt_at_ms: None,
            next_attempt_at_ms: None,
            lease_owner: None,
            lease_until_ms: None,
            last_http_status: None,
            last_error_category: None,
            last_error_summary: None,
            dead_lettered_at_ms: None,
            cancelled_at_ms: None,
            skipped_at_ms: None,
            skip_actor: None,
            skip_reason: None,
            replay_of_delivery_id: None,
            resolved_by_delivery_id: None,
            resolved_at_ms: None,
            created_at_ms: 1_000,
            succeeded_at_ms: None,
            env: ENV.to_string(),
        }
    }

    fn materialization() -> MaterializeFanoutTarget {
        MaterializeFanoutTarget {
            target_id: "target".to_string(),
            expected_lease_owner: "owner".to_string(),
            delivery: delivery_record(),
            materialized_at_ms: 1_000,
        }
    }

    fn completion() -> CompleteEventDeliveryAttempt {
        CompleteEventDeliveryAttempt {
            delivery_id: "delivery".to_string(),
            expected_lease_owner: "owner".to_string(),
            attempt_no: 1,
            started_at_ms: 1_000,
            completed_at_ms: 1_100,
            result: EventDeliveryAttemptRecordResult::Success,
            next_status: EventDeliveryStatus::Succeeded,
            next_attempt_at_ms: None,
            http_status: Some(204),
            error_category: None,
            error_summary: None,
            response_bytes_observed: 0,
        }
    }

    fn replay() -> CreateEventReplayTarget {
        CreateEventReplayTarget {
            original_delivery_id: "delivery".to_string(),
            subscription_id: "sub".to_string(),
            subscription_revision: 1,
            replay_request_id: "request".to_string(),
            target_id: "target".to_string(),
            actor: actor(),
            reason: None,
            created_at_ms: 1_000,
            env: ENV.to_string(),
        }
    }

    /// Panics on success or on an error variant that is not `InvalidInput`
    /// so a validation-order regression cannot pass as a message mismatch.
    fn message(result: Result<(), EventRepoError>) -> String {
        match result {
            Err(EventRepoError::InvalidInput(message)) => message,
            other => panic!("expected InvalidInput, got {other:?}"),
        }
    }

    #[test]
    fn invalid_claim_preserves_first_error() {
        assert!(
            matches!(validate_claim("", 5, 5, 0, ""),
                Err(EventRepoError::InvalidInput(s))
                    if s == "claim worker id and env must be non-empty")
        );
        assert!(validate_claim("worker", 5, 6, 1000, "dev").is_ok());
        assert!(validate_claim("worker", 5, 6, 1001, "dev").is_err());
        let long_worker = "w".repeat(201);
        assert_eq!(
            message(validate_claim(&long_worker, 5, 6, 10, "dev")),
            "claim worker id and env must be non-empty"
        );
        assert_eq!(
            message(validate_claim("worker", 5, 5, 10, "dev")),
            "claim lease must expire after now"
        );
        assert_eq!(
            message(validate_claim("worker", 5, 6, 0, "dev")),
            "claim limit must be between 1 and 1000"
        );
    }

    #[test]
    fn lease_renewal_rejects_lease_at_now_and_missing_identity() {
        assert!(validate_lease_renewal(&renewal()).is_ok());
        let mut command = renewal();
        command.lease_until_ms = command.now_ms;
        assert_eq!(
            message(validate_lease_renewal(&command)),
            "Delivery lease renewal is invalid"
        );
        let mut command = renewal();
        command.attempt_no = 0;
        assert_eq!(
            message(validate_lease_renewal(&command)),
            "Delivery lease renewal is invalid"
        );
        let mut command = renewal();
        command.env.clear();
        assert_eq!(
            message(validate_lease_renewal(&command)),
            "Delivery lease renewal is invalid"
        );
    }

    #[test]
    fn new_subscription_checks_scope_then_identity_then_revision_one() {
        let mut record = subscription_record();
        record.subscription.scope.id.clear();
        record.subscription.subscription_id.clear();
        record.subscription.env.clear();
        record.revision.revision = 7;
        assert_eq!(
            message(validate_new_subscription(&record)),
            "group scope must include a non-empty id"
        );
        let mut record = subscription_record();
        record.subscription.subscription_id.clear();
        record.revision.subscription_id.clear();
        record.revision.revision = 7;
        assert_eq!(
            message(validate_new_subscription(&record)),
            "subscription id and env must be non-empty"
        );
        let mut record = subscription_record();
        record.revision.revision = 7;
        assert_eq!(
            message(validate_new_subscription(&record)),
            "new subscription must contain matching immutable revision 1"
        );
        let mut record = subscription_record();
        record.subscription.current_revision = 7;
        assert_eq!(
            message(validate_new_subscription(&record)),
            "new subscription must contain matching immutable revision 1"
        );
        let mut record = subscription_record();
        record.revision.subscription_id = "other".to_string();
        assert_eq!(
            message(validate_new_subscription(&record)),
            "new subscription must contain matching immutable revision 1"
        );
        let mut record = subscription_record();
        record.scope_limit = 0;
        assert_eq!(
            message(validate_new_subscription(&record)),
            "subscription scope limit must be non-zero"
        );
    }

    #[test]
    fn materialization_rejects_payload_hash_mismatch_and_leased_state() {
        assert!(validate_materialization(&materialization()).is_ok());
        let mut command = materialization();
        command.delivery.payload_sha256 = "wrong-hash".to_string();
        assert_eq!(
            message(validate_materialization(&command)),
            "Delivery payload SHA-256 does not match payload bytes"
        );
        let mut command = materialization();
        command.delivery.attempt_count = 1;
        assert_eq!(
            message(validate_materialization(&command)),
            "new Delivery must be pending or a projection dead letter, unattempted, and unleased"
        );
        let mut command = materialization();
        command.delivery.lease_owner = Some("owner".to_string());
        assert_eq!(
            message(validate_materialization(&command)),
            "new Delivery must be pending or a projection dead letter, unattempted, and unleased"
        );
        let mut command = materialization();
        command.target_id.clear();
        assert_eq!(
            message(validate_materialization(&command)),
            "materialization identifiers and env must be non-empty"
        );
    }

    #[test]
    fn completion_rejects_retry_wait_without_future_retry_time() {
        assert!(validate_completion(&completion()).is_ok());
        let mut command = completion();
        command.result = EventDeliveryAttemptRecordResult::Retryable;
        command.next_status = EventDeliveryStatus::RetryWait;
        assert_eq!(
            message(validate_completion(&command)),
            "Attempt result and next Delivery state are inconsistent"
        );
        let mut command = completion();
        command.result = EventDeliveryAttemptRecordResult::Retryable;
        command.next_status = EventDeliveryStatus::RetryWait;
        command.next_attempt_at_ms = Some(command.completed_at_ms);
        assert_eq!(
            message(validate_completion(&command)),
            "Attempt result and next Delivery state are inconsistent"
        );
        let mut command = completion();
        command.result = EventDeliveryAttemptRecordResult::Retryable;
        command.next_status = EventDeliveryStatus::RetryWait;
        command.next_attempt_at_ms = Some(command.completed_at_ms + 1);
        assert!(validate_completion(&command).is_ok());
        let mut command = completion();
        command.completed_at_ms = command.started_at_ms - 1;
        assert_eq!(
            message(validate_completion(&command)),
            "attempt completion cannot precede its start"
        );
        let mut command = completion();
        command.attempt_no = 0;
        assert_eq!(
            message(validate_completion(&command)),
            "completion identifiers and attempt number must be non-empty"
        );
        let mut command = completion();
        command.error_summary = Some("x".repeat(2_049));
        assert_eq!(
            message(validate_completion(&command)),
            "error summary exceeds 2048 bytes"
        );
        let mut command = completion();
        command.error_category = Some("x".repeat(129));
        assert_eq!(
            message(validate_completion(&command)),
            "error category exceeds 128 bytes"
        );
    }

    #[test]
    fn list_limit_accepts_one_hundred_and_rejects_zero_and_above_one_hundred() {
        assert!(validate_list_limit(1, "Delivery").is_ok());
        assert!(validate_list_limit(100, "Delivery").is_ok());
        assert_eq!(
            message(validate_list_limit(0, "Delivery")),
            "Delivery list limit must be between 1 and 100"
        );
        assert_eq!(
            message(validate_list_limit(101, "Delivery")),
            "Delivery list limit must be between 1 and 100"
        );
    }

    #[test]
    fn replay_rejects_overlong_reason_and_missing_identifiers() {
        assert!(validate_replay(&replay()).is_ok());
        let mut command = replay();
        command.reason = Some("x".repeat(128));
        assert!(validate_replay(&command).is_ok());
        let mut command = replay();
        command.reason = Some("x".repeat(129));
        assert_eq!(
            message(validate_replay(&command)),
            "replay reason exceeds 128 bytes"
        );
        let mut command = replay();
        command.original_delivery_id.clear();
        command.subscription_revision = 0;
        assert_eq!(
            message(validate_replay(&command)),
            "replay identifiers, revision, and env must be non-empty"
        );
        let mut command = replay();
        command.subscription_revision = 0;
        assert_eq!(
            message(validate_replay(&command)),
            "replay identifiers, revision, and env must be non-empty"
        );
    }

    #[test]
    fn replacement_rejects_max_revision_and_non_next_revision() {
        assert!(validate_replacement(&replacement()).is_ok());
        let mut command = replacement();
        command.expected_revision = u64::MAX;
        assert_eq!(
            message(validate_replacement(&command)),
            "replacement must contain the next immutable revision"
        );
        let mut command = replacement();
        command.expected_revision = 2;
        assert_eq!(
            message(validate_replacement(&command)),
            "replacement must contain the next immutable revision"
        );
        let mut command = replacement();
        command.revision.subscription_id = "other".to_string();
        assert_eq!(
            message(validate_replacement(&command)),
            "replacement must contain the next immutable revision"
        );
    }

    fn assert_append_field_error(name: &str, command: AppendEventRecord) {
        assert_eq!(
            message(validate_event_command(&command)),
            format!("{name} must be non-empty")
        );
    }

    #[test]
    fn append_rejects_the_first_empty_field_by_name() {
        assert!(validate_event_command(&append_record()).is_ok());
        let mut command = append_record();
        command.env.clear();
        assert_append_field_error("env", command);
        let mut command = append_record();
        command.event.event_id.clear();
        assert_append_field_error("event_id", command);
        let mut command = append_record();
        command.event.event_type.clear();
        assert_append_field_error("event_type", command);
        let mut command = append_record();
        command.event.producer.clear();
        assert_append_field_error("producer", command);
        let mut command = append_record();
        command.event.producer_key.clear();
        assert_append_field_error("producer_key", command);
        let mut command = append_record();
        command.event.stream_key.clear();
        assert_append_field_error("stream_key", command);
    }
}

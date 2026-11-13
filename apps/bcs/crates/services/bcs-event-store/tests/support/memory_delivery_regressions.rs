use super::*;
use bcs_service_api::port::repo::*;
use bcs_service_api::types::EventDeliveryStatus;
use sha2::{Digest, Sha256};

fn delivery(target: &EventFanoutTargetRecord, event: &EventRecord, id: &str, now: u64) -> EventDeliveryRecord {
    let payload_bytes = serde_json::to_vec(&event.envelope).unwrap();
    let payload_sha256 = format!("{:x}", Sha256::digest(&payload_bytes));
    EventDeliveryRecord {
        delivery_id: id.into(), fanout_target_id: target.target_id.clone(), event_id: target.event_id.clone(),
        event_type: event.envelope.event_type.clone(), subscription_id: target.subscription_id.clone(),
        subscription_revision: target.subscription_revision, stream_key: event.envelope.stream.key.clone(),
        sequence: event.envelope.stream.sequence, payload_bytes, payload_sha256, status: EventDeliveryStatus::Pending,
        attempt_count: 0, first_attempt_at_ms: None, last_attempt_at_ms: None, next_attempt_at_ms: None,
        lease_owner: None, lease_until_ms: None, last_http_status: None, last_error_category: None, last_error_summary: None,
        dead_lettered_at_ms: None, cancelled_at_ms: None, skipped_at_ms: None, skip_actor: None, skip_reason: None,
        replay_of_delivery_id: None, resolved_by_delivery_id: None, resolved_at_ms: None, created_at_ms: now,
        succeeded_at_ms: None, env: common::ENV.into(),
    }
}

#[tokio::test]
async fn coverage_event_subscription_and_append_validation_never_publish_invalid_records() {
    use bcs_service_api::types::{EventSubscriptionScope, EventSubscriptionScopeType};
    let repo = MemoryEventStore::new();
    let valid = common::subscription("validated");
    let mut invalids = Vec::new();
    let mut record = valid.clone(); record.subscription.env.clear(); invalids.push(record);
    let mut record = valid.clone(); record.revision.revision = 2; invalids.push(record);
    let mut record = valid.clone(); record.scope_limit = 0; invalids.push(record);
    let mut record = valid.clone(); record.subscription.scope.id.clear(); invalids.push(record);
    for record in invalids {
        assert!(matches!(repo.create_subscription(record).await, Err(EventRepoError::InvalidInput(_))));
        assert!(repo.get_subscription("validated", common::ENV).await.unwrap().is_none());
    }
    repo.create_subscription(valid).await.unwrap();
    let replacement = ReplaceEventSubscriptionRevision { subscription_id: "validated".into(), expected_revision: u64::MAX,
        name: "invalid revision".into(), status: EventSubscriptionStatus::Active,
        revision: common::revision("validated", 2, vec!["group.*"]), cancel_retired_pending_deliveries: false,
        actor: common::actor(), reason: None, updated_at_ms: 1_755_561_600_100, env: common::ENV.into() };
    assert!(matches!(repo.replace_subscription_revision(replacement.clone()).await, Err(EventRepoError::InvalidInput(_))));
    let mut missing_id = replacement; missing_id.subscription_id.clear();
    assert!(matches!(repo.replace_subscription_revision(missing_id).await, Err(EventRepoError::InvalidInput(_))));
    assert_eq!(repo.get_subscription("validated", common::ENV).await.unwrap().unwrap().0.current_revision, 1);
    let mut invalids = Vec::new();
    let valid = common::append("valid-event", "valid-key", "group.updated");
    let mut record = valid.clone(); record.event.producer.clear(); invalids.push(record);
    let mut record = valid.clone(); record.recorded_at = "not a timestamp".into(); invalids.push(record);
    let mut record = valid.clone(); record.event.occurred_at = "1969-12-31T23:59:59Z".into(); invalids.push(record);
    for record in invalids {
        assert!(matches!(repo.append_event(record).await, Err(EventRepoError::InvalidInput(_))));
        assert!(repo.get_event("valid-event", common::ENV).await.unwrap().is_none());
    }
    let mut self_cause = valid.clone(); self_cause.event.causation_event_id = Some("valid-event".into());
    assert!(matches!(repo.append_event(self_cause).await, Err(EventRepoError::CausationViolation(_))));
    let mut absent_cause = valid.clone(); absent_cause.event.causation_event_id = Some("absent-cause".into());
    assert!(matches!(repo.append_event(absent_cause).await, Err(EventRepoError::CausationViolation(_))));
    assert_eq!(repo.append_event(valid).await.unwrap().event.envelope.stream.sequence, 1, "validation failures must not allocate sequence numbers");
    for limit in [0, 101] {
        assert!(matches!(repo.list_subscriptions(ListEventSubscriptionRecords { scope: Some(EventSubscriptionScope {
            scope_type: EventSubscriptionScopeType::Group, id: common::GROUP_ID.into() }), status: None,
            after_subscription_id: None, limit, env: common::ENV.into() }).await, Err(EventRepoError::InvalidInput(_))));
    }
}

#[tokio::test]
async fn coverage_event_delivery_validation_preserves_target_and_inflight_attempt() {
    let repo = MemoryEventStore::new();
    repo.create_subscription(common::subscription("validated-delivery")).await.unwrap();
    let event = repo.append_event(common::append("validated-event", "validated-key", "group.created")).await.unwrap().event;
    let now = 1_755_561_600_010;
    let claim = ClaimFanoutTargets { worker_id: "worker".into(), now_ms: now, lease_until_ms: now + 1000, limit: 1, env: common::ENV.into() };
    for (worker, limit, lease) in [("", 1, now + 1000), ("worker", 0, now + 1000), ("worker", 1, now)] {
        let mut invalid = claim.clone(); invalid.worker_id = worker.into(); invalid.limit = limit; invalid.lease_until_ms = lease;
        assert!(matches!(repo.claim_fanout_targets(invalid).await, Err(EventRepoError::InvalidInput(_))));
    }
    let target = repo.claim_fanout_targets(claim).await.unwrap().remove(0);
    let materialize = MaterializeFanoutTarget { target_id: target.target_id.clone(), expected_lease_owner: target.lease_owner.clone().unwrap(),
        delivery: delivery(&target, &event, "validated-delivery", now), materialized_at_ms: now };
    let mut invalids = Vec::new();
    let mut invalid = materialize.clone(); invalid.expected_lease_owner.clear(); invalids.push(invalid);
    let mut invalid = materialize.clone(); invalid.delivery.status = EventDeliveryStatus::Succeeded; invalids.push(invalid);
    let mut invalid = materialize.clone(); invalid.delivery.payload_sha256 = "wrong-hash".into(); invalids.push(invalid);
    for invalid in invalids {
        assert!(matches!(repo.materialize_fanout_target(invalid).await, Err(EventRepoError::InvalidInput(_))));
        assert!(repo.get_delivery("validated-delivery", common::ENV).await.unwrap().is_none());
    }
    repo.materialize_fanout_target(materialize).await.unwrap();
    let inflight = repo.claim_deliveries(ClaimEventDeliveries { worker_id: "sender".into(), now_ms: now + 1,
        lease_until_ms: now + 1000, limit: 1, env: common::ENV.into() }).await.unwrap().remove(0);
    let valid = CompleteEventDeliveryAttempt { delivery_id: inflight.delivery_id.clone(), expected_lease_owner: inflight.lease_owner.clone().unwrap(),
        attempt_no: inflight.attempt_count, started_at_ms: now + 1, completed_at_ms: now + 2, result: EventDeliveryAttemptRecordResult::Success,
        next_status: EventDeliveryStatus::Succeeded, next_attempt_at_ms: None, http_status: Some(204), error_category: None, error_summary: None, response_bytes_observed: 0 };
    let mut invalids = Vec::new();
    let mut invalid = valid.clone(); invalid.attempt_no = 0; invalids.push(invalid);
    let mut invalid = valid.clone(); invalid.completed_at_ms = now; invalids.push(invalid);
    let mut invalid = valid.clone(); invalid.error_summary = Some("x".repeat(2049)); invalids.push(invalid);
    let mut invalid = valid.clone(); invalid.error_category = Some("x".repeat(129)); invalids.push(invalid);
    let mut invalid = valid.clone(); invalid.next_status = EventDeliveryStatus::Pending; invalids.push(invalid);
    for invalid in invalids {
        assert!(matches!(repo.complete_delivery_attempt(invalid).await, Err(EventRepoError::InvalidInput(_))));
        let state = repo.get_delivery("validated-delivery", common::ENV).await.unwrap().unwrap().0;
        assert_eq!(state.status, EventDeliveryStatus::InFlight);
        assert_eq!(state.attempt_count, 1);
    }
    let invalid_renewal = RenewEventDeliveryLease { delivery_id: inflight.delivery_id, expected_lease_owner: inflight.lease_owner.unwrap(),
        attempt_no: 1, now_ms: now + 3, lease_until_ms: now + 3, env: common::ENV.into() };
    assert!(matches!(repo.renew_delivery_lease(invalid_renewal).await, Err(EventRepoError::InvalidInput(_))));
    assert_eq!(repo.complete_delivery_attempt(valid).await.unwrap().status, EventDeliveryStatus::Succeeded);
}

#[tokio::test]
async fn coverage_group_deletion_cancels_unsent_work_but_preserves_inflight_leases() {
    let repo = MemoryEventStore::new();
    for id in ["first-sub", "second-sub", "third-sub"] { repo.create_subscription(common::subscription(id)).await.unwrap(); }
    let event = repo.append_event(common::append("delivery-event", "delivery-key", "group.created")).await.unwrap().event;
    let now = 1_755_561_600_010;
    let targets = repo.claim_fanout_targets(ClaimFanoutTargets { worker_id: "worker".into(), now_ms: now,
        lease_until_ms: now + 1000, limit: 3, env: common::ENV.into() }).await.unwrap();
    assert_eq!(targets.len(), 3);
    for (i, target) in targets.iter().take(2).enumerate() {
        repo.materialize_fanout_target(MaterializeFanoutTarget {
            target_id: target.target_id.clone(), expected_lease_owner: target.lease_owner.clone().unwrap(),
            delivery: delivery(target, &event, &format!("delivery-{i}"), now + 1), materialized_at_ms: now + 1,
        }).await.unwrap();
    }
    let inflight = repo.claim_deliveries(ClaimEventDeliveries { worker_id: "sender".into(), now_ms: now + 2,
        lease_until_ms: now + 1000, limit: 1, env: common::ENV.into() }).await.unwrap().remove(0);
    let mut renew = RenewEventDeliveryLease { delivery_id: inflight.delivery_id.clone(), expected_lease_owner: "wrong-owner".into(),
        attempt_no: inflight.attempt_count, now_ms: now + 3, lease_until_ms: now + 2000, env: common::ENV.into() };
    assert!(matches!(repo.renew_delivery_lease(renew.clone()).await, Err(EventRepoError::LeaseLost(_))));
    renew.expected_lease_owner = inflight.lease_owner.clone().unwrap();
    assert_eq!(repo.renew_delivery_lease(renew).await.unwrap().lease_until_ms, Some(now + 2000));
    repo.commit_group_deletion(common::GROUP_ID, common::ENV, now + 4, || Ok(())).await.unwrap();
    let active = repo.get_delivery(&inflight.delivery_id, common::ENV).await.unwrap().unwrap().0;
    assert_eq!(active.status, EventDeliveryStatus::InFlight);
    let cancelled = repo.get_delivery("delivery-1", common::ENV).await.unwrap().unwrap().0;
    assert_eq!(cancelled.status, EventDeliveryStatus::Cancelled);
    let mut debug = active.clone();
    debug.skip_reason = Some("private operator reason".into());
    let rendered = format!("{debug:?}");
    assert!(rendered.contains("[REDACTED]"));
    assert!(!rendered.contains("private operator reason"));
    assert!(!rendered.contains(&active.payload_sha256));
    assert!(!rendered.contains("correlation_id"), "serialized payload is not dumped by Debug");
    let skip = SkipDeadLetteredEventDelivery { delivery_id: active.delivery_id.clone(), actor: common::actor(),
        reason: "private operator reason".into(), skipped_at_ms: now + 5, env: common::ENV.into() };
    assert!(!format!("{skip:?}").contains("private operator reason"));
    assert!(matches!(repo.skip_dead_lettered_delivery(skip).await, Err(EventRepoError::Conflict(_))));
    let completed = repo.complete_delivery_attempt(CompleteEventDeliveryAttempt {
        delivery_id: active.delivery_id, expected_lease_owner: active.lease_owner.unwrap(), attempt_no: active.attempt_count,
        started_at_ms: now + 2, completed_at_ms: now + 5, result: EventDeliveryAttemptRecordResult::Success,
        next_status: EventDeliveryStatus::Succeeded, next_attempt_at_ms: None, http_status: Some(204),
        error_category: None, error_summary: None, response_bytes_observed: 0,
    }).await.unwrap();
    assert_eq!(completed.status, EventDeliveryStatus::Succeeded, "a real in-flight response can finish after Group deletion");
    let purge = repo.purge_expired(EventRetentionRequest { now_ms: 2_000_000_000_001, event_limit: 10, audit_limit: 10, env: common::ENV.into() }).await.unwrap();
    assert_eq!(purge.events_deleted, 1); assert_eq!(purge.deliveries_deleted, 2); assert_eq!(purge.attempts_deleted, 1);
    assert!(repo.get_event("delivery-event", common::ENV).await.unwrap().is_none());
}

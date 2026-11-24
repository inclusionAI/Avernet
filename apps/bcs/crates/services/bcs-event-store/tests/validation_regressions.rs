#![allow(clippy::expect_used, clippy::unwrap_used)]

mod common;

use std::sync::Arc;

use bcs_db_api::DbPlugin;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_db_migrations as bootstrap_migrations;
use bcs_event_store::{DbEventStore, MemoryEventStore};
use bcs_service_api::port::repo::{
    ClaimEventDeliveries, ClaimFanoutTargets, CompleteEventDeliveryAttempt,
    CreateEventReplayTarget, EventDeliveryAttemptRecordResult, EventDeliveryRecord, EventRepoError,
    EventRepoPort, ListEventDeliveryRecords, ListEventSubscriptionRecords, MaterializeFanoutTarget,
    RenewEventDeliveryLease, ReplaceEventSubscriptionRevision,
};
use bcs_service_api::types::{EventDeliveryStatus, EventSubscriptionStatus};
use sha2::{Digest, Sha256};

use common::invalid_message;

async fn store_pair() -> (MemoryEventStore, DbEventStore) {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    (MemoryEventStore::new(), DbEventStore::sqlite(db))
}

fn renewal() -> RenewEventDeliveryLease {
    RenewEventDeliveryLease {
        delivery_id: "delivery".to_string(),
        expected_lease_owner: "owner".to_string(),
        attempt_no: 1,
        now_ms: 1_000,
        lease_until_ms: 2_000,
        env: common::ENV.to_string(),
    }
}

fn delivery() -> EventDeliveryRecord {
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
        env: common::ENV.to_string(),
    }
}

fn materialization() -> MaterializeFanoutTarget {
    MaterializeFanoutTarget {
        target_id: "target".to_string(),
        expected_lease_owner: "owner".to_string(),
        delivery: delivery(),
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
        actor: common::actor(),
        reason: None,
        created_at_ms: 1_000,
        env: common::ENV.to_string(),
    }
}

fn replacement() -> ReplaceEventSubscriptionRevision {
    ReplaceEventSubscriptionRevision {
        subscription_id: "sub".to_string(),
        expected_revision: 1,
        name: "group events".to_string(),
        status: EventSubscriptionStatus::Active,
        revision: common::revision("sub", 2, vec!["group.*"]),
        cancel_retired_pending_deliveries: false,
        actor: common::actor(),
        reason: None,
        updated_at_ms: 2_000,
        env: common::ENV.to_string(),
    }
}

#[tokio::test]
async fn claim_validation_matches_and_rejects_lease_at_now() {
    let (memory, db) = store_pair().await;
    let cases = [
        ("", 5_u64, 5_u64, 0_u32, ""),
        ("worker", 5, 6, 0, common::ENV),
        ("worker", 5, 5, 10, common::ENV),
        ("worker", 5, 6, 1_001, common::ENV),
    ];
    for (worker_id, now_ms, lease_until_ms, limit, env) in cases {
        let memory_message = invalid_message(
            memory
                .claim_fanout_targets(ClaimFanoutTargets {
                    worker_id: worker_id.to_string(),
                    now_ms,
                    lease_until_ms,
                    limit,
                    env: env.to_string(),
                })
                .await,
        );
        let db_message = invalid_message(
            db.claim_fanout_targets(ClaimFanoutTargets {
                worker_id: worker_id.to_string(),
                now_ms,
                lease_until_ms,
                limit,
                env: env.to_string(),
            })
            .await,
        );
        assert_eq!(
            memory_message, db_message,
            "case {worker_id:?} now={now_ms} lease={lease_until_ms} limit={limit}"
        );
    }
    let memory_message = invalid_message(
        memory
            .claim_deliveries(ClaimEventDeliveries {
                worker_id: "worker".to_string(),
                now_ms: 5,
                lease_until_ms: 5,
                limit: 10,
                env: common::ENV.to_string(),
            })
            .await,
    );
    let db_message = invalid_message(
        db.claim_deliveries(ClaimEventDeliveries {
            worker_id: "worker".to_string(),
            now_ms: 5,
            lease_until_ms: 5,
            limit: 10,
            env: common::ENV.to_string(),
        })
        .await,
    );
    assert_eq!(memory_message, db_message);
    assert_eq!(db_message, "claim lease must expire after now");
}

#[tokio::test]
async fn lease_renewal_validation_matches() {
    let (memory, db) = store_pair().await;
    let mut at_now = renewal();
    at_now.lease_until_ms = at_now.now_ms;
    let mut no_attempt = renewal();
    no_attempt.attempt_no = 0;
    for command in [at_now, no_attempt] {
        let memory_message = invalid_message(memory.renew_delivery_lease(command.clone()).await);
        let db_message = invalid_message(db.renew_delivery_lease(command).await);
        assert_eq!(memory_message, db_message);
        assert_eq!(db_message, "Delivery lease renewal is invalid");
    }
}

#[tokio::test]
async fn delivery_list_limit_zero_and_one_hundred_one_match_on_both_backends() {
    let (memory, db) = store_pair().await;
    for limit in [0_u32, 101] {
        let query = || ListEventDeliveryRecords {
            subscription_id: None,
            event_id: None,
            status: None,
            after_delivery_id: None,
            limit,
            env: common::ENV.to_string(),
        };
        let memory_message = invalid_message(memory.list_deliveries(query()).await);
        let db_message = invalid_message(db.list_deliveries(query()).await);
        assert_eq!(memory_message, db_message);
        assert_eq!(db_message, "Delivery list limit must be between 1 and 100");
    }
    let hundred = ListEventDeliveryRecords {
        subscription_id: None,
        event_id: None,
        status: None,
        after_delivery_id: None,
        limit: 100,
        env: common::ENV.to_string(),
    };
    assert!(memory.list_deliveries(hundred.clone()).await.is_ok());
    assert!(db.list_deliveries(hundred).await.is_ok());
}

#[tokio::test]
async fn new_subscription_checks_scope_identity_and_revision_on_both_backends() {
    let (memory, db) = store_pair().await;
    let mut multi_error = common::subscription("validation-sub");
    multi_error.subscription.scope.id.clear();
    multi_error.subscription.subscription_id.clear();
    multi_error.subscription.env.clear();
    multi_error.revision.revision = 7;
    let memory_message = invalid_message(memory.create_subscription(multi_error.clone()).await);
    let db_message = invalid_message(db.create_subscription(multi_error).await);
    assert_eq!(memory_message, db_message);
    assert_eq!(db_message, "group scope must include a non-empty id");

    let mut missing_identity = common::subscription("validation-sub");
    missing_identity.subscription.subscription_id.clear();
    missing_identity.subscription.env.clear();
    missing_identity.revision.revision = 7;
    let memory_message =
        invalid_message(memory.create_subscription(missing_identity.clone()).await);
    let db_message = invalid_message(db.create_subscription(missing_identity).await);
    assert_eq!(memory_message, db_message);
    assert_eq!(db_message, "subscription id and env must be non-empty");

    let mut wrong_revision = common::subscription("validation-sub");
    wrong_revision.revision.revision = 7;
    let memory_message = invalid_message(memory.create_subscription(wrong_revision.clone()).await);
    let db_message = invalid_message(db.create_subscription(wrong_revision).await);
    assert_eq!(memory_message, db_message);
    assert_eq!(
        db_message,
        "new subscription must contain matching immutable revision 1"
    );

    let mut zero_limit = common::subscription("validation-sub");
    zero_limit.scope_limit = 0;
    let memory_message = invalid_message(memory.create_subscription(zero_limit.clone()).await);
    let db_message = invalid_message(db.create_subscription(zero_limit).await);
    assert_eq!(memory_message, db_message);
    assert_eq!(db_message, "subscription scope limit must be non-zero");
}

#[tokio::test]
async fn materialization_validation_matches_payload_hash_and_leased_state() {
    let (memory, db) = store_pair().await;
    let mut wrong_hash = materialization();
    wrong_hash.delivery.payload_sha256 = "wrong-hash".to_string();
    let mut leased = materialization();
    leased.delivery.lease_owner = Some("owner".to_string());
    for (command, expected) in [
        (wrong_hash, "Delivery payload SHA-256 does not match payload bytes"),
        (leased, "new Delivery must be pending or a projection dead letter, unattempted, and unleased"),
    ] {
        assert_eq!(invalid_message(memory.materialize_fanout_target(command.clone()).await), expected);
        assert_eq!(invalid_message(db.materialize_fanout_target(command).await), expected);
    }
}

#[tokio::test]
async fn completion_validation_matches_retry_wait_and_first_error() {
    let (memory, db) = store_pair().await;
    let mut retry_without_time = completion();
    retry_without_time.result = EventDeliveryAttemptRecordResult::Retryable;
    retry_without_time.next_status = EventDeliveryStatus::RetryWait;
    let mut no_attempt = completion();
    no_attempt.attempt_no = 0;
    let mut backwards = completion();
    backwards.started_at_ms = 2_000;
    for (command, expected) in [
        (retry_without_time, "Attempt result and next Delivery state are inconsistent"),
        (no_attempt, "completion identifiers and attempt number must be non-empty"),
        (backwards, "attempt completion cannot precede its start"),
    ] {
        assert_eq!(invalid_message(memory.complete_delivery_attempt(command.clone()).await), expected);
        assert_eq!(invalid_message(db.complete_delivery_attempt(command).await), expected);
    }
}

#[tokio::test]
async fn replay_validation_matches_overlong_reason_and_missing_identifiers() {
    let (memory, db) = store_pair().await;
    let mut overlong = replay();
    overlong.reason = Some("x".repeat(129));
    let mut missing = replay();
    missing.subscription_revision = 0;
    for (command, expected) in [
        (overlong, "replay reason exceeds 128 bytes"),
        (missing, "replay identifiers, revision, and env must be non-empty"),
    ] {
        assert_eq!(invalid_message(memory.create_replay_target(command.clone()).await), expected);
        assert_eq!(invalid_message(db.create_replay_target(command).await), expected);
    }
}

#[tokio::test]
async fn replacement_keeps_the_backend_specific_first_error() {
    let (memory, db) = store_pair().await;
    let mut multi_error = replacement();
    multi_error.subscription_id.clear();
    multi_error.env.clear();
    multi_error.expected_revision = 3;
    multi_error.revision.revision = 9;
    multi_error.revision.subscription_id = "other".to_string();
    let memory_message = invalid_message(
        memory
            .replace_subscription_revision(multi_error.clone())
            .await,
    );
    let db_message = invalid_message(db.replace_subscription_revision(multi_error).await);
    assert_eq!(memory_message, "subscription id and env must be non-empty");
    assert_eq!(
        db_message,
        "replacement must contain the next immutable revision"
    );

    let mut max_revision = replacement();
    max_revision.expected_revision = u64::MAX;
    let mut skipped_revision = replacement();
    skipped_revision.expected_revision = 2;
    for command in [max_revision, skipped_revision] {
        let expected = "replacement must contain the next immutable revision";
        assert_eq!(invalid_message(memory.replace_subscription_revision(command.clone()).await), expected);
        assert_eq!(invalid_message(db.replace_subscription_revision(command).await), expected);
    }
}

#[tokio::test]
async fn append_validation_checks_fields_before_timestamps_on_both_backends() {
    let (memory, db) = store_pair().await;
    let mut command =
        common::append("validation-event", "validation-key", "group.updated");
    command.event.producer.clear();
    command.recorded_at = "not a timestamp".to_string();
    let memory_message = invalid_message(memory.append_event(command.clone()).await);
    let db_message = invalid_message(db.append_event(command).await);
    assert_eq!(memory_message, db_message);
    assert_eq!(db_message, "producer must be non-empty");

    let mut command = common::append("validation-event", "validation-key", "group.updated");
    command.event.stream_key.clear();
    let memory_message = invalid_message(memory.append_event(command.clone()).await);
    let db_message = invalid_message(db.append_event(command).await);
    assert_eq!(memory_message, db_message);
    assert_eq!(db_message, "stream_key must be non-empty");

    let mut invalid_recorded_at =
        common::append("validation-event", "validation-key", "group.updated");
    invalid_recorded_at.recorded_at = "not a timestamp".to_string();
    let memory_message = invalid_message(memory.append_event(invalid_recorded_at.clone()).await);
    let db_message = invalid_message(db.append_event(invalid_recorded_at).await);
    assert_eq!(memory_message, db_message);
    assert!(db_message.starts_with("invalid RFC3339 timestamp"));

    let mut invalid_occurred_at =
        common::append("validation-event", "validation-key", "group.updated");
    invalid_occurred_at.event.occurred_at = "not a timestamp".to_string();
    let memory_message = invalid_message(memory.append_event(invalid_occurred_at.clone()).await);
    let db_message = invalid_message(db.append_event(invalid_occurred_at).await);
    assert_eq!(memory_message, db_message);
    assert!(db_message.starts_with("invalid RFC3339 timestamp"));

    // The pre-epoch guard is Memory-only; the SQL path writes the instant and
    // later fails to read it back as an unsigned millisecond column.
    let mut pre_epoch = common::append("validation-event", "validation-key", "group.updated");
    pre_epoch.event.occurred_at = "1969-12-31T23:59:59Z".to_string();
    assert_eq!(
        invalid_message(memory.append_event(pre_epoch.clone()).await),
        "timestamp predates Unix epoch: 1969-12-31T23:59:59Z"
    );
    assert!(matches!(
        db.append_event(pre_epoch).await,
        Err(EventRepoError::Storage(_))
    ));
}

#[tokio::test]
async fn subscription_list_limit_stays_backend_inline_for_zero_and_one_hundred_one() {
    let (memory, db) = store_pair().await;
    for limit in [0_u32, 101] {
        let query = || ListEventSubscriptionRecords {
            scope: None,
            status: None,
            after_subscription_id: None,
            limit,
            env: common::ENV.to_string(),
        };
        let memory_message = invalid_message(memory.list_subscriptions(query()).await);
        let db_message = invalid_message(db.list_subscriptions(query()).await);
        assert_eq!(memory_message, db_message);
        assert_eq!(
            db_message,
            "subscription list limit must be between 1 and 100"
        );
    }
    let hundred = ListEventSubscriptionRecords {
        scope: None,
        status: None,
        after_subscription_id: None,
        limit: 100,
        env: common::ENV.to_string(),
    };
    assert!(memory.list_subscriptions(hundred.clone()).await.is_ok());
    assert!(db.list_subscriptions(hundred).await.is_ok());
}

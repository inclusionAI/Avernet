#![allow(clippy::expect_used, clippy::unwrap_used)]

mod common;

use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use bcs_event_store::DbEventStore;
use bcs_service_api::port::repo::{
    ClaimEventDeliveries, ClaimFanoutTargets, CompleteEventDeliveryAttempt,
    EventDeliveryAttemptRecordResult, EventDeliveryRecord, EventRepoPort, MaterializeFanoutTarget,
};
use bcs_service_api::types::EventDeliveryStatus;
use bcs_test_support::contract::repo::{
    event_delivery_repo_port_contract_tests, event_repo_port_contract_tests,
};
use mysql_async::Opts;
use sha2::{Digest, Sha256};

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn mysql_event_store_passes_contract() {
    let mysql_url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set for the ignored MySQL contract");
    let opts = Opts::from_url(&mysql_url).expect("valid BCS_TEST_MYSQL_URL");
    let database = opts
        .db_name()
        .expect("BCS_TEST_MYSQL_URL includes a database name");
    let mut config = MysqlDbConfig::new()
        .with_database(database)
        .with_connection(MysqlConnectionConfig {
            connection_type: "direct".to_string(),
            host: Some(opts.ip_or_hostname().to_string()),
            port: Some(opts.tcp_port()),
            user: opts.user().map(str::to_string),
            password: opts.pass().map(str::to_string),
            extra: BTreeMap::new(),
        })
        .with_statement_protocol(StatementProtocol::Text);
    // A single pooled connection keeps the non-UTC session timezone below
    // stable for the entire regression test.
    config.pool_size = 1;
    config.min_pool_size = 1;

    let manager = MysqlDbManager::new(config)
        .await
        .expect("open MySQL contract datasource");
    let plugin = Arc::new(MysqlDbPlugin::new(manager.clone(), "bcs"));
    apply_eventing_migration(plugin.as_ref()).await;
    plugin
        .execute(DbStatement::new("SET SESSION time_zone = '+08:00'"))
        .await
        .expect("set non-UTC MySQL session timezone");
    let repo = DbEventStore::mysql(plugin.clone());

    let mut timezone_subscription = common::subscription("sub-mysql-timezone");
    timezone_subscription.subscription.env = "contract-timezone-mysql".to_string();
    let expected_activated_at_ms = timezone_subscription.revision.activated_at_ms;
    repo.create_subscription(timezone_subscription)
        .await
        .expect("create subscription in non-UTC session");
    let (_, timezone_revision) = repo
        .get_subscription("sub-mysql-timezone", "contract-timezone-mysql")
        .await
        .expect("read timezone subscription")
        .expect("timezone subscription exists");
    assert_eq!(timezone_revision.activated_at_ms, expected_activated_at_ms);

    let mut timezone_event = common::append(
        "evt-mysql-timezone",
        "mysql:timezone:group:created",
        "group.created",
    );
    timezone_event.env = "contract-timezone-mysql".to_string();
    let expected_occurred_at = timezone_event.event.occurred_at.clone();
    let expected_recorded_at = timezone_event.recorded_at.clone();
    let expected_retention_until_ms = timezone_event.retention_until_ms;
    let stored_timezone_event = repo
        .append_event(timezone_event)
        .await
        .expect("append Event in non-UTC session")
        .event;
    assert_eq!(stored_timezone_event.envelope.occurred_at, expected_occurred_at);
    assert_eq!(stored_timezone_event.envelope.recorded_at, expected_recorded_at);
    assert_eq!(
        stored_timezone_event.retention_until_ms,
        expected_retention_until_ms
    );

    event_repo_port_contract_tests(
        &repo,
        common::subscription("sub-mysql-contract"),
        common::append("evt-mysql-contract", "mysql:group:created", "group.created"),
    )
    .await;
    let mut delivery_subscription = common::subscription("sub-mysql-delivery");
    delivery_subscription.subscription.env = "contract-delivery-mysql".to_string();
    let mut delivery_append =
        common::append("evt-mysql-delivery", "mysql:delivery", "group.created");
    delivery_append.env = delivery_subscription.subscription.env.clone();
    event_delivery_repo_port_contract_tests(&repo, delivery_subscription, delivery_append).await;
    assert_second_precision_claims(&repo, plugin.as_ref()).await;
    manager.close().await;
}

async fn assert_second_precision_claims(repo: &DbEventStore, db: &dyn DbPlugin) {
    const BASE: u64 = 1_755_561_610_999;
    const ENV: &str = "contract-second-precision-mysql";

    for statement in [
        "ALTER TABLE bcs_event_fanout_targets MODIFY COLUMN lease_until TIMESTAMP NULL DEFAULT NULL",
        "ALTER TABLE bcs_event_deliveries MODIFY COLUMN lease_until TIMESTAMP NULL DEFAULT NULL",
    ] {
        db
            .execute(DbStatement::new(statement))
            .await
            .unwrap_or_else(|error| panic!("apply second-precision lease column: {error}"));
    }

    let mut subscription = common::subscription("sub-second-precision-mysql");
    subscription.subscription.env = ENV.to_string();
    repo.create_subscription(subscription)
        .await
        .expect("create second-precision Subscription");
    let mut append = common::append(
        "evt-second-precision-mysql",
        "mysql:second-precision",
        "group.created",
    );
    append.env = ENV.to_string();
    let event = repo
        .append_event(append)
        .await
        .expect("append second-precision Event")
        .event;

    let targets = repo
        .claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "fanout-second-precision".to_string(),
            now_ms: BASE,
            lease_until_ms: BASE + 1_000,
            limit: 10,
            env: ENV.to_string(),
        })
        .await
        .expect("claim target with a truncated lease timestamp");
    assert_eq!(targets.len(), 1);
    let target = &targets[0];
    assert_eq!(target.lease_until_ms, Some(BASE + 1_001));
    let payload_bytes = serde_json::to_vec(&event.envelope).expect("serialize Event payload");
    let payload_sha256 = format!("{:x}", Sha256::digest(&payload_bytes));
    let delivery = repo
        .materialize_fanout_target(MaterializeFanoutTarget {
            target_id: target.target_id.clone(),
            expected_lease_owner: target
                .lease_owner
                .clone()
                .expect("claimed target lease owner"),
            delivery: EventDeliveryRecord {
                delivery_id: "delivery-second-precision-mysql".to_string(),
                fanout_target_id: target.target_id.clone(),
                event_id: event.envelope.event_id.clone(),
                event_type: event.envelope.event_type.clone(),
                subscription_id: target.subscription_id.clone(),
                subscription_revision: target.subscription_revision,
                stream_key: event.envelope.stream.key.clone(),
                sequence: event.envelope.stream.sequence,
                payload_bytes,
                payload_sha256,
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
                created_at_ms: BASE + 1,
                succeeded_at_ms: None,
                env: ENV.to_string(),
            },
            materialized_at_ms: BASE + 1,
        })
        .await
        .expect("materialize target with a truncated lease timestamp");

    let claimed = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-second-precision".to_string(),
            now_ms: BASE + 2,
            lease_until_ms: BASE + 1_002,
            limit: 10,
            env: ENV.to_string(),
        })
        .await
        .expect("claim Delivery with a truncated lease timestamp");
    assert_eq!(claimed.len(), 1);
    assert_eq!(claimed[0].delivery_id, delivery.delivery_id);
    assert_eq!(claimed[0].lease_until_ms, Some(BASE + 2_001));
    let (_, attempts) = repo
        .get_delivery(&delivery.delivery_id, ENV)
        .await
        .expect("read claimed Delivery")
        .expect("claimed Delivery exists");
    assert_eq!(attempts.len(), 1);
    assert_eq!(attempts[0].attempt_no, 1);
    assert_eq!(attempts[0].completed_at_ms, None);

    let reclaimed = repo
        .claim_deliveries(ClaimEventDeliveries {
            worker_id: "delivery-second-precision-recovery".to_string(),
            now_ms: BASE + 2_002,
            lease_until_ms: BASE + 3_002,
            limit: 10,
            env: ENV.to_string(),
        })
        .await
        .expect("reclaim Delivery with a truncated expired lease timestamp");
    assert_eq!(reclaimed.len(), 1);
    assert_eq!(reclaimed[0].attempt_count, 2);
    let (_, attempts) = repo
        .get_delivery(&delivery.delivery_id, ENV)
        .await
        .expect("read reclaimed Delivery")
        .expect("reclaimed Delivery exists");
    assert_eq!(attempts.len(), 2);
    assert_eq!(
        attempts[0].result,
        Some(EventDeliveryAttemptRecordResult::Retryable)
    );
    assert_eq!(attempts[1].attempt_no, 2);
    assert_eq!(attempts[1].completed_at_ms, None);

    let completed = repo
        .complete_delivery_attempt(CompleteEventDeliveryAttempt {
            delivery_id: reclaimed[0].delivery_id.clone(),
            expected_lease_owner: reclaimed[0]
                .lease_owner
                .clone()
                .expect("claimed Delivery lease owner"),
            attempt_no: reclaimed[0].attempt_count,
            started_at_ms: BASE + 2_002,
            completed_at_ms: BASE + 2_102,
            result: EventDeliveryAttemptRecordResult::Success,
            next_status: EventDeliveryStatus::Succeeded,
            next_attempt_at_ms: None,
            http_status: Some(204),
            error_category: None,
            error_summary: None,
            response_bytes_observed: 0,
        })
        .await
        .expect("complete Delivery with second-precision lease columns");
    assert_eq!(completed.status, EventDeliveryStatus::Succeeded);
}

async fn apply_eventing_migration(db: &dyn DbPlugin) {
    let migration = include_str!("../../../../migrations/mysql/009_eventing.sql");
    for statement in migration
        .split(';')
        .map(str::trim)
        .filter(|sql| !sql.is_empty())
    {
        db.execute(DbStatement::new(statement))
            .await
            .unwrap_or_else(|error| {
                panic!("apply Eventing migration statement: {error}\n{statement}")
            });
    }
}

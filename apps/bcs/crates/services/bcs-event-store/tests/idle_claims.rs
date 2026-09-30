#![allow(clippy::expect_used, clippy::unwrap_used)]

mod common;

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use async_trait::async_trait;
use bcs_db_api::{DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep, DbTransactionStepResult};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_event_store::DbEventStore;
use bcs_service_api::port::repo::{ClaimEventDeliveries, ClaimFanoutTargets, EventDeliveryRecord, EventRepoPort, MaterializeFanoutTarget};
use bcs_service_api::types::EventDeliveryStatus;
use sha2::{Digest, Sha256};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

const NOW: u64 = 1_800_000_000_000;

struct ObservedDb {
    inner: LocalSqliteDbPlugin,
    executed: AtomicUsize,
    fail_delivery_claim: AtomicBool,
}

#[async_trait]
impl DbPlugin for ObservedDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.executed.fetch_add(1, Ordering::SeqCst);
        self.inner.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.executed.fetch_add(1, Ordering::SeqCst);
        self.inner.execute(statement).await
    }
    async fn transaction(&self, mut steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
        if self.fail_delivery_claim.load(Ordering::SeqCst) && matches!(steps.first(),
            Some(DbTransactionStep::Execute(statement))
                if statement.stops_transaction_on_no_rows() && statement.sql().starts_with("UPDATE bcs_event_deliveries")) {
            steps[2] = DbTransactionStep::Execute(DbStatement::new("INSERT INTO missing_attempt_table VALUES (1)"));
        }
        let results = self.inner.transaction(steps).await?;
        // The real plugin returns exactly the executed prefix. Its shared
        // conformance suite separately proves skipped SQL never reaches the DB.
        self.executed.fetch_add(results.len(), Ordering::SeqCst);
        Ok(results)
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

async fn harness() -> (Arc<ObservedDb>, Arc<DbEventStore>) {
    let inner = LocalSqliteDbPlugin::new().unwrap();
    bootstrap_migrations::run_sqlite_migrations(&inner).await.unwrap();
    let db = Arc::new(ObservedDb { inner, executed: AtomicUsize::new(0), fail_delivery_claim: AtomicBool::new(false) });
    let repo = Arc::new(DbEventStore::sqlite(db.clone()));
    (db, repo)
}

fn claim() -> ClaimEventDeliveries {
    ClaimEventDeliveries { worker_id: "delivery".into(), now_ms: NOW, lease_until_ms: NOW + 1000, limit: 10, env: common::ENV.into() }
}

#[tokio::test]
async fn four_idle_workers_execute_one_statement_per_claim() {
    let (db, repo) = harness().await;
    let mut workers = tokio::task::JoinSet::new();
    for worker in 0..4 {
        let repo = repo.clone();
        workers.spawn(async move {
            for _ in 0..10 {
                assert!(repo.claim_fanout_targets(ClaimFanoutTargets {
                    worker_id: format!("fanout-{worker}"), now_ms: NOW,
                    lease_until_ms: NOW + 1000, limit: 10, env: common::ENV.into(),
                }).await.unwrap().is_empty());
                assert!(repo.claim_deliveries(claim()).await.unwrap().is_empty());
            }
        });
    }
    while let Some(result) = workers.join_next().await { result.unwrap(); }
    assert_eq!(db.executed.load(Ordering::SeqCst), 80, "previously 320 SQL statements for this idle workload");
}

#[tokio::test]
async fn a_later_attempt_write_failure_rolls_back_the_claim_and_can_be_retried() {
    let (db, repo) = harness().await;
    repo.create_subscription(common::subscription("sub-atomic")).await.unwrap();
    let event = repo.append_event(common::append("evt-atomic", "atomic", "group.created")).await.unwrap().event;
    let target = repo.claim_fanout_targets(ClaimFanoutTargets {
        worker_id: "fanout".into(), now_ms: NOW - 10, lease_until_ms: NOW + 1000,
        limit: 10, env: common::ENV.into(),
    }).await.unwrap().remove(0);
    let payload_bytes = serde_json::to_vec(&event.envelope).unwrap();
    let delivery = EventDeliveryRecord {
        delivery_id: "delivery-atomic".into(),
        fanout_target_id: target.target_id.clone(),
        event_id: event.envelope.event_id.clone(),
        event_type: event.envelope.event_type.clone(),
        subscription_id: target.subscription_id.clone(),
        subscription_revision: target.subscription_revision,
        stream_key: event.envelope.stream.key.clone(),
        sequence: event.envelope.stream.sequence,
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
        created_at_ms: NOW - 5,
        succeeded_at_ms: None,
        env: common::ENV.into(),
    };
    repo.materialize_fanout_target(MaterializeFanoutTarget {
        target_id: target.target_id, expected_lease_owner: target.lease_owner.unwrap(),
        delivery, materialized_at_ms: NOW - 5,
    }).await.unwrap();
    db.fail_delivery_claim.store(true, Ordering::SeqCst);
    assert!(repo.claim_deliveries(claim()).await.is_err());
    let (stored, attempts) = repo.get_delivery("delivery-atomic", common::ENV).await.unwrap().unwrap();
    assert_eq!(stored.status, EventDeliveryStatus::Pending);
    assert_eq!(stored.attempt_count, 0);
    assert!(stored.lease_owner.is_none() && attempts.is_empty());
    db.fail_delivery_claim.store(false, Ordering::SeqCst);
    db.executed.store(0, Ordering::SeqCst);
    assert_eq!(repo.claim_deliveries(claim()).await.unwrap().len(), 1);
    assert_eq!(db.executed.load(Ordering::SeqCst), 6, "successful claims still execute every atomic step");
    let (stored, attempts) = repo.get_delivery("delivery-atomic", common::ENV).await.unwrap().unwrap();
    assert_eq!(stored.status, EventDeliveryStatus::InFlight);
    assert_eq!(attempts.len(), 1);
}

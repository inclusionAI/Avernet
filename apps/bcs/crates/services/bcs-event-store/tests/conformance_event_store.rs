#![allow(clippy::expect_used, clippy::unwrap_used)]

mod common;

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbTransactionStep, db_get_column};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_event_store::{DbEventStore, MemoryEventStore};
use bcs_service_api::port::repo::EventRepoPort;
use bcs_test_support::contract::repo::event_repo_port_contract_tests;

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

#[tokio::test]
async fn memory_event_store_passes_contract() {
    let repo = MemoryEventStore::new();
    run_contract(&repo).await;
}

#[tokio::test]
async fn sqlite_event_store_passes_contract() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let repo = DbEventStore::sqlite(db);
    run_contract(&repo).await;
}

#[tokio::test]
async fn sqlite_transaction_fragment_rolls_back_business_mutation_and_sequence() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    db.execute(DbStatement::new(
        "CREATE TABLE contract_business_mutations (mutation_id TEXT PRIMARY KEY)",
    ))
    .await
    .expect("create business mutation fixture");
    let repo = DbEventStore::sqlite(db.clone());
    repo.create_subscription(common::subscription("sub-uow"))
        .await
        .expect("create subscription");
    let first = repo
        .append_event(common::append("evt-uow-1", "uow:first", "group.created"))
        .await
        .expect("append first Event");
    assert_eq!(first.event.envelope.stream.sequence, 1);

    let mut missing_cause = common::append("evt-uow-invalid", "uow:invalid", "group.created");
    missing_cause.event.causation_event_id = Some("evt-uow-future".to_string());
    let plan = repo
        .append_transaction_plan(&missing_cause, 1)
        .expect("build composable Event fragment");
    let mut steps = vec![DbTransactionStep::Execute(DbStatement::with_params(
        "INSERT INTO contract_business_mutations (mutation_id) VALUES (?)",
        vec!["mutation-rolled-back".into()],
    ))];
    steps.extend(plan.steps);
    assert!(db.transaction(steps).await.is_err());

    let rows = db
        .query(DbStatement::new(
            "SELECT COUNT(*) AS mutation_count FROM contract_business_mutations",
        ))
        .await
        .expect("query rolled back mutation");
    assert_eq!(db_get_column::<i64>(&rows[0], "mutation_count").unwrap(), 0);

    let committed = common::append("evt-uow-2", "uow:second", "group.created");
    let plan = repo
        .append_transaction_plan(&committed, 1)
        .expect("build successful Event fragment");
    let mut steps = vec![DbTransactionStep::Execute(DbStatement::with_params(
        "INSERT INTO contract_business_mutations (mutation_id) VALUES (?)",
        vec!["mutation-committed".into()],
    ))];
    steps.extend(plan.steps);
    db.transaction(steps)
        .await
        .expect("commit business mutation and Event snapshot together");
    let second = repo
        .get_event("evt-uow-2", common::ENV)
        .await
        .expect("read committed Event")
        .expect("composed Event exists");
    assert_eq!(
        second.envelope.stream.sequence, 2,
        "rolled back transaction must not consume a visible sequence"
    );
    let rows = db
        .query(DbStatement::new(
            "SELECT COUNT(*) AS target_count FROM bcs_event_fanout_targets \
             WHERE event_id = 'evt-uow-2'",
        ))
        .await
        .expect("query committed target snapshot");
    assert_eq!(db_get_column::<i64>(&rows[0], "target_count").unwrap(), 1);
}

async fn run_contract(repo: &dyn EventRepoPort) {
    event_repo_port_contract_tests(
        repo,
        common::subscription("sub-contract"),
        common::append("evt-contract-1", "group:created", "group.created"),
    )
    .await;
}

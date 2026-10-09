#![allow(clippy::expect_used, clippy::unwrap_used)]

mod common;

use std::sync::Arc;

use bcs_db_api::DbPlugin;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_event_store::{DbEventStore, MemoryEventStore};
use bcs_test_support::contract::repo::event_delivery_repo_port_contract_tests;

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

#[tokio::test]
async fn memory_delivery_store_passes_contract() {
    let repo = MemoryEventStore::new();
    event_delivery_repo_port_contract_tests(
        &repo,
        common::subscription("sub-delivery-memory"),
        common::append("evt-delivery-memory", "delivery:memory", "group.created"),
    )
    .await;
}

#[tokio::test]
async fn sqlite_delivery_store_passes_contract() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let repo = DbEventStore::sqlite(db);
    event_delivery_repo_port_contract_tests(
        &repo,
        common::subscription("sub-delivery-sqlite"),
        common::append("evt-delivery-sqlite", "delivery:sqlite", "group.created"),
    )
    .await;
}

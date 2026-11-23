//! Memory and SQL implementations run the same centralized snapshot contract.
use std::sync::Arc;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_group_store::{MemoryGroupRepo, MySqlGroupStore};
use bcs_test_support::contract::group_mutation_snapshot::group_mutation_snapshot_contract_tests;

#[tokio::test]
async fn memory_commit_snapshot_conformance() {
    group_mutation_snapshot_contract_tests(Arc::new(MemoryGroupRepo::new())).await;
}

#[tokio::test]
async fn sqlite_commit_snapshot_conformance() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    group_mutation_snapshot_contract_tests(Arc::new(MySqlGroupStore::sqlite(db, "snapshot-contract".into()))).await;
}

//! The existing grant-kind contract runs first. AgentInvoke then uses the SAME
//! migrated, populated database, without migration replay or clearing legacy data.
use std::sync::Arc;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement};
use bcs_service_api::port::repo::PermissionProfileRepoPort;
use bcs_edge_permission_store::{DbEdgeGrantStore, DbPermissionProfileStore};
#[path = "support/agent_invoke_migrated.rs"]
mod agent_invoke;

#[tokio::test]
async fn agent_invoke_contract_shares_populated_grant_database() {
    let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    DbPermissionProfileStore::sqlite(db.clone()).ensure_default_profile("legacy-bot", "auth-edge").await.unwrap();
    bcs_test_support::contract::repo::run_edge_grant_repo_contract(
        &DbEdgeGrantStore::sqlite(db.clone()), "auth-edge", "legacy-bot", "human_legacy").await;
    let legacy = snapshot(db.as_ref()).await;
    assert!(legacy.iter().all(|rows| !rows.is_empty()), "real old-kind fixture must be populated");
    agent_invoke::run(db.clone(), DbSqlFlavor::Sqlite).await;
    assert_eq!(snapshot(db.as_ref()).await, legacy, "AgentInvoke never clears or changes legacy rows");
}
async fn snapshot(db: &dyn DbPlugin) -> Vec<Vec<bcs_db_api::DbRow>> {
    let mut rows = vec![];
    for table in ["edge_grants", "permission_profiles"] {
        rows.push(db.query(DbStatement::new(format!("SELECT * FROM {table} WHERE env='auth-edge' ORDER BY id"))).await.unwrap());
    }
    rows
}

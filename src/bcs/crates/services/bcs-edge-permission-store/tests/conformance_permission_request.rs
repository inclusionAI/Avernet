//! Rule 25 driver: runs the `PermissionRequestRepoPort` conformance suite
//! against the SQLite-backed [`DbPermissionRequestStore`].
//!
//! Mirrors the inline unit tests in `src/lib.rs::tests` for the SQLite setup
//! (`LocalSqliteDbPlugin` + the full 16-column `permission_requests` DDL) but
//! delegates the assertions to the reusable contract function in
//! `bcs_test_support::contract::repo::permission_request`.

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::DbPermissionRequestStore;
use bcs_test_support::contract::repo::run_permission_request_repo_contract;

#[tokio::test]
async fn permission_request_repo_contract_sqlite() {
    let db = sqlite_with_schema().await;
    let store = DbPermissionRequestStore::sqlite(db);
    run_permission_request_repo_contract(&store, "test").await;
}

/// Fresh LocalSqliteDbPlugin with the full 16-column `permission_requests`
/// table (mirrors `migrations/mysql/014_edge_permission.sql` for SQLite;
/// matches the inline `request_store()` helper in `src/lib.rs::tests`).
async fn sqlite_with_schema() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("local sqlite"));
    db.execute(DbStatement::new(
        "CREATE TABLE permission_requests (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            request_id VARCHAR(64) NOT NULL, \
            edge_id INTEGER, \
            env VARCHAR(32) NOT NULL, \
            from_id VARCHAR(128) NOT NULL, \
            to_id VARCHAR(128) NOT NULL, \
            request_kind VARCHAR(32) NOT NULL, \
            requested_ref_id INTEGER, \
            requested_rules TEXT, \
            message TEXT, \
            status VARCHAR(16) NOT NULL DEFAULT 'pending', \
            decision_reason TEXT, \
            created_by VARCHAR(128) NOT NULL, \
            decided_by VARCHAR(128), \
            decided_at TEXT, \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ))
    .await
    .expect("create permission_requests");
    db
}
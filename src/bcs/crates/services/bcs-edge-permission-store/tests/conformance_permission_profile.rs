//! Rule 25 driver: runs the `PermissionProfileRepoPort` conformance suite
//! against the SQLite-backed [`DbPermissionProfileStore`].
//!
//! Mirrors the inline unit tests in `src/lib.rs::tests` for the SQLite setup
//! (`LocalSqliteDbPlugin` + the full 14-column `permission_profiles` DDL) but
//! delegates the assertions to the reusable contract function in
//! `bcs_test_support::contract::repo::permission_profile`.

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::DbPermissionProfileStore;
use bcs_test_support::contract::repo::run_permission_profile_repo_contract;

#[tokio::test]
async fn permission_profile_repo_contract_sqlite() {
    let db = sqlite_with_schema().await;
    let store = DbPermissionProfileStore::sqlite(db);
    run_permission_profile_repo_contract(&store, "test").await;
}

/// Fresh LocalSqliteDbPlugin with the full 14-column `permission_profiles`
/// table (mirrors `migrations/mysql/009_edge_permission.sql` for SQLite;
/// matches the inline `profile_store()` helper in `src/lib.rs::tests`).
async fn sqlite_with_schema() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("local sqlite"));
    db.execute(DbStatement::new(
        "CREATE TABLE permission_profiles (\
            permission_profile_id VARCHAR(128) NOT NULL, \
            bot_id VARCHAR(128) NOT NULL, \
            env VARCHAR(32) NOT NULL, \
            name VARCHAR(128) NOT NULL DEFAULT 'default', \
            description VARCHAR(512), \
            rules_template TEXT NOT NULL, \
            revision INTEGER NOT NULL DEFAULT 1, \
            digest VARCHAR(128) NOT NULL, \
            is_default INTEGER NOT NULL DEFAULT 0, \
            status VARCHAR(16) NOT NULL DEFAULT 'active', \
            created_by VARCHAR(128) NOT NULL, \
            updated_by VARCHAR(128), \
            created_at INTEGER NOT NULL, \
            updated_at INTEGER NOT NULL, \
            PRIMARY KEY (permission_profile_id))",
    ))
    .await
    .expect("create permission_profiles");
    db
}
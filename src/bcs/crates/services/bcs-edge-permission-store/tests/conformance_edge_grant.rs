//! Rule 25 driver: runs the `EdgeGrantRepoPort` conformance suite against the
//! SQLite-backed [`DbEdgeGrantStore`].
//!
//! Mirrors the inline unit tests in `src/lib.rs::tests` for the SQLite setup
//! (`LocalSqliteDbPlugin` + `edge_grants` / `permission_profiles` table DDL)
//! but delegates the assertions to the reusable contract function in
//! `bcs_test_support::contract::repo::edge_grant`. Per the contract's contract,
//! the driver seeds `target_bot`'s default profile via
//! [`DbPermissionProfileStore::ensure_default_profile`] before invoking the
//! harness — `EdgeGrantRepoPort` itself can only *read* the default id.

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::{DbEdgeGrantStore, DbPermissionProfileStore};
use bcs_service_api::port::repo::PermissionProfileRepoPort;
use bcs_test_support::contract::repo::run_edge_grant_repo_contract;

#[tokio::test]
async fn edge_grant_repo_contract_sqlite() {
    let db = sqlite_with_schema().await;

    // Seed target bot's default profile so get_default_profile_id resolves.
    let profile_store = DbPermissionProfileStore::sqlite(db.clone());
    profile_store
        .ensure_default_profile("bot_target:001", "test")
        .await
        .expect("seed default profile");

    // Run the conformance suite against the grant store.
    let grant_store = DbEdgeGrantStore::sqlite(db);
    run_edge_grant_repo_contract(&grant_store, "test", "bot_target:001", "human_1").await;
}

/// Fresh LocalSqliteDbPlugin with `edge_grants` + `permission_profiles`
/// tables (mirrors `migrations/mysql/006_edge_permission.sql` for SQLite;
/// matches the inline `sqlite_store()` helper in `src/lib.rs::tests`).
async fn sqlite_with_schema() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("local sqlite"));
    db.execute(DbStatement::new(
        "CREATE TABLE edge_grants (\
            edge_id VARCHAR(128) NOT NULL, \
            env VARCHAR(32) NOT NULL, \
            from_id VARCHAR(128) NOT NULL, \
            to_id VARCHAR(128) NOT NULL, \
            grant_kind VARCHAR(32) NOT NULL, \
            grant_ref_id VARCHAR(128) NOT NULL, \
            rules TEXT, \
            status VARCHAR(16) NOT NULL DEFAULT 'approved', \
            originator_policy_type VARCHAR(32) NOT NULL DEFAULT 'any', \
            originator_policy_data TEXT, \
            created_at INTEGER NOT NULL, \
            updated_at INTEGER NOT NULL, \
            PRIMARY KEY (edge_id), \
            UNIQUE (from_id, to_id, env, grant_ref_id))",
    ))
    .await
    .expect("create edge_grants");
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
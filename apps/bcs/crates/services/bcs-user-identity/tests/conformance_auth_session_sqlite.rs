#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test assertions intentionally fail fast"
)]

//! SQLite-backed `AuthSessionRepoPort` conformance for `DbUserIdentityStore`.
//!
//! This test exercises the real SQL implementation end-to-end against a
//! `bcs-db-local` `LocalSqliteDbPlugin` in three layers:
//!
//! 1. **Upgrade-on-apply legacy invalidation**: build an *old* (pre-028)
//!    `bcs_user_identities` schema, install a legacy `token`/`token_expire_at`
//!    row, apply migration `032_auth_session_version.sql` directly, then
//!    assert (a) the legacy token is cleared by the migration's guarded VLDB
//!    `UPDATE`, (b) the new `session_id` / `session_revision` /
//!    `session_expires_at` columns exist, (c) re-running the migration's
//!    `UPDATE` is a no-op once a fresh session is installed through the port
//!    (the `session_id IS NULL` guard excludes the new row). Legacy tokens
//!    no longer validate post-upgrade (`get_by_token` returns `None`).
//!
//! 2. **Central harness pass-through**: invoke the
//!    `bcs_test_support::contract::repo::auth_session::auth_session_repo_port_contract_tests`
//!    suite with a real `DbUserIdentityStore` and a `scope` obtained via the
//!    legacy `ensure_identity` path. The SQL impl must be observably
//!    identical to the in-memory reference in `session_memory`.
//!
//! 3. **Cross-scope legacy survival**: a legacy identity row that never gets
//!    a session installed remains queryable by `get_by_user_id_display` and
//!    reports `read_session_revision == 0`.

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::auth_session::{
    AuthSessionRepoPort, AuthSessionScope, AuthSessionVersion, InstallAuthSession,
};
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::DbUserIdentityStore;

/// Build an *old* (pre-028) `bcs_user_identities` table and the
/// `bcs_schema_migrations` record table. No `session_*` columns exist on the
/// legacy row.
async fn build_legacy_schema(db: &Arc<dyn DbPlugin>) {
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_user_identities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            auth_source TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            user_name TEXT DEFAULT NULL,
            external_user_name TEXT DEFAULT NULL,
            avatar TEXT DEFAULT NULL,
            token TEXT DEFAULT NULL,
            token_expire_at TEXT DEFAULT NULL,
            env TEXT NOT NULL,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )",
    ))
    .await
    .expect("create legacy bcs_user_identities");
    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX uk_user_id ON bcs_user_identities(user_id)",
    ))
    .await
    .expect("create uk_user_id");
    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX uk_external ON bcs_user_identities(auth_source, external_user_id, env)",
    ))
    .await
    .expect("create uk_external");
    db.execute(DbStatement::new(
        "CREATE INDEX idx_external ON bcs_user_identities(external_user_id, env)",
    ))
    .await
    .expect("create idx_external");
}

async fn insert_legacy_token_row(db: &Arc<dyn DbPlugin>) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_user_identities \
         (user_id, auth_source, external_user_id, user_name, external_user_name, avatar, \
          token, token_expire_at, env) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        vec![
            DbValue::from("legacy-user-1"),
            DbValue::from("cookie"),
            DbValue::from("ext-legacy"),
            DbValue::from("legacy-internal-name"),
            DbValue::from("Legacy Display"),
            DbValue::Null,
            DbValue::from("legacy-token-abc"),
            DbValue::from("2030-01-01 00:00:00"),
            DbValue::from("dev"),
        ],
    ))
    .await
    .expect("insert legacy token row");
}

/// Apply migration 028's SQL body directly against the SQLite plugin. The SQL
/// file's `--` line comments may contain `;` (prose), so comment lines are
/// stripped first and the remaining body is split on `;` (the file is plain
/// additive `ALTER TABLE ADD COLUMN`'s plus one guarded `UPDATE` clearing
/// legacy `token`/`token_expire_at` rows).
async fn apply_migration_28(db: &Arc<dyn DbPlugin>) {
    let sql = include_str!("../../../../migrations/sqlite/032_auth_session_version.sql");
    let stripped: String = sql
        .lines()
        .filter(|line| !line.trim_start().starts_with("--"))
        .collect::<Vec<_>>()
        .join("\n");
    for stmt in stripped
        .split(';')
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        db.execute(DbStatement::new(stmt))
            .await
            .expect("apply migration 028 statement");
    }
}

/// Extract only the `UPDATE bcs_user_identities …` statement from the
/// migration body, after stripping `--` comment lines. Used for re-run
/// idempotency verification.
fn migration_28_update_statement() -> String {
    let sql = include_str!("../../../../migrations/sqlite/032_auth_session_version.sql");
    let stripped: String = sql
        .lines()
        .filter(|line| !line.trim_start().starts_with("--"))
        .collect::<Vec<_>>()
        .join("\n");
    stripped
        .split(';')
        .map(str::trim)
        .find(|s| s.contains("UPDATE bcs_user_identities"))
        .map(str::to_string)
        .expect("UPDATE statement in migration 028")
}

async fn column_names(db: &dyn DbPlugin, table: &str) -> Vec<String> {
    let rows = db
        .query(DbStatement::new(format!("PRAGMA table_info({table})")))
        .await
        .expect("PRAGMA table_info");
    rows.into_iter()
        .map(|row| {
            row.get_string("name")
                .expect("PRAGMA name column")
                .unwrap_or_default()
        })
        .collect()
}

#[tokio::test]
async fn sqlite_store_passes_contract_harness_after_legacy_invalidation() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("open in-memory sqlite"));

    // --- Layer 1: legacy invalidate-on-upgrade ----------------------------------
    build_legacy_schema(&db).await;
    insert_legacy_token_row(&db).await;

    // The legacy identity is reachable via the legacy `get_by_token` SQL path
    // before the migration runs.
    let store = DbUserIdentityStore::sqlite(db.clone());
    let pre = store.get_by_token("legacy-token-abc").await;
    assert_eq!(
        pre.map(|i| i.user_id).as_deref(),
        Some("legacy-user-1"),
        "legacy token must resolve to the legacy identity before migration"
    );

    // Run migration 028's SQL body directly on the legacy table.
    apply_migration_28(&db).await;

    // New columns are present on the table.
    let cols = column_names(&*db, "bcs_user_identities").await;
    assert!(
        cols.iter().any(|c| c == "session_id"),
        "session_id column must exist after migration; got {cols:?}"
    );
    assert!(
        cols.iter().any(|c| c == "session_revision"),
        "session_revision column must exist after migration; got {cols:?}"
    );
    assert!(
        cols.iter().any(|c| c == "session_expires_at"),
        "session_expires_at column must exist after migration; got {cols:?}"
    );

    // Legacy invalidate-on-upgrade: the legacy `token` is cleared and the
    // legacy `get_by_token` SQL path returns `None`.
    let invalidated = store.get_by_token("legacy-token-abc").await;
    assert!(
        invalidated.is_none(),
        "legacy token must be invalidated by the migration; got {invalidated:?}"
    );

    // Read session_revision on the legacy scope must yield 0: the migration
    // left the row with DEFAULT 0 and no session installed, consistent with
    // the contract's missing-install state.
    let legacy_scope = AuthSessionScope {
        user_id: "legacy-user-1".to_string(),
        provider: "cookie".to_string(),
        env: "dev".to_string(),
    };
    assert_eq!(
        store
            .read_session_revision(&legacy_scope)
            .await
            .expect("read_session_revision on legacy row must be Ok"),
        0,
        "legacy session_revision must be 0 after migration"
    );

    // --- Layer 2: contract harness pass-through ---------------------------------
    // Build a fresh scope via the legacy `ensure_identity` path and run the
    // central `AuthSessionRepoPort` conformance suite.
    let harness_scope = {
        let user_id = store
            .ensure_identity("cookie", "ext-harness", Some("alice"), None, "dev")
            .await
            .expect("ensure_identity for contract harness");
        AuthSessionScope {
            user_id,
            provider: "cookie".to_string(),
            env: "dev".to_string(),
        }
    };

    bcs_test_support::contract::repo::auth_session::auth_session_repo_port_contract_tests(
        &store,
        harness_scope.clone(),
    )
    .await;

    // --- Layer 3: re-running migration 28's UPDATE does not clear sessions --------
    // Install a fresh session on the harness scope and re-run ONLY the
    // migration's `UPDATE` guard. The new row has `session_id` set, so the
    // WHERE clause matched no rows; the session payload survives.
    let fresh_scope = {
        let user_id = store
            .ensure_identity("cookie", "ext-rerun", Some("bob"), None, "dev")
            .await
            .expect("ensure_identity for re-run check");
        AuthSessionScope {
            user_id,
            provider: "cookie".to_string(),
            env: "dev".to_string(),
        }
    };
    store
        .install_login_session(InstallAuthSession {
            scope: fresh_scope.clone(),
            expected_revision: 0,
            next: AuthSessionVersion {
                session_id: "sid-rerun".to_string(),
                revision: 1,
                token_hash: "hash-rerun".to_string(),
            },
            expires_at: 5_000_000_000,
        })
        .await
        .expect("install_login_session for re-run check");

    // Re-run only the migration's UPDATE body. The full migration body cannot
    // re-run prior ALTERs (SQLite 3.26 has no DROP COLUMN; ADD COLUMN on a
    // duplicate errors). The runner itself is idempotent through
    // `bcs_schema_migrations`; here we directly check the UPDATE-only
    // idempotency that the SQL guard provides.
    let update_sql = migration_28_update_statement();
    let result = db
        .execute(DbStatement::new(update_sql))
        .await
        .expect("re-run migration UPDATE must be Ok");
    assert_eq!(
        result.affected_rows, 0,
        "re-running migration UPDATE must be a no-op once a session is installed (affected_rows=0); \
         got affected_rows={}",
        result.affected_rows
    );
    // Session payload survives the re-run.
    let snapshot = store
        .get_session_by_hash(&fresh_scope, "hash-rerun", 1_000_000_000)
        .await
        .expect("get_session_by_hash after re-run must be Ok")
        .expect("hash-rerun must remain queryable after the re-run UPDATE");
    assert_eq!(snapshot.version.session_id, "sid-rerun");

    // --- Cross-scope legacy survival --------------------------------------------
    // The legacy row's identity columns remain queryable; only the legacy
    // token was invalidated.
    let by_id = store
        .get_by_user_id_display("legacy-user-1")
        .await
        .expect("legacy identity row must still exist after upgrade");
    assert_eq!(by_id.user_id, "legacy-user-1");
    assert_eq!(by_id.auth_source, "cookie");
    assert_eq!(by_id.external_user_name.as_deref(), Some("Legacy Display"));
}

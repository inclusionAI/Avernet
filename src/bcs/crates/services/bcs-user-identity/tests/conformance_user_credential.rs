use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::UserCredentialRepoPort;
use bcs_user_identity::{DbUserCredentialStore, MemoryUserCredentialRepo};

/// Generic credential-port contract, run against every impl.
async fn run_credential_contract<R: UserCredentialRepoPort + ?Sized>(repo: &R) {
    // create + find
    repo.create_credential("u-abc", "alice", "phc:argon2:...", "dev")
        .await
        .expect("create");
    let found = repo
        .find_for_login("alice", "dev")
        .await
        .expect("find")
        .expect("credential present");
    assert_eq!(found.user_id, "u-abc");
    assert_eq!(found.username, "alice");
    assert_eq!(found.password_hash, "phc:argon2:...");
    assert_eq!(found.env, "dev");

    // unknown user -> None
    assert!(repo.find_for_login("nobody", "dev").await.unwrap().is_none());

    // duplicate username -> "duplicate"
    let err = repo
        .create_credential("u-def", "alice", "h", "dev")
        .await
        .unwrap_err();
    assert_eq!(err, "duplicate");

    // duplicate user_id -> "duplicate"
    let err = repo
        .create_credential("u-abc", "alice2", "h", "dev")
        .await
        .unwrap_err();
    assert_eq!(err, "duplicate");

    // env partitioning: same username, different env is allowed and distinct
    repo.create_credential("u-xyz", "alice", "h", "prod")
        .await
        .expect("create prod");
    let prod = repo.find_for_login("alice", "prod").await.unwrap().unwrap();
    assert_eq!(prod.user_id, "u-xyz");
    assert_eq!(prod.env, "prod");
}

#[tokio::test]
async fn memory_repo_passes_credential_contract() {
    let repo = MemoryUserCredentialRepo::new();
    run_credential_contract(&repo).await;
}

#[tokio::test]
async fn sqlite_store_passes_credential_contract() {
    let db = sqlite_db().await;
    let repo = DbUserCredentialStore::sqlite(db);
    run_credential_contract(&repo).await;
}

// MySQL conformance parity (Rule 25):
//
// `DbUserCredentialStore::mysql` and `::sqlite` are the SAME struct over the
// same `dyn DbPlugin` SQL; the credential SQL has no flavor branch, so every
// statement is flavor-independent. The sqlite run above exercises the shared
// production code path. A real MySQL/OB server is not available in CI, so the
// live MySQL path is verified by dev/pre smoke tests rather than here. This
// test pins the structural parity (both constructors build the same store type
// and report the expected flavor) so the assumption can't silently drift.
#[tokio::test]
async fn mysql_store_shares_sqlite_code_path() {
    let db = sqlite_db().await; // standing in for any DbPlugin; not queried here
    let mysql_store = DbUserCredentialStore::mysql(db.clone());
    let sqlite_store = DbUserCredentialStore::sqlite(db);

    assert_eq!(mysql_store.flavor(), bcs_db_api::DbSqlFlavor::Mysql);
    assert_eq!(sqlite_store.flavor(), bcs_db_api::DbSqlFlavor::Sqlite);
    // Same concrete type -> same SQL/code path.
    assert_eq!(
        std::any::type_name_of_val(&mysql_store),
        std::any::type_name_of_val(&sqlite_store),
    );
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    // SQLite-compatible DDL mirroring migration 009 + v9's logical shape.
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_user_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            env TEXT NOT NULL
        )",
    ))
    .await
    .expect("create user credential table");
    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX uk_user_creds_user ON bcs_user_credentials(user_id, env)",
    ))
    .await
    .expect("idx user");
    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX uk_user_creds_username ON bcs_user_credentials(username, env)",
    ))
    .await
    .expect("idx username");
    db
}

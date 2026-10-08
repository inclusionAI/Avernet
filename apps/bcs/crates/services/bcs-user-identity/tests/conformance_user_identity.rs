use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::{DbUserIdentityStore, MemoryUserIdentityRepo};

async fn run_contract<R: UserIdentityRepoPort + ?Sized>(repo: &R) {
    // First login allocates a 12-char internal id.
    let id1 = repo.ensure_identity("cookie", "12345", Some("张三"), None, "dev").await.unwrap();
    assert_eq!(id1.len(), 12);
    assert!(id1.chars().all(|c| c.is_ascii_alphanumeric()));

    // Repeat login is idempotent on (auth_source, external_user_id, env).
    let id2 = repo.ensure_identity("cookie", "12345", Some("张三改名"), Some("https://img.url/new"), "dev").await.unwrap();
    assert_eq!(id1, id2);

    // Different external id -> different internal id.
    let id3 = repo.ensure_identity("cookie", "99999", None, None, "dev").await.unwrap();
    assert_ne!(id1, id3);

    // Different env partitions the same external id.
    let id4 = repo.ensure_identity("cookie", "12345", None, None, "prod").await.unwrap();
    assert_ne!(id1, id4);

    // Forward lookup.
    assert_eq!(repo.lookup_user_id("cookie", "12345", "dev").await.as_deref(), Some(id1.as_str()));
    assert!(repo.lookup_user_id("cookie", "nobody", "dev").await.is_none());

    // Reverse lookup: user_id + auth_source -> external_user_id.
    assert_eq!(repo.lookup_by_user_id(&id1, "cookie").await.as_deref(), Some("12345"));
    assert_eq!(repo.lookup_by_user_id(&id3, "cookie").await.as_deref(), Some("99999"));
    assert!(repo.lookup_by_user_id("nonexistent", "cookie").await.is_none());
    assert!(repo.lookup_by_user_id(&id1, "google").await.is_none());

    // update_token: write token, then find by token.
    repo.update_token(&id1, "jwt-abc-123", 9999).await.unwrap();
    let by_token = repo.get_by_token("jwt-abc-123").await.expect("should find by token");
    assert_eq!(by_token.user_id, id1);
    assert_eq!(by_token.auth_source, "cookie");
    // external_user_name tracks the provider's latest name (refreshed on the
    // repeat login above)...
    assert_eq!(by_token.external_user_name.as_deref(), Some("张三改名"));
    // ...but the internal user_name is initialized from the FIRST login's
    // external name and is NOT overwritten on subsequent logins.
    assert_eq!(by_token.user_name.as_deref(), Some("张三"));
    // avatar was updated on the repeat ensure_identity call above
    assert_eq!(by_token.avatar.as_deref(), Some("https://img.url/new"));

    // get_by_token: unknown token returns None.
    assert!(repo.get_by_token("nonexistent-token").await.is_none());

    // update_token: overwrite token (single-session model).
    repo.update_token(&id1, "jwt-new-token", 10000).await.unwrap();
    assert!(repo.get_by_token("jwt-abc-123").await.is_none(), "old token should no longer match");
    let by_new = repo.get_by_token("jwt-new-token").await.expect("should find by new token");
    assert_eq!(by_new.user_id, id1);

    // get_by_user_id_display: look up by user_id.
    let by_id = repo.get_by_user_id_display(&id1).await.expect("should find by user_id");
    assert_eq!(by_id.user_id, id1);
    assert_eq!(by_id.user_name.as_deref(), Some("张三"));
    assert_eq!(by_id.avatar.as_deref(), Some("https://img.url/new"));

    // get_by_user_id_display: unknown user_id returns None.
    assert!(repo.get_by_user_id_display("nonexistent").await.is_none());
}

#[tokio::test]
async fn memory_repo_passes_contract() {
    let repo = MemoryUserIdentityRepo::new();
    run_contract(&repo).await;
}

#[tokio::test]
async fn sqlite_store_passes_contract() {
    let db = sqlite_db().await;
    let repo = DbUserIdentityStore::sqlite(db);
    run_contract(&repo).await;
}

// MySQL conformance parity (Rule 25):
//
// `DbUserIdentityStore::mysql` and `::sqlite` are the SAME struct over the same
// `dyn DbPlugin` SQL; `flavor` differs only in `update_token`'s timestamp
// expression (`FROM_UNIXTIME(?)` vs `datetime(?, 'unixepoch')`) — every other
// statement is flavor-independent. The sqlite run above therefore exercises the
// shared production code path. A real MySQL/OB server is not available in CI,
// so the live MySQL path is verified by the dev/pre smoke tests rather than
// here. This test pins the structural parity (both constructors build the same
// store type and report the expected flavor) so the assumption can't silently
// drift.
#[tokio::test]
async fn mysql_store_shares_sqlite_code_path() {
    use bcs_db_api::DbSqlFlavor;

    let db = sqlite_db().await; // standing in for any DbPlugin; not queried here
    let mysql_store = DbUserIdentityStore::mysql(db.clone());
    let sqlite_store = DbUserIdentityStore::sqlite(db);

    assert_eq!(mysql_store.flavor(), DbSqlFlavor::Mysql);
    assert_eq!(sqlite_store.flavor(), DbSqlFlavor::Sqlite);
    // Same concrete type → same SQL/code path apart from the flavor branch.
    assert_eq!(
        std::any::type_name_of_val(&mysql_store),
        std::any::type_name_of_val(&sqlite_store),
    );
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    // SQLite-compatible DDL mirroring migration 013 + 015's logical shape.
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_user_identities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id VARCHAR(32) NOT NULL,
            auth_source VARCHAR(64) NOT NULL,
            external_user_id VARCHAR(512) NOT NULL,
            user_name VARCHAR(256),
            external_user_name VARCHAR(256),
            avatar VARCHAR(1024),
            token VARCHAR(64),
            token_expire_at TIMESTAMP,
            env VARCHAR(64) NOT NULL,
            UNIQUE (user_id),
            UNIQUE (auth_source, external_user_id, env)
        )",
    ))
    .await
    .expect("create user identity table");
    db
}
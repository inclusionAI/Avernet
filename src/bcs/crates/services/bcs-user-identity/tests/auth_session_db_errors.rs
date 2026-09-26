#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test assertions intentionally fail fast"
)]

//! Fault-injection and corruption classification for the SQL
//! `AuthSessionRepoPort` implementation on `DbUserIdentityStore`.
//!
//! The fixture wraps an `Arc<LocalSqliteDbPlugin>` with a `FaultyDbPlugin`
//! decorator that delegates every `DbPlugin` call but injects
//! `DbError::Backend("injected".into())` for the designated operation
//! (`query` / `execute` / `transaction`, selectable via `FailureTarget`). The
//! test asserts each of the five port methods classifies the injected
//! storage fault as `Err(AuthSessionStoreError::Unavailable)` — never `.ok()`
//! discard, never `CorruptRecord`, never `Conflict`/`NotCurrent`.
//!
//! A second `MockRowsDbPlugin` returns craft `DbRow` payloads whose column
//! values fail to decode (`session_revision` is a string, etc.). Those
//! exercises assert the impl maps decode failures to
//! `Err(AuthSessionStoreError::CorruptRecord)` — a structurally valid row
//! whose fields are unusable — never silently repaired and never
//! `Unavailable`.
//!
//! Finally the suite asserts the contract's hard invariant: **0 affected
//! rows is NOT an error**. `install_login_session` and `rotate_session` on a
//! stale revision return `Ok(AuthSessionWrite::Conflict)`; `revoke_session`
//! on an absent `session_id` returns `Ok(AuthSessionRevoke::NotCurrent)`.
//! Every assertion discriminates the specific enum variant.

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionStoreError,
    AuthSessionVersion, AuthSessionWrite, InstallAuthSession, RotateAuthSession,
};
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::DbUserIdentityStore;

/// Selects which `DbPlugin` operation the fault decorator injects with an
/// injected backend error.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FailureTarget {
    Query,
    Execute,
    Transaction,
}

/// Wraps the real `LocalSqliteDbPlugin` and injects `DbError::Backend` on
/// the designated operation. All other operations delegate. This proves the
/// store goes through the public `DbPlugin` surface for every conditional
/// write/read — no silent fallback path.
struct FaultyDbPlugin {
    inner: Arc<dyn DbPlugin>,
    fail_query: AtomicBool,
    fail_execute: AtomicBool,
    fail_transaction: AtomicBool,
}

impl FaultyDbPlugin {
    fn new(inner: Arc<dyn DbPlugin>, target: FailureTarget) -> Self {
        let me = Self {
            inner,
            fail_query: AtomicBool::new(false),
            fail_execute: AtomicBool::new(false),
            fail_transaction: AtomicBool::new(false),
        };
        match target {
            FailureTarget::Query => me.fail_query.store(true, Ordering::SeqCst),
            FailureTarget::Execute => me.fail_execute.store(true, Ordering::SeqCst),
            FailureTarget::Transaction => me.fail_transaction.store(true, Ordering::SeqCst),
        }
        me
    }
}

#[async_trait]
impl DbPlugin for FaultyDbPlugin {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if self.fail_query.load(Ordering::SeqCst) {
            return Err(DbError::Backend("injected".into()));
        }
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        if self.fail_execute.load(Ordering::SeqCst) {
            return Err(DbError::Backend("injected".into()));
        }
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        if self.fail_transaction.load(Ordering::SeqCst) {
            return Err(DbError::Backend("injected".into()));
        }
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

/// Minimal `DbPlugin` whose `query` returns a single craft `DbRow` (or empty
/// vec after one use) for testing decode-failure → `CorruptRecord`
/// classification. `execute`/`transaction`/`health_check` raise
/// `DbError::Backend("unused".into())` since the corruption tests never reach
/// the write paths.
struct MockRowsDbPlugin {
    next_query_rows: Mutex<Option<Vec<DbRow>>>,
}

impl MockRowsDbPlugin {
    fn with_rows(rows: Vec<DbRow>) -> Self {
        Self {
            next_query_rows: Mutex::new(Some(rows)),
        }
    }
}

#[async_trait]
impl DbPlugin for MockRowsDbPlugin {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let mut guard = self
            .next_query_rows
            .lock()
            .expect("mock rows mutex not poisoned");
        let rows = guard.take().unwrap_or_default();
        Ok(rows)
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        Err(DbError::Backend("unused in mock".into()))
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        Err(DbError::Backend("unused in mock".into()))
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

/// Convenience: build a real SQLite `LocalSqliteDbPlugin` with the legacy
/// `bcs_user_identities` table plus migration 028's session columns, then
/// install one live session under a freshly created scope and return the
/// `DbUserIdentityStore` plus the *actual* scope (the `user_id` is whatever
/// `ensure_identity` allocated — `scope_hint.user_id` is only used as a unique
/// external_user_id tag); the live row is installed at that scope with
/// session_id `sid-live` and hash `hash-live`, revision 1.
async fn real_store_with_session(
    scope_hint: AuthSessionScope,
) -> (Arc<dyn DbPlugin>, DbUserIdentityStore, AuthSessionScope) {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("open in-memory sqlite"));
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
            session_id TEXT,
            session_revision INTEGER NOT NULL DEFAULT 0,
            session_expires_at INTEGER NOT NULL DEFAULT 0,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )",
    ))
    .await
    .expect("create bcs_user_identities with session_* columns");
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

    let store = DbUserIdentityStore::sqlite(db.clone());
    let user_id = store
        .ensure_identity(
            &scope_hint.provider,
            &format!("ext-{}", scope_hint.user_id),
            Some("alice"),
            None,
            &scope_hint.env,
        )
        .await
        .expect("ensure_identity for fixture");
    let scope = AuthSessionScope {
        user_id,
        provider: scope_hint.provider,
        env: scope_hint.env,
    };
    store
        .install_login_session(InstallAuthSession {
            scope: scope.clone(),
            expected_revision: 0,
            next: AuthSessionVersion {
                session_id: "sid-live".to_string(),
                revision: 1,
                token_hash: "hash-live".to_string(),
            },
            expires_at: 5_000_000_000,
        })
        .await
        .expect("install live session for fixture");
    (db, store, scope)
}

fn scope_hint(tag: &str) -> AuthSessionScope {
    AuthSessionScope {
        user_id: tag.to_string(),
        provider: "cookie".to_string(),
        env: "dev".to_string(),
    }
}

#[tokio::test]
async fn read_session_revision_classifies_query_fault_as_unavailable() {
    let (db, _store, scope) = real_store_with_session(scope_hint("user-read-rev")).await;
    let faulty = Arc::new(FaultyDbPlugin::new(db, FailureTarget::Query));
    let store = DbUserIdentityStore::sqlite(faulty);

    let err = store
        .read_session_revision(&scope)
        .await
        .expect_err("query failure must surface Err");
    assert!(
        matches!(err, AuthSessionStoreError::Unavailable),
        "read_session_revision must classify query failure as Unavailable; got {err:?}"
    );
}

#[tokio::test]
async fn read_session_revision_classifies_corrupt_revision_as_corrupt_record() {
    let mut columns = BTreeMap::new();
    columns.insert(
        "session_revision".to_string(),
        DbValue::from("not-an-i64-string"),
    );
    let plugin = Arc::new(MockRowsDbPlugin::with_rows(vec![DbRow::new(columns)]));

    let store = DbUserIdentityStore::sqlite(plugin);
    let scope = AuthSessionScope {
        user_id: "user-corrupt".to_string(),
        provider: "cookie".to_string(),
        env: "dev".to_string(),
    };
    let err = store
        .read_session_revision(&scope)
        .await
        .expect_err("corrupt row must surface Err");
    assert!(
        matches!(err, AuthSessionStoreError::CorruptRecord),
        "read_session_revision must classify a non-i64 session_revision as CorruptRecord; got {err:?}"
    );
}

#[tokio::test]
async fn get_session_by_hash_classifies_query_fault_as_unavailable() {
    let (db, _store, scope) = real_store_with_session(scope_hint("user-get-hash")).await;
    let faulty = Arc::new(FaultyDbPlugin::new(db, FailureTarget::Query));
    let store = DbUserIdentityStore::sqlite(faulty);

    let err = store
        .get_session_by_hash(&scope, "hash-live", 1_000_000_000)
        .await
        .expect_err("query failure must surface Err");
    assert!(
        matches!(err, AuthSessionStoreError::Unavailable),
        "get_session_by_hash must classify query failure as Unavailable; got {err:?}"
    );
}

#[tokio::test]
async fn get_session_by_hash_classifies_corrupt_token_hash_as_corrupt_record() {
    // Construct a row whose `token` column carries an integer instead of a
    // string. Recovering the snapshot must surface `CorruptRecord`, not
    // silently coerce to a string or map to `Unavailable`.
    let mut columns = BTreeMap::new();
    columns.insert("user_id".to_string(), DbValue::from("u1"));
    columns.insert("auth_source".to_string(), DbValue::from("cookie"));
    columns.insert("env".to_string(), DbValue::from("dev"));
    columns.insert("session_id".to_string(), DbValue::from("sid-x"));
    columns.insert("session_revision".to_string(), DbValue::from(1_i64));
    columns.insert("token".to_string(), DbValue::from(42_i64)); // not a string
    columns.insert("session_expires_at".to_string(), DbValue::from(5_000_000_000_i64));
    columns.insert("external_user_id".to_string(), DbValue::from("ext-x"));
    let plugin = Arc::new(MockRowsDbPlugin::with_rows(vec![DbRow::new(columns)]));

    let store = DbUserIdentityStore::sqlite(plugin);
    let scope = AuthSessionScope {
        user_id: "u1".to_string(),
        provider: "cookie".to_string(),
        env: "dev".to_string(),
    };
    let err = store
        .get_session_by_hash(&scope, "hash-any", 1_000_000_000)
        .await
        .expect_err("corrupt token column must surface Err");
    assert!(
        matches!(err, AuthSessionStoreError::CorruptRecord),
        "get_session_by_hash must classify a non-string token as CorruptRecord; got {err:?}"
    );
}

#[tokio::test]
async fn install_login_session_classifies_execute_fault_as_unavailable() {
    let (db, _store, scope) = real_store_with_session(scope_hint("user-install")).await;
    let faulty = Arc::new(FaultyDbPlugin::new(db, FailureTarget::Execute));
    let store = DbUserIdentityStore::sqlite(faulty);

    let err = store
        .install_login_session(InstallAuthSession {
            scope,
            expected_revision: 1, // using revision 1 so the WHERE matches the live row
            next: AuthSessionVersion {
                session_id: "sid-install-2".to_string(),
                revision: 2,
                token_hash: "hash-install-2".to_string(),
            },
            expires_at: 6_000_000_000,
        })
        .await
        .expect_err("execute failure must surface Err");
    assert!(
        matches!(err, AuthSessionStoreError::Unavailable),
        "install_login_session must classify execute failure as Unavailable; got {err:?}"
    );
}

#[tokio::test]
async fn rotate_session_classifies_execute_fault_as_unavailable() {
    let (db, _store, scope) = real_store_with_session(scope_hint("user-rotate")).await;
    let faulty = Arc::new(FaultyDbPlugin::new(db, FailureTarget::Execute));
    let store = DbUserIdentityStore::sqlite(faulty);

    let err = store
        .rotate_session(RotateAuthSession {
            scope,
            expected: AuthSessionVersion {
                session_id: "sid-live".to_string(),
                revision: 1,
                token_hash: "hash-live".to_string(),
            },
            next: AuthSessionVersion {
                session_id: "sid-rotate-2".to_string(),
                revision: 2,
                token_hash: "hash-rotate-2".to_string(),
            },
            expires_at: 6_000_000_000,
            now: 2_000_000_000,
        })
        .await
        .expect_err("execute failure must surface Err");
    assert!(
        matches!(err, AuthSessionStoreError::Unavailable),
        "rotate_session must classify execute failure as Unavailable; got {err:?}"
    );
}

#[tokio::test]
async fn revoke_session_classifies_execute_fault_as_unavailable() {
    let (db, _store, scope) = real_store_with_session(scope_hint("user-revoke")).await;
    let faulty = Arc::new(FaultyDbPlugin::new(db, FailureTarget::Execute));
    let store = DbUserIdentityStore::sqlite(faulty);

    let err = store
        .revoke_session(&scope, "sid-live")
        .await
        .expect_err("execute failure must surface Err");
    assert!(
        matches!(err, AuthSessionStoreError::Unavailable),
        "revoke_session must classify execute failure as Unavailable; got {err:?}"
    );
}

#[tokio::test]
async fn transaction_failure_target_does_not_apply_through_impl() {
    // The SQL impl uses `query` and `execute`; it never opens a transaction.
    // Asserting that the transaction-fault target classifies as Unavailable
    // is not possible through our port (the path is unreached), but we still
    // verify the decorator injects correctly so an impl regression that
    // later switches to a transaction is caught: an unused `FailureTarget::Transaction`
    // shows up as Unavailable on whichever method it actually goes through.
    // This test documents the expectation that no port method currently
    // invokes `transaction`, by exercising install (which uses `execute`)
    // against a `FailureTarget::Transaction` plugin, expecting success.
    let (db, _store, scope) = real_store_with_session(scope_hint("user-tx-target")).await;
    let faulty = Arc::new(FaultyDbPlugin::new(db, FailureTarget::Transaction));
    let store = DbUserIdentityStore::sqlite(faulty);

    // Install against the SAME scope with expected=1 (the live row); the impl
    // goes through `execute`, not `transaction`, so the injected
    // transaction fault is not triggered and the install proceeds normally.
    let outcome = store
        .install_login_session(InstallAuthSession {
            scope,
            expected_revision: 1,
            next: AuthSessionVersion {
                session_id: "sid-tx-install".to_string(),
                revision: 2,
                token_hash: "hash-tx-install".to_string(),
            },
            expires_at: 6_000_000_000,
        })
        .await
        .expect("install_login_session must be Ok when only the transaction target is faulted");
    assert_eq!(
        outcome,
        AuthSessionWrite::Applied,
        "transaction-target fault must NOT affect execute paths; got {outcome:?}"
    );
}

// --- Zero-affected-rows is NOT an error ----------------------------------------

#[tokio::test]
async fn install_login_session_zero_affected_rows_is_conflict_not_error() {
    let (db, _store, scope) = real_store_with_session(scope_hint("user-install-zero")).await;
    let store = DbUserIdentityStore::sqlite(db);

    // Install pretending the revision is still 0; the live row has revision
    // 1 (after the fixture's install), so the CAS WHERE matches 0 rows.
    let outcome = store
        .install_login_session(InstallAuthSession {
            scope,
            expected_revision: 0,
            next: AuthSessionVersion {
                session_id: "sid-conflict".to_string(),
                revision: 1,
                token_hash: "hash-conflict".to_string(),
            },
            expires_at: 5_000_000_000,
        })
        .await
        .expect("zero-affected-rows install must be Ok, never Err");
    assert_eq!(
        outcome,
        AuthSessionWrite::Conflict,
        "install_login_session 0 affected rows ≡ Conflict (CAS), never Err"
    );
}

#[tokio::test]
async fn rotate_session_zero_affected_rows_is_conflict_not_error() {
    let (db, _store, scope) = real_store_with_session(scope_hint("user-rotate-zero")).await;
    let store = DbUserIdentityStore::sqlite(db);

    // Rotate against a stale expected revision (current live is revision 1;
    // pretend it's 99). The WHERE matches 0 rows; the result is `Conflict`,
    // never an error.
    let outcome = store
        .rotate_session(RotateAuthSession {
            scope,
            expected: AuthSessionVersion {
                session_id: "sid-live".to_string(),
                revision: 99, // mismatched
                token_hash: "hash-live".to_string(),
            },
            next: AuthSessionVersion {
                session_id: "sid-rotate-2".to_string(),
                revision: 2,
                token_hash: "hash-rotate-2".to_string(),
            },
            expires_at: 6_000_000_000,
            now: 2_000_000_000,
        })
        .await
        .expect("zero-affected-rows rotate must be Ok, never Err");
    assert_eq!(
        outcome,
        AuthSessionWrite::Conflict,
        "rotate_session 0 affected rows ≡ Conflict (CAS), never Err"
    );
}

#[tokio::test]
async fn revoke_session_zero_affected_rows_is_notcurrent_not_error() {
    let (db, _store, scope) = real_store_with_session(scope_hint("user-revoke-zero")).await;
    let store = DbUserIdentityStore::sqlite(db);

    // Revoke using an unknown session_id; the WHERE matches 0 rows; the
    // result is `NotCurrent`, never an error.
    let outcome = store
        .revoke_session(&scope, "sid-unknown")
        .await
        .expect("zero-affected-rows revoke must be Ok, never Err");
    assert_eq!(
        outcome,
        AuthSessionRevoke::NotCurrent,
        "revoke_session 0 affected rows ≡ NotCurrent, never Err"
    );
}

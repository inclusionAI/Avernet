#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test assertions intentionally fail fast"
)]

//! Strict `AuthSessionIdentityPort` conformance for the bootstrap
//! `RepoAuthSessionIdentityPort` bridge.
//!
//! Exercises the production wiring end-to-end against a real
//! `LocalSqliteDbPlugin` + `DbUserIdentityStore` in three layers:
//!
//! 1. **Central harness pass-through**: builds a real SQLite
//!    `DbUserIdentityStore` (which implements both `AuthSessionRepoPort`
//!    and `UserIdentityRepoPort`), wraps it in the
//!    [`RepoAuthSessionIdentityPort`] bridge, and runs the central
//!    [`bcs_test_support::contract::plugin::auth_session_identity_port_contract_tests`]
//!    suite. The bridge's field-by-field translation must be observably
//!    identical to the persistence-layer repo harness.
//!
//! 2. **DB-fault classification**: wraps the SQLite plugin in the same
//!    `FaultyDbPlugin` decorator pattern Task 4 introduced (selectively
//!    failing `query`/`execute` with `DbError::Backend`) and asserts the
//!    bridge surfaces every fault as `Err(SessionStoreError::Unavailable)`
//!    — for `get_session_by_hash`, `install_login_session`,
//!    `rotate_session`, `revoke_session`, and `ensure_identity` (the
//!    legacy method's `Err(String)` → `Unavailable` mapping).
//!
//! 3. **Healthy-but-empty**: asserts a healthy store returns `Ok(None)`
//!    for an unknown hash (no collapsed-error masking) and `Ok(0)` for a
//!    fresh scope's revision read.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_auth_api::{
    AuthSessionIdentityPort, InstallSession, RotateSession, SessionRevoke, SessionScope,
    SessionStoreError, SessionVersion, SessionWrite,
};
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement,
    DbTransactionStep, DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::auth_session::AuthSessionRepoPort;
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::DbUserIdentityStore;

use bcs::identity_session_wiring::RepoAuthSessionIdentityPort;

/// Build a SQLite plugin + `DbUserIdentityStore` with the full
/// `bcs_user_identities` schema including the `session_*` columns from
/// migration 028. Returns the underlying `db` for the test (so fault
/// decorators can wrap it after the fact) plus the store.
async fn build_sqlite_store() -> (Arc<dyn DbPlugin>, DbUserIdentityStore) {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("open sqlite"));
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
    .expect("create bcs_user_identities");
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
    (db, store)
}

/// Build a fresh scope by allocating an internal `user_id` via the legacy
/// `ensure_identity` path. The `tag` becomes the `external_user_id`, so
/// each test run uses a disjoint identity row.
async fn fresh_scope(store: &DbUserIdentityStore, tag: &str) -> SessionScope {
    let user_id = store
        .ensure_identity("cookie", tag, Some("alice"), None, "dev")
        .await
        .expect("ensure_identity for fresh scope");
    SessionScope {
        user_id,
        provider: "cookie".to_string(),
        env: "dev".to_string(),
    }
}

#[tokio::test]
async fn sqlite_bridge_passes_identity_port_contract_harness() {
    let (_db, store) = build_sqlite_store().await;
    // One arc; coerce to both trait objects via two bindings (the bridge
    // holds `Arc<dyn AuthSessionRepoPort>` + `Arc<dyn UserIdentityRepoPort>`).
    let store: Arc<DbUserIdentityStore> = Arc::new(store);
    let scope = fresh_scope(&store, "ext-harness").await;

    let bridge = RepoAuthSessionIdentityPort::new(store.clone(), store.clone());
    bcs_test_support::contract::plugin::auth_session_identity_port_contract_tests(
        &bridge as &dyn AuthSessionIdentityPort,
        scope.clone(),
    )
    .await;
}

// --- DB-fault classification ---------------------------------------------------

/// Wraps a real `LocalSqliteDbPlugin` and injects `DbError::Backend` on the
/// designated operation. Modeled on the Task 4 `FaultyDbPlugin` in
/// `services/bcs-user-identity/tests/auth_session_db_errors.rs`.
struct FaultyDbPlugin {
    inner: Arc<dyn DbPlugin>,
    fail_query: AtomicBool,
    fail_execute: AtomicBool,
}

impl FaultyDbPlugin {
    fn new_query_failing(inner: Arc<dyn DbPlugin>) -> Self {
        Self {
            inner,
            fail_query: AtomicBool::new(true),
            fail_execute: AtomicBool::new(false),
        }
    }

    fn new_execute_failing(inner: Arc<dyn DbPlugin>) -> Self {
        Self {
            inner,
            fail_query: AtomicBool::new(false),
            fail_execute: AtomicBool::new(true),
        }
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
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

/// Build an empty SQLite store and a fixed live session under a known scope
/// (used by the fault tests to exercise `get_session_by_hash` / `rotate` /
/// `revoke`). The decorator replaces the inner `db` with `FaultyDbPlugin`
/// pointing at the same rows; the live session remains in storage.
async fn build_with_live_session(
    tag: &str,
) -> (
    Arc<dyn DbPlugin>,
    DbUserIdentityStore,
    SessionScope,
    bcs_service_api::port::repo::auth_session::AuthSessionVersion,
) {
    use bcs_service_api::port::repo::auth_session::{
        AuthSessionVersion as RepoVersion, InstallAuthSession,
    };
    let (db, store) = build_sqlite_store().await;
    let scope = fresh_scope(&store, tag).await;
    let repo_scope = bcs_service_api::port::repo::auth_session::AuthSessionScope {
        user_id: scope.user_id.clone(),
        provider: scope.provider.clone(),
        env: scope.env.clone(),
    };
    let repo_version = RepoVersion {
        session_id: "sid-live-fault".to_string(),
        revision: 1,
        token_hash: "hash-live-fault".to_string(),
    };
    store
        .install_login_session(InstallAuthSession {
            scope: repo_scope,
            expected_revision: 0,
            next: repo_version.clone(),
            expires_at: 5_000_000_000,
        })
        .await
        .expect("install live session for fault fixture");
    (db, store, scope, repo_version)
}

#[tokio::test]
async fn get_session_by_hash_surfaces_unavailable_on_query_fault() {
    let (db, _store, scope, _version) =
        build_with_live_session("ext-get-hash-fault").await;
    let faulty = Arc::new(FaultyDbPlugin::new_query_failing(db));
    let store: Arc<DbUserIdentityStore> = Arc::new(DbUserIdentityStore::sqlite(faulty));
    let bridge = RepoAuthSessionIdentityPort::new(store.clone(), store.clone());

    let err = bridge
        .get_session_by_hash(&scope, "hash-live-fault", 1_000_000_000)
        .await
        .expect_err("query fault must surface Err");
    assert!(
        matches!(err, SessionStoreError::Unavailable),
        "get_session_by_hash must classify query fault as Unavailable; got {err:?}"
    );
}

#[tokio::test]
async fn install_login_session_surfaces_unavailable_on_execute_fault() {
    let (db, _store, scope, _version) =
        build_with_live_session("ext-install-fault").await;
    let faulty = Arc::new(FaultyDbPlugin::new_execute_failing(db));
    let store: Arc<DbUserIdentityStore> = Arc::new(DbUserIdentityStore::sqlite(faulty));
    let bridge = RepoAuthSessionIdentityPort::new(store.clone(), store.clone());

    let err = bridge
        .install_login_session(InstallSession {
            scope,
            expected_revision: 1, // matches live so SQL would normally update
            next: SessionVersion {
                session_id: "sid-install-fault".to_string(),
                revision: 2,
                token_hash: "hash-install-fault".to_string(),
            },
            expires_at: 6_000_000_000,
        })
        .await
        .expect_err("execute fault must surface Err");
    assert!(
        matches!(err, SessionStoreError::Unavailable),
        "install_login_session must classify execute fault as Unavailable; got {err:?}"
    );
}

#[tokio::test]
async fn rotate_session_surfaces_unavailable_on_execute_fault() {
    let (db, _store, scope, _version) =
        build_with_live_session("ext-rotate-fault").await;
    let faulty = Arc::new(FaultyDbPlugin::new_execute_failing(db));
    let store: Arc<DbUserIdentityStore> = Arc::new(DbUserIdentityStore::sqlite(faulty));
    let bridge = RepoAuthSessionIdentityPort::new(store.clone(), store.clone());

    let err = bridge
        .rotate_session(RotateSession {
            scope,
            expected: SessionVersion {
                session_id: "sid-live-fault".to_string(),
                revision: 1,
                token_hash: "hash-live-fault".to_string(),
            },
            next: SessionVersion {
                session_id: "sid-rotate-fault".to_string(),
                revision: 2,
                token_hash: "hash-rotate-fault".to_string(),
            },
            expires_at: 6_000_000_000,
            now: 2_000_000_000,
        })
        .await
        .expect_err("execute fault must surface Err");
    assert!(
        matches!(err, SessionStoreError::Unavailable),
        "rotate_session must classify execute fault as Unavailable; got {err:?}"
    );
}

#[tokio::test]
async fn revoke_session_surfaces_unavailable_on_execute_fault() {
    let (db, _store, scope, _version) =
        build_with_live_session("ext-revoke-fault").await;
    let faulty = Arc::new(FaultyDbPlugin::new_execute_failing(db));
    let store: Arc<DbUserIdentityStore> = Arc::new(DbUserIdentityStore::sqlite(faulty));
    let bridge = RepoAuthSessionIdentityPort::new(store.clone(), store.clone());

    let err = bridge
        .revoke_session(&scope, "sid-live-fault")
        .await
        .expect_err("execute fault must surface Err");
    assert!(
        matches!(err, SessionStoreError::Unavailable),
        "revoke_session must classify execute fault as Unavailable; got {err:?}"
    );
}

#[tokio::test]
async fn ensure_identity_surfaces_unavailable_on_query_fault() {
    // ensure_identity goes through DbUserIdentityStore's SELECT path;
    // failing `query` must surface as the strict Unavailable, not be
    // swallowed into a fresh user_id allocation.
    let (db, _store, _scope, _version) =
        build_with_live_session("ext-ensure-fault").await;
    let faulty = Arc::new(FaultyDbPlugin::new_query_failing(db));
    let store: Arc<DbUserIdentityStore> = Arc::new(DbUserIdentityStore::sqlite(faulty));
    let bridge = RepoAuthSessionIdentityPort::new(store.clone(), store.clone());

    let err = bridge
        .ensure_identity(
            "cookie",
            "ext-ensure-fault-target",
            Some("Carol"),
            None,
            "dev",
        )
        .await
        .expect_err("ensure_identity with query fault must surface Err");
    assert!(
        matches!(err, SessionStoreError::Unavailable),
        "ensure_identity must classify legacy `Err(String)` from a query fault as Unavailable; got {err:?}"
    );
}

#[tokio::test]
async fn ensure_identity_surfaces_unavailable_on_execute_fault_after_miss() {
    // The legacy `ensure_identity` hits SELECT first (miss → null row) then
    // tries INSERT. With `query` healthy but `execute` broken, the miss+insert
    // path surfaces the execute error; the bridge MUST convert the
    // `Err(String)` to `SessionStoreError::Unavailable`.
    let (db, _store, _scope, _version) =
        build_with_live_session("ext-ensure-exec-fault").await;
    let faulty = Arc::new(FaultyDbPlugin::new_execute_failing(db));
    let store: Arc<DbUserIdentityStore> = Arc::new(DbUserIdentityStore::sqlite(faulty));
    let bridge = RepoAuthSessionIdentityPort::new(store.clone(), store.clone());

    let err = bridge
        .ensure_identity(
            "cookie",
            "ext-ensure-exec-fault-target",
            Some("Dana"),
            None,
            "dev",
        )
        .await
        .expect_err("ensure_identity with execute fault on insert must surface Err");
    assert!(
        matches!(err, SessionStoreError::Unavailable),
        "ensure_identity must classify the INSERT execute fault as Unavailable; got {err:?}"
    );
}

// --- Healthy-but-empty ---------------------------------------------------------

#[tokio::test]
async fn healthy_store_returns_ok_none_for_unknown_hash() {
    let (_db, store) = build_sqlite_store().await;
    let scope = fresh_scope(&store, "ext-unknown-hash").await;
    let store: Arc<DbUserIdentityStore> = Arc::new(store);
    let bridge = RepoAuthSessionIdentityPort::new(store.clone(), store.clone());

    let res = bridge
        .get_session_by_hash(&scope, "hash-never-existed", 1_000_000_000)
        .await
        .expect("healthy query must be Ok, never Err");
    assert!(res.is_none(), "unknown hash returns Ok(None)");
}

#[tokio::test]
async fn healthy_store_returns_ok_zero_for_fresh_scope() {
    let (_db, store) = build_sqlite_store().await;
    let store: Arc<DbUserIdentityStore> = Arc::new(store);
    let scope = SessionScope {
        user_id: "user-never-installed".to_string(),
        provider: "cookie".to_string(),
        env: "dev".to_string(),
    };
    let bridge = RepoAuthSessionIdentityPort::new(store.clone(), store.clone());
    let rev = bridge
        .read_session_revision(&scope)
        .await
        .expect("healthy read on a never-installed scope must be Ok, not Err");
    assert_eq!(
        rev, 0,
        "a scope with no identity row yields revision 0 (NOT an error)"
    );
}

#[tokio::test]
async fn revoke_unknown_session_id_returns_notcurrent() {
    let (_db, store) = build_sqlite_store().await;
    let scope = fresh_scope(&store, "ext-revoke-unknown").await;
    let store: Arc<DbUserIdentityStore> = Arc::new(store);
    let bridge = RepoAuthSessionIdentityPort::new(store.clone(), store.clone());
    let out = bridge
        .revoke_session(&scope, "sid-never-existed")
        .await
        .expect("revoke of unknown sid must be Ok, never Err");
    assert_eq!(
        out,
        SessionRevoke::NotCurrent,
        "revoke on an unknown session_id returns NotCurrent, never Err"
    );
}

#[tokio::test]
async fn install_against_stale_revision_returns_conflict() {
    let (_db, store) = build_sqlite_store().await;
    let store: Arc<DbUserIdentityStore> = Arc::new(store);
    let scope = fresh_scope(&store, "ext-install-stale").await;
    let bridge = RepoAuthSessionIdentityPort::new(store.clone(), store.clone());
    // Install A first.
    bridge
        .install_login_session(InstallSession {
            scope: scope.clone(),
            expected_revision: 0,
            next: SessionVersion {
                session_id: "sid-stale-1".to_string(),
                revision: 1,
                token_hash: "hash-stale-1".to_string(),
            },
            expires_at: 5_000_000_000,
        })
        .await
        .expect("initial install");
    // Stale install (pretend live revision is 0; live is now 1).
    let out = bridge
        .install_login_session(InstallSession {
            scope,
            expected_revision: 0,
            next: SessionVersion {
                session_id: "sid-stale-2".to_string(),
                revision: 1,
                token_hash: "hash-stale-2".to_string(),
            },
            expires_at: 8_000_000_000,
        })
        .await
        .expect("stale install must be Ok, never Err");
    assert_eq!(
        out,
        SessionWrite::Conflict,
        "install against stale revision returns Conflict, never Err"
    );
}

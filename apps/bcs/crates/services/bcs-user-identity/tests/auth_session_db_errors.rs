//! Real-store fault classification and access-cost evidence for the SQL
//! auth-session store (V1 API auth plugin chain).
//!
//! A `FaultDbPlugin` decorator wraps the REAL local SQLite plugin: at a
//! selected fault point (`query`, `execute`, or `transaction`) it returns
//! `DbError::Backend("injected")` and delegates everything else, so each
//! store operation is proven to propagate the failure instead of coercing it
//! — no `.ok()` anywhere in the chain. Row/type errors map to `CorruptRecord`.
//! The same decorator counts `query` / `execute` / `transaction` calls per
//! operation, pinning the SQL server's access budgets: strict verify ≤ 1
//! SELECT, login ≤ 1 SELECT + 1 UPDATE (read revision, then CAS install),
//! refresh ≤ 1 UPDATE, logout ≤ 1 UPDATE, at most one row each, and the
//! session store never opens a transaction.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionStoreError,
    AuthSessionVersion, AuthSessionWrite, InstallAuthSession, RotateAuthSession,
};
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::DbUserIdentityStore;

const NOW: u64 = 1_800_000_000;

/// Where the decorator injects `DbError::Backend("injected")`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FaultPoint {
    Query,
    Execute,
    Transaction,
}

#[derive(Default)]
struct CallCounts {
    queries: AtomicU64,
    executes: AtomicU64,
    transactions: AtomicU64,
    rows_returned: AtomicU64,
}

impl CallCounts {
    fn snapshot(&self) -> (u64, u64, u64, u64) {
        (
            self.queries.load(Ordering::SeqCst),
            self.executes.load(Ordering::SeqCst),
            self.transactions.load(Ordering::SeqCst),
            self.rows_returned.load(Ordering::SeqCst),
        )
    }
}

/// A `DbPlugin` decorator over the real SQLite plugin: one optional fault
/// point returns `Backend("injected")`; everything else delegates. Counts
/// every call and every row handed back by a delegated query.
struct FaultDbPlugin {
    inner: Arc<dyn DbPlugin>,
    fault: Option<FaultPoint>,
    counts: Arc<CallCounts>,
}

#[async_trait]
impl DbPlugin for FaultDbPlugin {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if self.fault == Some(FaultPoint::Query) {
            return Err(DbError::Backend("injected".into()));
        }
        let rows = self.inner.query(statement).await?;
        self.counts.queries.fetch_add(1, Ordering::SeqCst);
        self.counts
            .rows_returned
            .fetch_add(rows.len() as u64, Ordering::SeqCst);
        Ok(rows)
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        if self.fault == Some(FaultPoint::Execute) {
            return Err(DbError::Backend("injected".into()));
        }
        let result = self.inner.execute(statement).await?;
        self.counts.executes.fetch_add(1, Ordering::SeqCst);
        Ok(result)
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        if self.fault == Some(FaultPoint::Transaction) {
            return Err(DbError::Backend("injected".into()));
        }
        let results = self.inner.transaction(steps).await?;
        self.counts.transactions.fetch_add(1, Ordering::SeqCst);
        Ok(results)
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

/// One shared real SQLite database plus shared counters, so stores built over
/// different faulting wrappers still observe the same persisted rows and the
/// same call counts.
struct Harness {
    real: Arc<dyn DbPlugin>,
    counts: Arc<CallCounts>,
}

impl Harness {
    async fn migrated() -> Self {
        let real: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
        real.execute(DbStatement::new(
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
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT DEFAULT NULL,
                session_revision INTEGER NOT NULL DEFAULT 0,
                session_expires_at INTEGER NOT NULL DEFAULT 0
            )",
        ))
        .await
        .expect("create migrated identity table");
        Self {
            real,
            counts: Arc::new(CallCounts::default()),
        }
    }

    fn wrapper(&self, fault: Option<FaultPoint>) -> Arc<dyn DbPlugin> {
        Arc::new(FaultDbPlugin {
            inner: Arc::clone(&self.real),
            fault,
            counts: Arc::clone(&self.counts),
        })
    }

    fn store(&self, fault: Option<FaultPoint>) -> DbUserIdentityStore {
        DbUserIdentityStore::sqlite(self.wrapper(fault))
    }
}

fn install_command(
    scope: &AuthSessionScope,
    expected_revision: u64,
    next: AuthSessionVersion,
    expires_at: u64,
) -> InstallAuthSession {
    InstallAuthSession {
        scope: scope.clone(),
        expected_revision,
        next,
        expires_at,
    }
}

/// Seed an identity row and return its trusted scope, then install a live
/// session so rotate/revoke have real current state to act on.
async fn session_scope(harness: &Harness, external: &str) -> AuthSessionScope {
    let repo = harness.store(None);
    let user_id = repo
        .ensure_identity("github", external, Some("故障分类用户"), None, "dev")
        .await
        .expect("seed identity row");
    let scope = AuthSessionScope {
        user_id,
        provider: "github".to_string(),
        env: "dev".to_string(),
    };
    assert_eq!(
        repo.install_login_session(install_command(
            &scope,
            0,
            AuthSessionVersion {
                session_id: format!("{external}-sid"),
                revision: 1,
                token_hash: format!("{external}-hash"),
            },
            NOW + 3_600,
        ))
        .await
        .expect("install the baseline session"),
        AuthSessionWrite::Applied
    );
    scope
}

/// Query-path failures are `Unavailable` for every strict read, and the store
/// never converts a failed query into absence or success.
#[tokio::test]
async fn injected_query_failures_classify_as_unavailable() {
    let harness = Harness::migrated().await;
    let scope = session_scope(&harness, "fault-query").await;
    let faulting = harness.store(Some(FaultPoint::Query));

    assert!(matches!(
        faulting.read_session_revision(&scope).await,
        Err(AuthSessionStoreError::Unavailable)
    ));
    assert!(matches!(
        faulting
            .get_session_by_hash(&scope, "fault-query-hash", NOW)
            .await,
        Err(AuthSessionStoreError::Unavailable)
    ));
    let unknown_scope = AuthSessionScope {
        user_id: "unreachable-user".to_string(),
        provider: "github".to_string(),
        env: "dev".to_string(),
    };
    assert!(matches!(
        faulting.get_session_by_hash(&unknown_scope, "any-hash", NOW).await,
        Err(AuthSessionStoreError::Unavailable)
    ));
}

/// Write-path failures are `Unavailable` for install, rotate, and revoke; the
/// store never reports them as `Conflict`/`NotCurrent` or as success.
#[tokio::test]
async fn injected_execute_failures_classify_as_unavailable() {
    let harness = Harness::migrated().await;
    let scope = session_scope(&harness, "fault-execute").await;
    let faulting = harness.store(Some(FaultPoint::Execute));

    assert!(matches!(
        faulting
            .install_login_session(install_command(
                &scope,
                1,
                AuthSessionVersion {
                    session_id: "fault-execute-sid-2".to_string(),
                    revision: 2,
                    token_hash: "fault-execute-hash-2".to_string(),
                },
                NOW + 3_600,
            ))
            .await,
        Err(AuthSessionStoreError::Unavailable)
    ));
    assert!(matches!(
        faulting
            .rotate_session(RotateAuthSession {
                scope: scope.clone(),
                expected: AuthSessionVersion {
                    session_id: "fault-execute-sid".to_string(),
                    revision: 1,
                    token_hash: "fault-execute-hash".to_string(),
                },
                next: AuthSessionVersion {
                    session_id: "fault-execute-sid".to_string(),
                    revision: 2,
                    token_hash: "fault-execute-hash-rotated".to_string(),
                },
                expires_at: NOW + 7_200,
                now: NOW + 10,
            })
            .await,
        Err(AuthSessionStoreError::Unavailable)
    ));
    assert!(matches!(
        faulting.revoke_session(&scope, "fault-execute-sid").await,
        Err(AuthSessionStoreError::Unavailable)
    ));

    // The failed writes left the current session untouched.
    let healthy = harness.store(None);
    assert!(
        healthy
            .get_session_by_hash(&scope, "fault-execute-hash", NOW)
            .await
            .expect("the session still resolves after failed writes")
            .is_some()
    );
}

/// A transaction fault fails only the transaction entry point; the store uses
/// single-statement `query`/`execute` paths, so its operations keep working
/// through the same decorator and a transaction failure elsewhere in the stack
/// never leaks into store outcomes.
#[tokio::test]
async fn injected_transaction_failure_stays_out_of_the_statement_paths() {
    let harness = Harness::migrated().await;
    let scope = session_scope(&harness, "fault-transaction").await;
    let faulting_wrapper = harness.wrapper(Some(FaultPoint::Transaction));

    // The decorator's transaction entry point is genuinely faulted…
    let err = faulting_wrapper
        .transaction(vec![DbTransactionStep::Execute(DbStatement::new(
            "UPDATE bcs_user_identities SET session_expires_at = 0 WHERE user_id = 'x'",
        ))])
        .await
        .expect_err("the injected transaction fault is observable at the plugin boundary");
    assert!(
        matches!(err, DbError::Backend(ref message) if message == "injected"),
        "transaction fault must be Backend(\"injected\"), got {err:?}"
    );

    // …while the store's own operations still work through the same wrapper.
    let repo = DbUserIdentityStore::sqlite(faulting_wrapper.clone());
    assert!(
        repo.get_session_by_hash(&scope, "fault-transaction-hash", NOW)
            .await
            .expect("statement reads proceed around the transaction fault")
            .is_some()
    );
    assert_eq!(
        repo.revoke_session(&scope, "fault-transaction-sid")
            .await
            .expect("statement writes proceed around the transaction fault"),
        AuthSessionRevoke::Revoked
    );
}

/// Persisted type corruption maps to `CorruptRecord`, never `Unavailable`:
/// a non-integer or negative `session_revision` cannot be a valid version,
/// and the store must not coerce it into a plausible revision or absence.
#[tokio::test]
async fn corrupt_revision_rows_classify_as_corrupt_record() {
    let harness = Harness::migrated().await;
    let repo = harness.store(None);
    for (i, arg) in [(1, DbValue::from("not-an-integer")), (2, DbValue::from(-5_i64))] {
        let external = format!("fault-corrupt-{i}");
        let user_id = repo
            .ensure_identity("github", &external, Some("损坏行用户"), None, "dev")
            .await
            .expect("seed corrupt-case identity");
        harness
            .real
            .execute(DbStatement::with_params(
                "UPDATE bcs_user_identities \
                 SET session_revision = ?, session_id = ?, token = ?, session_expires_at = ? \
                 WHERE user_id = ?",
                vec![
                    arg,
                    DbValue::from(format!("{external}-sid")),
                    DbValue::from(format!("{external}-hash")),
                    DbValue::from((NOW + 3_600) as i64),
                    DbValue::from(user_id),
                ],
            ))
            .await
            .expect("plant the corrupt revision");
        let scope = AuthSessionScope {
            user_id: repo.lookup_user_id("github", &external, "dev")
                .await
                .expect("planted row lookup"),
            provider: "github".to_string(),
            env: "dev".to_string(),
        };

        assert!(
            matches!(
                repo.read_session_revision(&scope).await,
                Err(AuthSessionStoreError::CorruptRecord)
            ),
            "case {external}: reading a corrupt revision must classify CorruptRecord"
        );
        assert!(
            matches!(
                repo.get_session_by_hash(&scope, &format!("{external}-hash"), NOW).await,
                Err(AuthSessionStoreError::CorruptRecord)
            ),
            "case {external}: a corrupt revision must not resolve as a snapshot"
        );
    }
}

/// The Task-4 reviewer's hand-corruption corner, pinned on real SQLite:
/// a row corrupted to the DOUBLE-checkable BIGINT maximum revision lets
/// the revoke predicate still match (scope + sid + non-empty hash), and
/// SQLite promotes the `session_revision + 1` bump to a REAL — so the
/// revoke itself reports `Revoked` while the NEXT classified read of the
/// row is persisted corruption (`CorruptRecord`), never a plausible
/// revision. (MySQL's equivalent raises BIGINT out-of-range at the
/// revoke, classifying `Unavailable`; that dialect difference is
/// arbitrated by the ignored live-MySQL gate, never by this file.)
#[tokio::test]
async fn revoke_on_hand_corrupted_max_revision_pins_the_sqlite_overflow() {
    let harness = Harness::migrated().await;
    let scope = session_scope(&harness, "revoke-overflow").await;

    // Hand-corrupt the stored revision to the BIGINT maximum (only
    // reachable by direct row manipulation, never by the engine's
    // checked bumps).
    harness
        .real
        .execute(DbStatement::with_params(
            "UPDATE bcs_user_identities SET session_revision = ? WHERE user_id = ?",
            vec![
                DbValue::from(i64::MAX),
                DbValue::from(scope.user_id.as_str()),
            ],
        ))
        .await
        .expect("plant the corrupted max revision");

    // The revoke predicate matches the corrupted row and commits.
    assert_eq!(
        harness
            .store(None)
            .revoke_session(&scope, "revoke-overflow-sid")
            .await
            .expect("the revoke itself commits on real SQLite"),
        AuthSessionRevoke::Revoked
    );

    // Every subsequent classified read of that row is persisted
    // corruption: the bump promoted the column to a non-integer REAL.
    assert!(
        matches!(
            harness
                .store(None)
                .read_session_revision(&scope)
                .await,
            Err(AuthSessionStoreError::CorruptRecord)
        ),
        "the SQLite promotion must be detected as corruption on the next read"
    );
}

/// Access-cost evidence, measured through the counting decorator over the real
/// plugin: one strict verify SELECT (≤ 1 row), login = 1 revision SELECT + 1
/// install UPDATE, refresh = 1 UPDATE, logout = 1 UPDATE, and zero
/// transactions across every operation.
#[tokio::test]
async fn per_operation_query_and_execute_budgets_hold() {
    let harness = Harness::migrated().await;
    let repo = harness.store(None);
    let scope = {
        let user_id = repo
            .ensure_identity("github", "budget-user", Some("预算用户"), None, "dev")
            .await
            .expect("seed budget identity");
        AuthSessionScope {
            user_id,
            provider: "github".to_string(),
            env: "dev".to_string(),
        }
    };

    // Strict verify: exactly one SELECT, at most one row.
    harness.counts.queries.store(0, Ordering::SeqCst);
    harness.counts.executes.store(0, Ordering::SeqCst);
    assert_eq!(
        repo.install_login_session(install_command(
            &scope,
            0,
            AuthSessionVersion {
                session_id: "budget-sid".to_string(),
                revision: 1,
                token_hash: "budget-hash".to_string(),
            },
            NOW + 3_600,
        ))
        .await
        .expect("budget install"),
        AuthSessionWrite::Applied
    );
    assert_eq!(harness.counts.snapshot().1, 1, "install spends exactly 1 UPDATE");

    harness.counts.queries.store(0, Ordering::SeqCst);
    harness.counts.rows_returned.store(0, Ordering::SeqCst);
    repo.get_session_by_hash(&scope, "budget-hash", NOW)
        .await
        .expect("budget strict verify")
        .expect("the budget session resolves");
    let (queries, _, _, rows) = harness.counts.snapshot();
    assert_eq!(queries, 1, "strict verify spends exactly 1 SELECT");
    assert_eq!(rows, 1, "strict verify reads at most one row");

    // Login batch: read the revision (1 SELECT), then CAS install (1 UPDATE).
    harness.counts.queries.store(0, Ordering::SeqCst);
    harness.counts.rows_returned.store(0, Ordering::SeqCst);
    let revision = repo
        .read_session_revision(&scope)
        .await
        .expect("budget revision read");
    assert_eq!(revision, 1);
    let (queries, _, _, rows) = harness.counts.snapshot();
    assert_eq!(queries, 1, "the revision read is exactly 1 SELECT");
    assert_eq!(rows, 1, "the revision read returns at most one row");

    // Refresh: exactly one UPDATE; the rotation CAS carries the whole refresh.
    harness.counts.executes.store(0, Ordering::SeqCst);
    assert_eq!(
        repo.rotate_session(RotateAuthSession {
            scope: scope.clone(),
            expected: AuthSessionVersion {
                session_id: "budget-sid".to_string(),
                revision: 1,
                token_hash: "budget-hash".to_string(),
            },
            next: AuthSessionVersion {
                session_id: "budget-sid".to_string(),
                revision: 2,
                token_hash: "budget-hash-rotated".to_string(),
            },
            expires_at: NOW + 7_200,
            now: NOW + 10,
        })
        .await
        .expect("budget refresh"),
        AuthSessionWrite::Applied
    );
    assert_eq!(harness.counts.snapshot().1, 1, "refresh spends exactly 1 UPDATE");

    // Logout: exactly one UPDATE, and it revokes the current session id.
    harness.counts.executes.store(0, Ordering::SeqCst);
    assert_eq!(
        repo.revoke_session(&scope, "budget-sid")
            .await
            .expect("budget logout"),
        AuthSessionRevoke::Revoked
    );
    assert_eq!(harness.counts.snapshot().1, 1, "logout spends exactly 1 UPDATE");

    // The store never opens a transaction for any session operation.
    assert_eq!(
        harness.counts.transactions.load(Ordering::SeqCst),
        0,
        "no session operation runs through a transaction"
    );

    // Zero-row budgets: an unknown scope's resolve still spends only its one
    // SELECT and reads zero rows.
    let unknown = AuthSessionScope {
        user_id: "budget-nobody".to_string(),
        provider: "github".to_string(),
        env: "dev".to_string(),
    };
    harness.counts.queries.store(0, Ordering::SeqCst);
    harness.counts.rows_returned.store(0, Ordering::SeqCst);
    assert!(
        repo.get_session_by_hash(&unknown, "budget-hash", NOW)
            .await
            .expect("unknown scope resolve")
            .is_none()
    );
    let (queries, _, _, rows) = harness.counts.snapshot();
    assert_eq!(queries, 1);
    assert_eq!(rows, 0);
}

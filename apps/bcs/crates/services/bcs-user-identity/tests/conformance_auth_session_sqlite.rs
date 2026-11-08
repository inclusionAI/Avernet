//! SQLite `AuthSessionRepoPort` conformance driver (V1 API auth plugin chain).
//!
//! Drives the REAL local SQLite plugin (`bcs-db-local`), not a fake: the
//! database starts from the pre-041 identity schema, the published migration
//! `migrations/sqlite/042_auth_session_version.sql` is applied through
//! `bcs_db_api::split_sql_statements` (the runner's splitter), and the store
//! executes its production SQL against the migrated rows. The central shared
//! harness from `bcs-test-support` runs verbatim; the driver additionally pins
//! the SQL-side behaviors the harness cannot see: legacy token invalidation,
//! zero-row `Ok(None)` reads, cross-env scope addressing, the snapshot
//! projection matching the in-memory store's mapping, and the strict-verify
//! query plan using the existing `uk_user_id` unique index.

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionVersion,
    AuthSessionWrite, InstallAuthSession, RotateAuthSession,
};
use bcs_service_api::UserIdentityRepoPort;
use bcs_test_support::contract::repo::auth_session_repo_port_contract_tests;
use bcs_user_identity::DbUserIdentityStore;

/// The published migration, split exactly like the bootstrap runner splits it.
const AUTH_SESSION_MIGRATION: &str =
    include_str!("../../../../migrations/sqlite/042_auth_session_version.sql");

const NOW: u64 = 1_800_000_000;

/// Create the pre-041 identity schema (the shape the upgrade path finds) with
/// the production index names, so query-plan assertions match the real
/// database. One legacy row carries an opaque browser token the migration must
/// invalidate.
async fn legacy_identity_db() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
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
    .expect("create legacy identity table");
    for index in [
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_user_id ON bcs_user_identities(user_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_external ON bcs_user_identities(auth_source, external_user_id, env)",
        "CREATE INDEX IF NOT EXISTS idx_external ON bcs_user_identities(external_user_id, env)",
    ] {
        db.execute(DbStatement::new(index))
            .await
            .expect("create legacy identity index");
    }
    db
}

/// Apply the 040 migration statements to the legacy schema via the shared
/// splitter. This mirrors the runner's first application; the runner-side
/// record/-repeat semantics ("the runner never clears twice") are pinned by
/// the bootstrap migration tests, which own `run_sqlite_migrations`.
async fn apply_auth_session_migration(db: &Arc<dyn DbPlugin>) {
    for sql in bcs_db_api::split_sql_statements(AUTH_SESSION_MIGRATION) {
        db.execute(DbStatement::new(sql))
            .await
            .expect("apply 040 migration statement");
    }
}

/// A migrated identity database with one legacy opaque-token row.
async fn migrated_identity_db() -> Arc<dyn DbPlugin> {
    let db = legacy_identity_db().await;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_user_identities \
         (user_id, auth_source, external_user_id, user_name, external_user_name, avatar, token, token_expire_at, env) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        vec![
            bcs_db_api::DbValue::from("legacy-browser-user"),
            bcs_db_api::DbValue::from("github"),
            bcs_db_api::DbValue::from("legacy-ext-1"),
            bcs_db_api::DbValue::from("Legacy 用户"),
            bcs_db_api::DbValue::from("Legacy 用户"),
            bcs_db_api::DbValue::from("https://avatars.example/legacy.png"),
            bcs_db_api::DbValue::from("legacy-opaque-token"),
            bcs_db_api::DbValue::from("2030-01-01 00:00:00"),
            bcs_db_api::DbValue::from("dev"),
        ],
    ))
    .await
    .expect("seed legacy identity row");
    apply_auth_session_migration(&db).await;
    db
}

/// Seed a fresh identity row through the store's own `ensure_identity` and
/// return its trusted session scope `(user_id, provider, env)`.
async fn seeded_scope(
    repo: &DbUserIdentityStore,
    external_user_id: &str,
    external_user_name: Option<&str>,
    avatar: Option<&str>,
    env: &str,
) -> AuthSessionScope {
    let user_id = repo
        .ensure_identity("github", external_user_id, external_user_name, avatar, env)
        .await
        .expect("seed identity row for the scope");
    assert!(!user_id.is_empty(), "ensure_identity must allocate an internal user id");
    AuthSessionScope {
        user_id,
        provider: "github".to_string(),
        env: env.to_string(),
    }
}

fn version(session_id: &str, revision: u64, token_hash: &str) -> AuthSessionVersion {
    AuthSessionVersion {
        session_id: session_id.to_string(),
        revision,
        token_hash: token_hash.to_string(),
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

/// The central harness verbatim, against the real SQLite plugin over the
/// migrated schema and a real seeded identity row.
#[tokio::test]
async fn sqlite_store_passes_auth_session_contract() {
    let db = migrated_identity_db().await;
    let repo = DbUserIdentityStore::sqlite(db);
    let scope = seeded_scope(&repo, "auth-session-ext-1", Some("Contract 用户"), None, "dev").await;
    auth_session_repo_port_contract_tests(&repo, scope).await;
}

/// The migration invalidates the historical opaque token on purpose and leaves
/// the three versioned-session columns present with their pinned defaults
/// (spec §8.5/§9: old browser sessions do not survive the strict chain).
#[tokio::test]
async fn migration_042_invalidates_legacy_tokens_and_adds_session_columns() {
    let db = migrated_identity_db().await;

    // New columns exist on every row of the upgraded table.
    let columns = db
        .query(DbStatement::new("PRAGMA table_info(bcs_user_identities)"))
        .await
        .expect("read identity table info");
    let names: Vec<String> = columns
        .iter()
        .map(|row| {
            row.get_string("name")
                .expect("column name is text")
                .expect("column name present")
        })
        .collect();
    for column in [
        "session_id",
        "session_revision",
        "session_expires_at",
    ] {
        assert!(names.iter().any(|name| name == column), "missing {column}");
    }

    // The legacy opaque token and its expiry are cleared exactly once.
    let rows = db
        .query(DbStatement::new(
            "SELECT token, token_expire_at, session_id, session_revision, session_expires_at \
             FROM bcs_user_identities WHERE user_id = 'legacy-browser-user'",
        ))
        .await
        .expect("read the legacy row after migration");
    let row = rows.first().expect("legacy row survives the migration");
    assert_eq!(
        row.get_string("token").expect("token column"),
        None,
        "the historical opaque token is invalidated by the migration"
    );
    assert_eq!(row.get_string("token_expire_at").expect("expiry column"), None);
    assert_eq!(row.get_string("session_id").expect("session id column"), None);
    assert_eq!(
        row.get_i64("session_revision").expect("revision column"),
        Some(0),
        "session_revision is non-null and defaults to 0"
    );
    assert_eq!(
        row.get_i64("session_expires_at").expect("expiry seconds column"),
        Some(0)
    );
}

/// The SQL snapshot projection must equal the in-memory store's mapping so the
/// shared harness observes the same semantics across stores: `username` is the
/// internal `user_name` with `external_user_name` as fallback, `display_name`
/// is the provider's latest external name, `avatar` comes along unchanged.
#[tokio::test]
async fn sqlite_snapshot_projection_matches_the_memory_mapping() {
    let db = migrated_identity_db().await;
    let repo = DbUserIdentityStore::sqlite(db);

    // First creation without any display name leaves the internal user_name
    // NULL; the next login sets only the external name, so the resolve path
    // exercises the fallback exactly like the memory store.
    let scope = seeded_scope(&repo, "auth-session-ext-2", None, None, "dev").await;
    repo.ensure_identity(
        "github",
        "auth-session-ext-2",
        Some("换名之后"),
        Some("https://avatars.example/refreshed.png"),
        "dev",
    )
    .await
    .expect("refresh the identity display fields");

    assert_eq!(
        repo.install_login_session(install_command(
            &scope,
            0,
            version("auth-session-sid-2", 1, "hash-sql-2"),
            NOW + 3_600,
        ))
        .await
        .expect("install the session"),
        AuthSessionWrite::Applied
    );
    let snapshot = repo
        .get_session_by_hash(&scope, "hash-sql-2", NOW)
        .await
        .expect("resolve the session by hash")
        .expect("the installed session resolves");
    assert_eq!(snapshot.scope, scope);
    assert_eq!(snapshot.version, version("auth-session-sid-2", 1, "hash-sql-2"));
    assert_eq!(snapshot.expires_at, NOW + 3_600);
    assert_eq!(snapshot.username, "换名之后", "user_name falls back to external_user_name");
    assert_eq!(snapshot.display_name.as_deref(), Some("换名之后"));
    assert_eq!(
        snapshot.avatar.as_deref(),
        Some("https://avatars.example/refreshed.png")
    );
}

/// Strict queries are absence-aware: a query that succeeds with zero rows is
/// `Ok(None)` (or revision 0 / `NotCurrent`), never an error and never a
/// synthetic snapshot. Writes targeting a scope without an identity row are
/// lost CAS races, never auto-creating identity rows.
#[tokio::test]
async fn strict_queries_map_zero_rows_to_absence_not_errors() {
    let db = migrated_identity_db().await;
    let repo = DbUserIdentityStore::sqlite(db);
    let scope = seeded_scope(&repo, "auth-session-ext-3", Some("用户三"), None, "dev").await;

    // Unknown scope: zero-row reads are Ok, not Err.
    let unknown = AuthSessionScope {
        user_id: "no-such-user".to_string(),
        provider: "github".to_string(),
        env: "dev".to_string(),
    };
    assert_eq!(
        repo.read_session_revision(&unknown)
            .await
            .expect("unknown scope revision is a zero, not an error"),
        0
    );
    assert!(
        repo.get_session_by_hash(&unknown, "hash-unknown", NOW)
            .await
            .expect("unknown scope resolve is not an error")
            .is_none()
    );
    assert_eq!(
        repo.revoke_session(&unknown, "auth-session-sid-3")
            .await
            .expect("unknown scope revoke is not an error"),
        AuthSessionRevoke::NotCurrent
    );

    // A session write never auto-creates the missing identity row.
    assert_eq!(
        repo.install_login_session(install_command(
            &unknown,
            0,
            version("auth-session-sid-3-ghost", 1, "hash-3-ghost"),
            NOW + 3_600,
        ))
        .await
        .expect("ghost install is a lost CAS, not an error"),
        AuthSessionWrite::Conflict
    );

    // An installed session: unknown hash and expired reads are Ok(None).
    assert_eq!(
        repo.install_login_session(install_command(
            &scope,
            0,
            version("auth-session-sid-3", 1, "hash-3"),
            NOW + 60,
        ))
        .await
        .expect("install the short-lived session"),
        AuthSessionWrite::Applied
    );
    assert!(
        repo.get_session_by_hash(&scope, "hash-wrong", NOW)
            .await
            .expect("wrong-hash resolve is not an error")
            .is_none()
    );
    assert!(
        repo.get_session_by_hash(&scope, "hash-3", NOW + 60)
            .await
            .expect("boundary read is not an error")
            .is_none(),
        "the session does not resolve at its expires_at boundary"
    );

    // An expired session cannot rotate (lost CAS), but still logs out.
    assert_eq!(
        repo.rotate_session(RotateAuthSession {
            scope: scope.clone(),
            expected: version("auth-session-sid-3", 1, "hash-3"),
            next: version("auth-session-sid-3", 2, "hash-3-next"),
            expires_at: NOW + 3_600,
            now: NOW + 100,
        })
        .await
        .expect("expired rotate is a lost CAS, not an error"),
        AuthSessionWrite::Conflict
    );
    assert_eq!(
        repo.revoke_session(&scope, "auth-session-sid-3")
            .await
            .expect("expired logout is not an error"),
        AuthSessionRevoke::Revoked,
        "expiry does not fence revocation"
    );
    assert!(
        repo.get_session_by_hash(&scope, "hash-3", NOW)
            .await
            .expect("revoked resolve is not an error")
            .is_none()
    );
}

/// Cross-env isolation: `env` participates in the scope address. The same
/// external identity in dev and prod gets independent rows and independent
/// sessions; sessions installed under one environment never resolve, rotate,
/// or revoke under the other.
#[tokio::test]
async fn cross_env_scopes_stay_independent_over_sql() {
    let db = migrated_identity_db().await;
    let repo = DbUserIdentityStore::sqlite(db);
    let dev = seeded_scope(&repo, "auth-session-ext-4", Some("用户四"), None, "dev").await;
    let prod = seeded_scope(&repo, "auth-session-ext-4", Some("用户四"), None, "prod").await;
    assert_ne!(dev.user_id, prod.user_id, "env partitions identity rows");

    assert_eq!(
        repo.install_login_session(install_command(
            &dev,
            0,
            version("auth-session-sid-4", 1, "hash-4-dev"),
            NOW + 3_600,
        ))
        .await
        .expect("install the dev session"),
        AuthSessionWrite::Applied
    );

    assert_eq!(
        repo.read_session_revision(&prod)
            .await
            .expect("prod revision read"),
        0,
        "prod has no session yet"
    );
    assert!(
        repo.get_session_by_hash(&prod, "hash-4-dev", NOW)
            .await
            .expect("prod resolve is not an error")
            .is_none(),
        "a prod-scoped read cannot see the dev session hash"
    );
    assert_eq!(
        repo.revoke_session(&prod, "auth-session-sid-4")
            .await
            .expect("prod revoke is not an error"),
        AuthSessionRevoke::NotCurrent
    );

    // The dev session is untouched by every wrong-env operation.
    let snapshot = repo
        .get_session_by_hash(&dev, "hash-4-dev", NOW)
        .await
        .expect("dev resolve")
        .expect("the dev session still resolves");
    assert_eq!(snapshot.version, version("auth-session-sid-4", 1, "hash-4-dev"));
}

/// Query-plan evidence for the strict verify: the one SELECT addresses the row
/// through the existing `uk_user_id` unique index. No new index, no cache, no
/// extra lookup statement.
#[tokio::test]
async fn strict_verify_query_plan_uses_the_existing_user_id_unique_index() {
    let db = legacy_identity_db().await;
    apply_auth_session_migration(&db).await;
    let rows = db
        .query(DbStatement::with_params(
            "EXPLAIN QUERY PLAN \
             SELECT session_id, session_revision, session_expires_at, \
                    COALESCE(user_name, external_user_name, ''), external_user_name, avatar \
             FROM bcs_user_identities \
             WHERE user_id = ? AND auth_source = ? AND env = ? AND token = ? \
               AND session_expires_at > ?",
            vec![
                bcs_db_api::DbValue::from("some-user"),
                bcs_db_api::DbValue::from("github"),
                bcs_db_api::DbValue::from("dev"),
                bcs_db_api::DbValue::from("hash"),
                bcs_db_api::DbValue::from(NOW as i64),
            ],
        ))
        .await
        .expect("explain the strict verify plan");
    let plan: Vec<String> = rows
        .iter()
        .map(|row| row.get_string("detail").expect("plan detail").expect("detail text"))
        .collect();
    assert!(
        plan.iter().any(|line| line.contains("uk_user_id")),
        "the strict verify must search via the existing uk_user_id unique index, got {plan:?}"
    );
    assert!(
        !plan.iter().any(|line| line.to_ascii_uppercase().contains("SCAN")),
        "no full table scan is acceptable, got {plan:?}"
    );
}

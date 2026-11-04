//! Live MySQL conformance gate for the strict auth-session store (V1 API
//! auth plugin chain, Task 13).
//!
//! The SAME shared Task 2 contract harness that the memory and SQLite
//! drivers run is repeated over the REAL `bcs-db-mysql` plugin on TWO
//! INDEPENDENT connections against one ISOLATED disposable MySQL
//! database (config from the test boundary ONLY: `BCS_TEST_MYSQL_URL`;
//! missing URL fails the gate immediately and loudly — this entry can
//! never report green without a database). On top of the contract it
//! drives the deterministic cross-connection race the HTTP races mirror:
//! one connection reads the current session version A; the OTHER
//! connection commits the by-sid revoke (logout); the first
//! connection's rotate CAS then reports a lost race (`Conflict`), and
//! the committed revoke is visible through BOTH connections.
//!
//! Locally there is no MySQL server, so this entry is `#[ignore]`d; the
//! command is:
//! `cargo test -p bcs-user-identity --test conformance_auth_session_mysql -- --ignored --nocapture`
//! The URL is never logged.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use bcs_db_mysql::MysqlDbPlugin;
use mysql_async::Opts;
#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;

use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionVersion,
    AuthSessionWrite, InstallAuthSession, RotateAuthSession,
};
use bcs_service_api::UserIdentityRepoPort;
use bcs_db_mysql::MysqlDbManager;
use bcs_test_support::contract::repo::auth_session_repo_port_contract_tests;
use bcs_user_identity::DbUserIdentityStore;

use mysql_chain::MysqlConformanceDb;

const NOW: u64 = 1_800_000_000;

/// A second INDEPENDENT MySQL plugin (its own manager/connection pool) on
/// the same disposable database as the conformance DB — the two
/// connections are the race parties. The URL and credentials come only
/// from the test boundary, never from this repository.
async fn second_connection_on_the_conformance_database(
) -> Arc<MysqlDbPlugin> {
    let url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set to run the ignored MySQL conformance gate");
    let opts =
        Opts::from_url(&url).expect("BCS_TEST_MYSQL_URL must be a valid MySQL URL");
    let mut config = MysqlDbConfig::new()
        .with_database(opts.db_name().expect(
            "BCS_TEST_MYSQL_URL must include an isolated disposable database name",
        ))
        .with_connection(MysqlConnectionConfig {
            connection_type: "direct".to_string(),
            host: Some(opts.ip_or_hostname().to_string()),
            port: Some(opts.tcp_port()),
            user: opts.user().map(str::to_string),
            password: opts.pass().map(str::to_string),
            extra: Default::default(),
        })
        .with_statement_protocol(StatementProtocol::Text);
    config.pool_size = 4;
    config.min_pool_size = 1;
    // Plugin keys address registered data sources, not individual connections.
    // The new manager already gives this race party its own connection pool.
    let datasource_name = config.datasource_name();
    let manager = MysqlDbManager::new(config).await.expect(
        "open the SECOND independent MySQL connection for the auth-session race",
    );
    Arc::new(MysqlDbPlugin::new(manager, datasource_name))
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

async fn seeded_scope(
    repo: &DbUserIdentityStore,
    external: &str,
) -> AuthSessionScope {
    let user_id = repo
        .ensure_identity("github", external, Some("MySQL Race 用户"), None, "dev")
        .await
        .expect("seed the MySQL identity row");
    AuthSessionScope {
        user_id,
        provider: "github".to_string(),
        env: "dev".to_string(),
    }
}

/// The shared Task 2 contract harness over the REAL MySQL plugin, twice —
/// once per independent connection — plus the deterministic
/// cross-connection refresh/logout race.
#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; run with -- --ignored --nocapture (isolated disposable EMPTY MySQL database only)"]
async fn auth_session_mysql_contract_and_two_connection_race() {
    let database = MysqlConformanceDb::open_empty().await;
    let applied = database.apply_full_migration_chain().await;
    assert!(
        !applied.is_empty(),
        "the full MySQL migration chain must apply ({} versions)",
        applied.len()
    );

    // ---- The shared contract harness, connection #1.
    let first_store = DbUserIdentityStore::mysql(database.plugin.clone());
    let first_scope = seeded_scope(&first_store, "mysql-contract-1").await;
    auth_session_repo_port_contract_tests(&first_store, first_scope).await;

    // ---- The SAME harness repeated through the SECOND independent
    // connection (a fresh scope; same rows, another pool).
    let race_plugin = second_connection_on_the_conformance_database().await;
    let second_store = DbUserIdentityStore::mysql(race_plugin.clone());
    let second_scope = seeded_scope(&second_store, "mysql-contract-2").await;
    auth_session_repo_port_contract_tests(&second_store, second_scope).await;

    // ---- The cross-connection race (the HTTP races' DB-level mirror):
    // connection #1 reads the current version A; connection #2 commits
    // the logout (by-sid revoke); connection #1's rotate CAS now loses
    // against the COMMITTED row, and the revoke is visible on BOTH.
    let race_scope = seeded_scope(&first_store, "mysql-race-user").await;
    assert_eq!(
        first_store
            .install_login_session(install_command(
                &race_scope,
                0,
                version("mysql-race-sid", 1, "mysql-race-hash-a"),
                NOW + 3_600,
            ))
            .await
            .expect("install the raced session on connection #1"),
        AuthSessionWrite::Applied
    );
    // The refresh's read of A (connection #1).
    let snapshot = first_store
        .get_session_by_hash(&race_scope, "mysql-race-hash-a", NOW)
        .await
        .expect("connection #1 reads A")
        .expect("A is the current version");
    assert_eq!(snapshot.version, version("mysql-race-sid", 1, "mysql-race-hash-a"));

    // The logout COMMITS on connection #2.
    assert_eq!(
        second_store
            .revoke_session(&race_scope, "mysql-race-sid")
            .await
            .expect("connection #2 commits the by-sid logout"),
        AuthSessionRevoke::Revoked
    );
    // The revoke is visible through BOTH connections.
    assert!(
        second_store
            .get_session_by_hash(&race_scope, "mysql-race-hash-a", NOW)
            .await
            .expect("connection #2 resolves after its own revoke")
            .is_none()
    );
    assert!(
        first_store
            .get_session_by_hash(&race_scope, "mysql-race-hash-a", NOW)
            .await
            .expect("connection #1 resolves the committed revoke")
            .is_none(),
        "the other connection's committed revoke is immediately visible"
    );

    // The resumed rotate CAS (connection #1, expected version A) is a
    // lost race — never an error, never a success.
    assert_eq!(
        first_store
            .rotate_session(RotateAuthSession {
                scope: race_scope.clone(),
                expected: version("mysql-race-sid", 1, "mysql-race-hash-a"),
                next: version("mysql-race-sid", 2, "mysql-race-hash-b"),
                expires_at: NOW + 7_200,
                now: NOW + 10,
            })
            .await
            .expect("the lost rotate is a classified outcome, not an error"),
        AuthSessionWrite::Conflict
    );

    database.close().await;
}

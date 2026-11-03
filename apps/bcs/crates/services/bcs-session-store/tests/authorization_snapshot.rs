//! Session authorization-snapshot conformance (bot-authority
//! plan Task 6.4/6.4a, spec §8.3).
//!
//! The SQL scenario set always exercises a WRITER and an
//! INDEPENDENT reader store instance over the same database
//! (per plan Task 6: 共享数据库、独立 Store 实例). The Memory
//! scenario set projects the same semantics from a single
//! shared state (Memory has no cache layer).
//!
//! Snapshot reads are strict: storage and decode failures are
//! `Err`, never a `try_get` fallback (spec §8.3: reads hit the
//! MASTER data source directly).
//!
//! The scenario functions live in
//! `common/authorization_scenarios.rs` so the same set runs on
//! the Memory projection, the SQLite store, and — via the plan
//! §6 gate — the MySQL store `authority_mysql.rs`.

use std::sync::Arc;

use bcs_db_api::DbPlugin;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::types::authorization_snapshot::SessionAuthorizationSnapshot;
use bcs_service_api::{Group as DomainGroup};
use bcs_group_store::MySqlGroupStore;
use bcs_session_store::{MemorySessionRepo, MySqlSessionStore};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

#[path = "common/authorization_scenarios.rs"]
mod scenarios;

use scenarios::{
    ENV, assert_env_isolation, assert_independent_instance_freshness,
    assert_missing_session_is_none, assert_session_lifecycle_projection,
    assert_session_snapshot_facts, assert_snapshot_failure_is_strict,
    fail_db,
};

#[tokio::test]
async fn memory_session_repo_passes_session_authorization_snapshot_contract() {
    let sessions = Arc::new(MemorySessionRepo::new());
    let groups = Arc::new(bcs_group_store::MemoryGroupRepo::new());
    let group_reader = Arc::clone(&groups) as Arc<dyn GroupRepoPort>;
    assert_session_snapshot_facts(sessions.as_ref(), group_reader.as_ref()).await;
    assert_session_lifecycle_projection(sessions.as_ref()).await;
    assert_missing_session_is_none(sessions.as_ref(), "never-created:00000000").await;

    // Memory projects the instance env (process env rule).
    groups
        .upsert(DomainGroup::new("env-check-group", "bot-driver", Vec::new()))
        .await
        .expect("upsert");
    let session = sessions
        .create("env-check-group", NewSessionParams::default())
        .await
        .expect("create");
    let snapshot: SessionAuthorizationSnapshot = sessions
        .read_authorization_snapshot(&session.id)
        .await
        .expect("read")
        .expect("snapshot");
    assert_eq!(snapshot.env, bcs_config::resolve_env_str());
}

#[tokio::test]
async fn sqlite_session_store_passes_session_authorization_snapshot_contract() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");

    let writer = MySqlSessionStore::sqlite(Arc::clone(&db), ENV.to_string());
    // INDEPENDENT second store instance over the same database.
    let reader2 = MySqlSessionStore::sqlite(Arc::clone(&db), ENV.to_string());
    let groups = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());

    assert_session_snapshot_facts(&writer, &groups).await;

    // Fresh-env read through the independent instance shows the
    // committed values (independent-instance freshness).
    assert_independent_instance_freshness(&writer, &reader2).await;

    assert_session_lifecycle_projection(&writer).await;
    assert_missing_session_is_none(&reader2, "seed-src:00000000").await;

    // env binding at instance level.
    let groups_b = MySqlGroupStore::sqlite(Arc::clone(&db), "auth-snap-b".to_string());
    let env_b = MySqlSessionStore::sqlite(Arc::clone(&db), "auth-snap-b".to_string());
    assert_env_isolation(&groups_b, &writer, &env_b).await;

    // Storage failures are strict Err — never None and never a
    // cache fallback (MySqlSessionStore has no display cache,
    // but the strictness is pinned anyway).
    let failing = MySqlSessionStore::sqlite(fail_db(), ENV.to_string());
    assert_snapshot_failure_is_strict(failing).await;
}

// -- plan-named 6.4a test functions -----------------------------------------

/// Plan 6.4a `session_scope_is_seeded_then_independent`: creation-time
/// legal-explicit-override / group-seed inheritance / missing-seed
/// default-Full; after creation a Group scope change must NOT
/// overwrite the Session projection and missing Session members are
/// NOT back-filled via the Group seed. Runs over the SQL store and
/// re-reads the seeded Session through an INDEPENDENT second store
/// instance.
#[tokio::test]
async fn session_scope_is_seeded_then_independent() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let writer = MySqlSessionStore::sqlite(Arc::clone(&db), ENV.to_string());
    let reader2 = MySqlSessionStore::sqlite(Arc::clone(&db), ENV.to_string());
    let groups = MySqlGroupStore::sqlite(db, ENV.to_string());
    assert_session_snapshot_facts(&writer, &groups).await;

    // Independent-instance freshness on the seeded facts: the
    // projection through a separate store over the same database
    // equals the writer store's projection for the inherited seed.
    assert_independent_instance_freshness(&writer, &reader2).await;
}

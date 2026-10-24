//! Group authorization-snapshot conformance (bot-authority plan
//! Task 6.4/6.4a, spec §8.3).
//!
//! The SQL scenario set always exercises a WRITER and an
//! INDEPENDENT reader store instance over the same database:
//! proving freshness through a snapshot read must never rely on
//! one shared cache instance. The Memory scenario set projects the
//! same semantics from a single shared state (Memory has no cache
//! layer to bypass).
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
use bcs_group_store::{GroupBuilder, MemoryGroupRepo, MySqlGroupStore};
use bcs_service_api::port::repo::GroupRepoPort;
use bcs_service_api::types::authorization_snapshot::GroupAuthorizationSnapshot;

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

#[path = "common/authorization_scenarios.rs"]
mod scenarios;

use scenarios::{
    ENV, assert_cache_freshness_through_independent_reader,
    assert_env_isolation, assert_group_seed_facts,
    assert_live_and_deleted_projection, assert_nonexistent_group_is_none,
    assert_scope_only_update_changes_snapshot, assert_snapshot_env_binding,
    assert_snapshot_failure_is_strict, assert_strategy_gating,
    fail_db,
};

#[tokio::test]
async fn memory_group_repo_passes_group_authorization_snapshot_contract() {
    let repo = Arc::new(MemoryGroupRepo::new());
    let reader = Arc::clone(&repo) as Arc<dyn GroupRepoPort>;
    assert_strategy_gating(repo.as_ref(), reader.as_ref()).await;
    assert_scope_only_update_changes_snapshot(repo.as_ref(), reader.as_ref()).await;
    assert_group_seed_facts(repo.as_ref(), reader.as_ref()).await;
    assert_nonexistent_group_is_none(repo.as_ref()).await;
    // The live/deletion projection scenario shared with the
    // SQL drivers: provisioning is NOT live and deletion
    // removes the record for good.
    assert_live_and_deleted_projection(repo.as_ref()).await;

    // Memory projects the instance env (the process env rule,
    // same as the authority memory projection).
    let env_group = GroupBuilder::new("bot-driver")
        .id("snap-mem-env")
        .build();
    repo.upsert(env_group).await.expect("upsert");
    let snapshot: GroupAuthorizationSnapshot = repo
        .read_authorization_snapshot("snap-mem-env")
        .await
        .expect("read")
        .expect("snapshot");
    assert_eq!(snapshot.env, bcs_config::resolve_env_str());
}

#[tokio::test]
async fn sqlite_group_store_passes_group_authorization_snapshot_contract() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");

    let writer = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    // INDEPENDENT second store instance over the same database.
    let reader2 = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());

    assert_strategy_gating(&writer, &reader2).await;
    assert_scope_only_update_changes_snapshot(&writer, &reader2).await;
    assert_group_seed_facts(&writer, &reader2).await;
    assert_nonexistent_group_is_none(&writer).await;

    // Cache-poisoning proof: `reader2` caches the group through its
    // ordinary try_get, `writer` then flips the scope through its
    // own instance, and the snapshot read through `reader2` must
    // observe the MASTER value — never its poisoned cache.
    assert_cache_freshness_through_independent_reader(&writer, &reader2).await;

    // env binding at instance level.
    let env_b = MySqlGroupStore::sqlite(Arc::clone(&db), "auth-snap-b".to_string());
    assert_env_isolation(&writer, &env_b).await;

    assert_live_and_deleted_projection(&writer).await;

    // Memory snapshot env rule does not apply to SQL: the snapshot
    // reports the instance-bound env.
    assert_snapshot_env_binding(&reader2, ENV).await;

    // Storage failures are strict Err — never None, never a cache
    // fallback.
    let failing = MySqlGroupStore::sqlite(fail_db(), ENV.to_string());
    assert_snapshot_failure_is_strict(failing).await;
}

// -- plan-named 6.4a test functions -----------------------------------------
// These named tests surface the plan §6.4a scenario names for the
// Task 8.7 acceptance mapping; the aggregate contracts above keep
// exercising the same scenario helpers on both stores.

/// Plan 6.4a `manager_role_is_strategy_gated`: Chat / ManagerWorker /
/// StateMachine — the same-named extra Manager participant becomes a
/// management candidate ONLY under ManagerWorker (driver/originator
/// always count). Pinned on both the Memory projection and, with an
/// INDEPENDENT second store instance, the SQL master-source read.
#[tokio::test]
async fn manager_role_is_strategy_gated() {
    let memory = Arc::new(MemoryGroupRepo::new());
    let memory_reader = Arc::clone(&memory) as Arc<dyn GroupRepoPort>;
    assert_strategy_gating(memory.as_ref(), memory_reader.as_ref()).await;

    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let writer = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let reader2 = MySqlGroupStore::sqlite(db, ENV.to_string());
    assert_strategy_gating(&writer, &reader2).await;
}

/// Plan 6.4a `scope_only_update_changes_authorization_snapshot`: two
/// independent store instances over the same database; ONLY the
/// participant scope flips while actor/role/mode stay stable.
#[tokio::test]
async fn scope_only_update_changes_authorization_snapshot() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let writer = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let reader2 = MySqlGroupStore::sqlite(db, ENV.to_string());
    assert_scope_only_update_changes_snapshot(&writer, &reader2).await;
}

//! Group authorization-snapshot MySQL conformance gate
//! (bot-authority plan Task 8 / §6).
//!
//! The plan §6 named entry: the SAME shared scenario set as
//! `authorization_snapshot.rs` — strategy gating, scope-only
//! update, seeding independence, cache freshness through
//! two live store instances, env isolation, live/deletion
//! projection and strict-error reads — runs against
//! [`MySqlGroupStore`] over the REAL full MySQL migration
//! chain. Locally there is no MySQL server, so this entry
//! is `#[ignore]`d and the SQLite suite proves the scenario
//! parity on SQLite (plan §6).
//!
//! `BCS_TEST_MYSQL_URL` must point at an ISOLATED disposable
//! EMPTY MySQL database; a missing URL fails the entry
//! immediately (启动缺URL立即失败) and the URL is never
//! logged.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use bcs_group_store::MySqlGroupStore;
#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;

use mysql_chain::{MysqlConformanceDb, report_mysql_gate_scenarios};

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

/// Plan §6 MySQL gate: the group authorization-snapshot
/// scenarios over the migrated MySQL chain.
#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs the plan §6 MySQL gate"]
async fn group_authorization_mysql_conformance() {
    let database = MysqlConformanceDb::open_empty().await;
    let applied = database.apply_full_migration_chain().await;
    assert!(
        !applied.is_empty(),
        "full MySQL migration chain applied ({} versions)",
        applied.len()
    );

    let writer = MySqlGroupStore::new(database.plugin.clone(), ENV.to_string());
    // INDEPENDENT second store instance over the same
    // database (plan Task 6: 共享数据库、独立 Store 实例).
    let reader2 = MySqlGroupStore::new(database.plugin.clone(), ENV.to_string());

    let scenarios: [&'static str; 9] = [
        "strategy_gating",
        "scope_only_update",
        "group_seed_facts",
        "missing_group_is_none",
        "cache_freshness_through_independent_reader",
        "env_isolation",
        "live_and_deleted_projection",
        "snapshot_env_binding",
        "snapshot_failure_is_strict",
    ];

    assert_strategy_gating(&writer, &reader2).await;
    assert_scope_only_update_changes_snapshot(&writer, &reader2).await;
    assert_group_seed_facts(&writer, &reader2).await;
    assert_nonexistent_group_is_none(&writer).await;
    assert_cache_freshness_through_independent_reader(&writer, &reader2).await;

    let env_b =
        MySqlGroupStore::new(database.plugin.clone(), "auth-snap-b".to_string());
    assert_env_isolation(&writer, &env_b).await;

    assert_live_and_deleted_projection(&writer).await;
    assert_snapshot_env_binding(&reader2, ENV).await;

    let failing = MySqlGroupStore::new(fail_db(), ENV.to_string());
    assert_snapshot_failure_is_strict(failing).await;

    report_mysql_gate_scenarios("group-authorization", &scenarios);

    database.close().await;
}

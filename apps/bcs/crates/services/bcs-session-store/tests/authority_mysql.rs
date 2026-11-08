//! Session authorization-snapshot MySQL conformance gate
//! (bot-authority plan Task 8 / §6).
//!
//! The plan §6 named entry: the SAME shared scenario
//! set as `authorization_snapshot.rs` — the session
//! snapshot conformance (seed inheritance, legal
//! explicit override, missing-seed fallback, Session
//! evolves independently from the Group seed,
//! scope-only update) plus the delivery-relevant
//! snapshot semantics (independent-instance
//! freshness, env binding, missing/not-live reads,
//! strict storage failures) — runs against
//! [`MySqlSessionStore`] over the REAL full MySQL
//! migration chain. Locally there is no MySQL server,
//! so this entry is `#[ignore]`d and the SQLite suite
//! proves the scenario parity on SQLite (plan §6).
//!
//! `BCS_TEST_MYSQL_URL` must point at an ISOLATED
//! disposable EMPTY MySQL database; a missing URL
//! fails the entry immediately (启动缺URL立即失败)
//! and the URL is never logged.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use bcs_group_store::MySqlGroupStore;
use bcs_session_store::MySqlSessionStore;
#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;

use mysql_chain::{MysqlConformanceDb, report_mysql_gate_scenarios};

#[path = "common/authorization_scenarios.rs"]
mod scenarios;

use scenarios::{
    ENV, assert_env_isolation, assert_independent_instance_freshness,
    assert_missing_session_is_none, assert_session_lifecycle_projection,
    assert_session_snapshot_facts, assert_snapshot_failure_is_strict,
    fail_db,
};

/// Plan §6 MySQL gate: the session
/// authorization-snapshot scenarios over the migrated
/// MySQL chain.
#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs the plan §6 MySQL gate"]
async fn session_authorization_mysql_conformance() {
    let database = MysqlConformanceDb::open_empty().await;
    let applied = database.apply_full_migration_chain().await;
    assert!(
        !applied.is_empty(),
        "full MySQL migration chain applied ({} versions)",
        applied.len()
    );

    let writer = MySqlSessionStore::new(database.plugin.clone(), ENV.to_string());
    // INDEPENDENT second store instance over the same
    // database (plan Task 6: 共享数据库、独立 Store 实例).
    let reader2 = MySqlSessionStore::new(database.plugin.clone(), ENV.to_string());
    let groups = MySqlGroupStore::new(database.plugin.clone(), ENV.to_string());

    let scenarios: [&'static str; 6] = [
        "session_snapshot_facts",
        "independent_instance_freshness",
        "lifecycle_delete_projection",
        "missing_session_is_none",
        "env_isolation",
        "snapshot_failure_is_strict",
    ];

    assert_session_snapshot_facts(&writer, &groups).await;
    assert_independent_instance_freshness(&writer, &reader2).await;
    assert_session_lifecycle_projection(&writer).await;
    assert_missing_session_is_none(&reader2, "seed-src:00000000").await;

    let groups_b =
        MySqlGroupStore::new(database.plugin.clone(), "auth-snap-b".to_string());
    let env_b = MySqlSessionStore::new(database.plugin.clone(), "auth-snap-b".to_string());
    assert_env_isolation(&groups_b, &writer, &env_b).await;

    let failing = MySqlSessionStore::new(fail_db(), ENV.to_string());
    assert_snapshot_failure_is_strict(failing).await;

    report_mysql_gate_scenarios("session-authorization", &scenarios);

    database.close().await;
}

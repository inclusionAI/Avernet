use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_group_store::{GroupBuilder, MemoryGroupRepo, MySqlGroupStore};
use bcs_service_api::{GroupKind, GroupStatus, GroupStrategy};
use bcs_service_api::port::repo::GroupRepoPort;

#[tokio::test]
async fn memory_group_repo_passes_group_repo_contract() {
    let repo = MemoryGroupRepo::new();

    bcs_test_support::contract::repo::group_repo_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn memory_group_metrics_snapshot_port_contract() {
    let repo = MemoryGroupRepo::new();
    let mut normal = GroupBuilder::new("driver")
        .id("metrics-normal")
        .build();
    normal.group_strategy = GroupStrategy::ManagerWorker;
    normal.service_mode = Some("master_slave".to_string());
    let mut dm = GroupBuilder::new("driver")
        .id("metrics-dm")
        .build();
    dm.group_kind = GroupKind::Dm;
    dm.group_strategy = GroupStrategy::StateMachine;
    dm.status = GroupStatus::Completed;
    dm.service_mode = Some("user-provided-mode".to_string());

    repo.upsert(normal).await.expect("insert normal group");
    repo.upsert(dm).await.expect("insert dm group");

    bcs_test_support::contract::port::group_metrics_snapshot_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn mysql_group_store_sqlite_smoke_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    let repo = MySqlGroupStore::new(db, "contract".to_string());

    assert!(repo.get("bcs-contract-missing-group").await.is_none());
}

#[tokio::test]
async fn mysql_group_metrics_snapshot_port_sqlite_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_groups ( \
             group_id TEXT PRIMARY KEY, \
             env TEXT NOT NULL, \
             status TEXT NOT NULL, \
             group_kind TEXT, \
             group_strategy TEXT, \
             service_mode TEXT \
         )",
    ))
    .await
    .expect("create groups table");
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_groups (group_id, env, status, group_kind, group_strategy, service_mode) \
         VALUES (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?)",
        vec![
            DbValue::from("metrics-normal"),
            DbValue::from("contract"),
            DbValue::from("active"),
            DbValue::from("normal"),
            DbValue::from("manager_worker"),
            DbValue::from("master_slave"),
            DbValue::from("metrics-dm"),
            DbValue::from("contract"),
            DbValue::from("completed"),
            DbValue::from("dm"),
            DbValue::from("state_machine"),
            DbValue::from("user-provided-mode"),
            DbValue::from("metrics-other-env"),
            DbValue::from("other"),
            DbValue::from("active"),
            DbValue::from("normal"),
            DbValue::from("chat"),
            DbValue::from("master_slave"),
        ],
    ))
    .await
    .expect("seed groups");

    let repo = MySqlGroupStore::new(db, "contract".to_string());
    bcs_test_support::contract::port::group_metrics_snapshot_port_contract_tests(&repo).await;
}

#[tokio::test]
#[ignore = "requires a MySQL-compatible backend; LocalSqliteDbPlugin does not support ON DUPLICATE KEY used by MySqlGroupStore::upsert"]
async fn mysql_group_store_full_repo_contract_requires_mysql_backend() {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    let repo = MySqlGroupStore::new(db, "contract".to_string());

    bcs_test_support::contract::repo::group_repo_port_contract_tests(&repo).await;
}

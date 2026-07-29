use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_group_store::{GroupBuilder, MemoryGroupRepo, MySqlGroupStore};
use bcs_service_api::port::repo::GroupRepoPort;
use bcs_service_api::{
    GroupKind, GroupMutableFieldsPatch, GroupStatus, GroupStrategy, Participant, ParticipantRole,
    ServiceError,
};

#[tokio::test]
async fn memory_group_repo_passes_group_repo_contract() {
    let repo = MemoryGroupRepo::new();

    bcs_test_support::contract::repo::group_repo_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn versioned_patch_returns_the_exact_persisted_representation() {
    let repo = MemoryGroupRepo::new();
    let group = GroupBuilder::new("driver").id("versioned-patch").build();
    let original_version = group.version;
    repo.upsert(group).await.expect("seed group");

    let persisted = repo
        .patch_mutable_fields_if_version(
            "versioned-patch",
            original_version,
            GroupMutableFieldsPatch {
                label: Some("Renamed".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("versioned patch");
    let reloaded = repo
        .try_get("versioned-patch")
        .await
        .expect("reload group")
        .expect("group exists");

    assert_eq!(persisted.version, original_version + 1);
    assert_eq!(persisted.updated_at, reloaded.updated_at);
    assert_eq!(persisted.label, reloaded.label);
}

#[tokio::test]
async fn visibility_patch_and_protected_member_add_serialize_without_breaking_the_invariant() {
    let repo = MemoryGroupRepo::new();
    let protected = Participant::bot("protected", ParticipantRole::Consultant);

    let private = GroupBuilder::new("driver").id("add-wins").build();
    let private_version = private.version;
    repo.upsert(private).await.expect("seed add-wins group");
    repo.add_participant_with_visibility_guard("add-wins", protected.clone(), false)
        .await
        .expect("protected Bot joins while private");
    let stale_patch = repo
        .patch_mutable_fields_if_version(
            "add-wins",
            private_version,
            GroupMutableFieldsPatch {
                visibility: Some("public".to_string()),
                ..Default::default()
            },
        )
        .await;
    assert!(matches!(stale_patch, Err(ServiceError::Conflict(_))));

    let private = GroupBuilder::new("driver").id("patch-wins").build();
    let private_version = private.version;
    repo.upsert(private).await.expect("seed patch-wins group");
    repo.patch_mutable_fields_if_version(
        "patch-wins",
        private_version,
        GroupMutableFieldsPatch {
            visibility: Some("public".to_string()),
            ..Default::default()
        },
    )
    .await
    .expect("public patch wins");
    let rejected = repo
        .add_participant_with_visibility_guard("patch-wins", protected, false)
        .await;
    assert!(matches!(
        rejected,
        Err(ServiceError::ExistNonPublicBots { .. })
    ));
}

#[tokio::test]
async fn memory_group_metrics_snapshot_port_contract() {
    let repo = MemoryGroupRepo::new();
    let mut normal = GroupBuilder::new("driver").id("metrics-normal").build();
    normal.group_strategy = GroupStrategy::ManagerWorker;
    normal.service_mode = Some("master_slave".to_string());
    let mut dm = GroupBuilder::new("driver").id("metrics-dm").build();
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

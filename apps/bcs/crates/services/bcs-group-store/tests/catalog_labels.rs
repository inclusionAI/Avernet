use std::sync::Arc;
use bcs_db_api::{DbPlugin,DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_group_store::{MemoryGroupRepo,MySqlGroupStore};
use bcs_service_api::{Group,GroupKind,GroupStrategy,GroupMutableFieldsPatch};
use bcs_service_api::port::repo::{GroupRepoPort,GroupEventfulMutation,CommitGroupEventfulMutation};
use bcs_service_api::types::catalog_labels::CatalogLabels;
fn labels(value:&str)->CatalogLabels {serde_json::from_value(serde_json::json!({"stage":[value]})).unwrap()}
async fn contract(repo:&dyn GroupRepoPort) {
    let mut group=Group::new("labels","manager",vec![]);group.group_strategy=GroupStrategy::ManagerWorker;
    repo.upsert_with_catalog_labels(group.clone(),Some(labels("trial"))).await.unwrap();
    assert_eq!(repo.read_card_snapshot("labels").await.unwrap().unwrap().catalog_labels,labels("trial"));
    group.label=Some("renamed".into());repo.upsert(group).await.unwrap();
    repo.patch_mutable_fields("labels",GroupMutableFieldsPatch {context:Some("goal".into()),..Default::default()}).await.unwrap();
    assert_eq!(repo.read_card_snapshot("labels").await.unwrap().unwrap().catalog_labels,labels("trial"));
    let version=repo.try_get("labels").await.unwrap().unwrap().version;
    repo.commit_eventful_mutation(CommitGroupEventfulMutation {group_id:"labels".into(),expected_version:version,mutated_at_ms:1000,event:None,
        mutation:GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {catalog_labels:Some(labels("running")),visibility:Some("public".into()),..Default::default()})}).await.unwrap();
    assert_eq!(repo.read_card_snapshot("labels").await.unwrap().unwrap().catalog_labels,labels("running"));
    assert!(repo.commit_eventful_mutation(CommitGroupEventfulMutation {group_id:"labels".into(),expected_version:version,mutated_at_ms:1001,event:None,
        mutation:GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {catalog_labels:Some(labels("offline")),..Default::default()})}).await.is_err());
    assert_eq!(repo.read_card_snapshot("labels").await.unwrap().unwrap().catalog_labels,labels("running"));
    let (a,b)=tokio::join!(
        repo.patch_mutable_fields("labels",GroupMutableFieldsPatch {context:Some("concurrent goal".into()),..Default::default()}),
        repo.patch_mutable_fields("labels",GroupMutableFieldsPatch {catalog_labels:Some(labels("offline")),..Default::default()})
    );a.unwrap();b.unwrap();
    let snapshot=repo.read_card_snapshot("labels").await.unwrap().unwrap();
    assert_eq!(snapshot.catalog_labels,labels("offline"));assert_eq!(snapshot.card_template.unwrap()["description"],"concurrent goal");
    repo.patch_mutable_fields("labels",GroupMutableFieldsPatch {catalog_labels:Some(Default::default()),..Default::default()}).await.unwrap();
    assert!(repo.read_card_snapshot("labels").await.unwrap().unwrap().catalog_labels.is_empty());
    for kind in [GroupKind::Normal,GroupKind::Dm] {
        let mut group=Group::new(format!("unsupported-{kind:?}"),"manager",vec![]);group.group_kind=kind;
        assert!(repo.upsert_with_catalog_labels(group.clone(),Some(labels("trial"))).await.is_err());
        repo.upsert_with_catalog_labels(group,Some(Default::default())).await.unwrap();
    }
}
#[tokio::test] async fn memory_preserve_replace_and_version_fence(){contract(&MemoryGroupRepo::new()).await;}
#[tokio::test] async fn sqlite_preserve_replace_and_version_fence(){
    let db=Arc::new(LocalSqliteDbPlugin::new().unwrap());bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    contract(&MySqlGroupStore::sqlite(db,"labels".into())).await;
}
#[tokio::test]
async fn legacy_missing_template_null_corruption_and_failure_rollback(){
    let db=Arc::new(LocalSqliteDbPlugin::new().unwrap());bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let repo=MySqlGroupStore::sqlite(db.clone(),"labels".into());
    let mut group=Group::new("legacy","manager",vec![]);group.group_strategy=GroupStrategy::StateMachine;repo.upsert(group).await.unwrap();
    let initialized=db.query(DbStatement::new("SELECT catalog_labels FROM bcs_group_agent_cards")).await.unwrap();
    assert_eq!(initialized[0].get_string("catalog_labels").unwrap().as_deref(),Some("{}"));
    db.execute(DbStatement::new("DELETE FROM bcs_group_agent_cards")).await.unwrap();
    let missing=repo.read_card_snapshot("legacy").await.unwrap().unwrap();assert!(missing.card_template.is_none());assert!(missing.catalog_labels.is_empty());
    repo.patch_mutable_fields("legacy",GroupMutableFieldsPatch {catalog_labels:Some(labels("trial")),..Default::default()}).await.unwrap();
    let restored=repo.read_card_snapshot("legacy").await.unwrap().unwrap();assert!(restored.card_template.is_some());assert_eq!(restored.catalog_labels,labels("trial"));
    db.execute(DbStatement::new("CREATE TRIGGER fail_labels BEFORE UPDATE OF catalog_labels ON bcs_group_agent_cards BEGIN SELECT RAISE(ABORT,'fail'); END")).await.unwrap();
    assert!(repo.patch_mutable_fields("legacy",GroupMutableFieldsPatch {label:Some("must not save".into()),catalog_labels:Some(labels("running")),..Default::default()}).await.is_err());
    let before=repo.try_get("legacy").await.unwrap().unwrap();
    assert!(repo.commit_eventful_mutation(CommitGroupEventfulMutation {
        group_id:"legacy".into(),expected_version:before.version,mutated_at_ms:2000,event:None,
        mutation:GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
            label:Some("event must not save".into()),catalog_labels:Some(labels("running")),..Default::default()
        }),
    }).await.is_err());
    let after=repo.try_get("legacy").await.unwrap().unwrap();assert_eq!(after.version,before.version);
    assert_eq!(after.label,None);
    assert_eq!(repo.read_card_snapshot("legacy").await.unwrap().unwrap().catalog_labels,labels("trial"));
    db.execute(DbStatement::new("DROP TRIGGER fail_labels")).await.unwrap();
    db.execute(DbStatement::new("UPDATE bcs_group_agent_cards SET catalog_labels=NULL")).await.unwrap();
    assert!(repo.read_card_snapshot("legacy").await.unwrap().unwrap().catalog_labels.is_empty());
    db.execute(DbStatement::new("UPDATE bcs_group_agent_cards SET catalog_labels='null'")).await.unwrap();
    assert!(repo.read_card_snapshot("legacy").await.is_err());
}

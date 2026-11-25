//! Bounded strict reads: no stale-cache fallback, N+1, or error-as-missing.
use std::sync::{Arc, atomic::{AtomicBool, AtomicUsize, Ordering}};
use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbStatement, DbResult, DbRow, DbExecuteResult, DbTransactionStep, DbTransactionStepResult, DbHealth, DbError};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_group_store::{MySqlGroupStore, GroupRepoPort};
use bcs_service_api::{Group, GroupStrategy, GroupMutableFieldsPatch, ServiceError};
use bcs_service_api::port::repo::{CommitGroupEventfulMutation, GroupEventfulMutation};
use serde_json::json;

struct Reads {
    inner: LocalSqliteDbPlugin, queries: AtomicUsize, churn: AtomicBool, fail_card: AtomicBool,
}
#[async_trait]
impl DbPlugin for Reads {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.fetch_add(1, Ordering::SeqCst);
        if statement.sql().contains("c.catalog_labels FROM bcs_groups AS g") {
            if self.fail_card.load(Ordering::SeqCst) { return Err(DbError::Backend("injected snapshot read failure".into())); }
            if self.churn.load(Ordering::SeqCst) {
                self.inner.execute(DbStatement::new("UPDATE bcs_groups SET version=version+1")).await?;
            }
        }
        self.inner.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> { self.inner.execute(statement).await }
    async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> { self.inner.transaction(steps).await }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
}
async fn fixture() -> (Arc<Reads>, MySqlGroupStore, Group) {
    let db=Arc::new(Reads { inner:LocalSqliteDbPlugin::new().unwrap(), queries:AtomicUsize::new(0), churn:AtomicBool::new(false), fail_card:AtomicBool::new(false) });
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let repo=MySqlGroupStore::sqlite(db.clone(),"detail-test".into());
    let mut group=Group::new("detail-group","driver",vec![]); group.group_strategy=GroupStrategy::ManagerWorker; group.label=Some("A".into());
    repo.create_with_catalog_labels(group.clone(),Some(serde_json::from_value(json!({"stage":["trial"]})).unwrap())).await.unwrap();
    (db,repo,group)
}
#[tokio::test]
async fn detail_read_bypasses_a_warm_stale_cache_with_three_queries() {
    let (db,repo,group)=fixture().await;
    assert_eq!(repo.try_get(&group.id).await.unwrap().unwrap().label.as_deref(),Some("A"));
    let other=MySqlGroupStore::sqlite(db.clone(),"detail-test".into());
    other.commit_eventful_mutation_with_snapshot(CommitGroupEventfulMutation {
        group_id:group.id.clone(),expected_version:group.version,mutated_at_ms:1_800_000_000_000,event:None,
        mutation:GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
            label:Some("B".into()),catalog_labels:Some(serde_json::from_value(json!({"stage":["running"]})).unwrap()),..Default::default()
        }),
    }).await.unwrap();
    db.queries.store(0,Ordering::SeqCst);
    let read=repo.read_detail_snapshot(&group.id).await.unwrap().unwrap();
    assert_eq!(read.group.label.as_deref(),Some("B")); assert_eq!(read.group.version,group.version+1);
    assert_eq!(read.catalog_labels.as_map()["stage"],["running"]);
    assert_eq!(db.queries.load(Ordering::SeqCst),3);
}
#[tokio::test]
async fn continuous_version_churn_stops_after_nine_queries() {
    let (db,repo,group)=fixture().await; db.churn.store(true,Ordering::SeqCst); db.queries.store(0,Ordering::SeqCst);
    assert!(matches!(repo.read_detail_snapshot(&group.id).await,Err(ServiceError::Conflict(_))));
    assert_eq!(db.queries.load(Ordering::SeqCst),9);
}
#[tokio::test]
async fn snapshot_storage_failure_is_not_missing_or_cached_success() {
    let (db,repo,group)=fixture().await; repo.try_get(&group.id).await.unwrap();
    db.fail_card.store(true,Ordering::SeqCst); db.queries.store(0,Ordering::SeqCst);
    assert!(matches!(repo.read_detail_snapshot(&group.id).await,Err(ServiceError::InternalError(_))));
    assert_eq!(db.queries.load(Ordering::SeqCst),3);
}
#[tokio::test]
async fn absent_detail_returns_none_after_one_indexed_lookup() {
    let (db,repo,_)=fixture().await; db.queries.store(0,Ordering::SeqCst);
    assert!(repo.read_detail_snapshot("missing").await.unwrap().is_none());
    assert_eq!(db.queries.load(Ordering::SeqCst),1);
}

#[tokio::test]
async fn detail_read_batches_many_participants_without_per_participant_queries() {
    let (db,repo,mut group)=fixture().await;
    group.id="many-participants".into();
    group.participants=(0..128).map(|i| bcs_service_api::Participant::bot(format!("worker-{i}"),bcs_service_api::ParticipantRole::Consultant)).collect();
    repo.create_with_catalog_labels(group.clone(),None).await.unwrap();
    db.queries.store(0,Ordering::SeqCst);
    let snapshot=repo.read_detail_snapshot(&group.id).await.unwrap().unwrap();
    assert_eq!(snapshot.group.participants.len(),128);
    assert_eq!(db.queries.load(Ordering::SeqCst),3);
}

#[tokio::test]
async fn participant_mutation_detail_preserves_labels_at_the_new_version() {
    let (_,repo,group)=fixture().await;
    let committed=repo.commit_eventful_mutation_with_snapshot(CommitGroupEventfulMutation {
        group_id:group.id.clone(),expected_version:group.version,mutated_at_ms:1_800_000_000_000,event:None,
        mutation:GroupEventfulMutation::AddParticipant {
            participant:bcs_service_api::Participant::bot("new-worker",bcs_service_api::ParticipantRole::Consultant),actor_is_public:true,
        },
    }).await.unwrap();
    let detail=repo.read_detail_snapshot(&group.id).await.unwrap().unwrap();
    assert_eq!(detail.group.version,group.version+1);
    assert_eq!(detail.group.version,committed.group.version);
    assert!(detail.group.participants.iter().any(|p|p.bot_uuid=="new-worker"));
    assert_eq!(detail.catalog_labels.as_map()["stage"],["trial"]);
}

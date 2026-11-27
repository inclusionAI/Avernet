#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "contract test support"
)]
use async_trait::async_trait;
use bcs_db_api::*;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_dist_model::*;
use bcs_dist_store::{archive::DbArchiveStore, identity::digest_json, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{common::*, session::*};
use bcs_store_api::document::DocumentStorePlugin;
use bcs_test_support::store_model::Model;
use std::{
    collections::{BTreeMap, VecDeque},
    num::NonZeroU32,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
};

pub struct Fixture {
    pub db: Arc<FaultDb>,
    pub documents: Arc<Model>,
    pub store: DbArchiveStore,
    _directory: tempfile::TempDir,
}
pub fn layout(namespace: &str) -> KeyLayout {
    KeyLayout::new("archive", namespace).unwrap()
}
impl Fixture {
    pub async fn new() -> Self {
        let directory = tempfile::tempdir().unwrap();
        let db = LocalSqliteDbPlugin::new_file(directory.path().join("archive.db")).unwrap();
        bcs_db_migrations::run_sqlite_migrations(&db).await.unwrap();
        let db = Arc::new(FaultDb::new(db));
        let documents = Arc::new(Model::default());
        let store = DbArchiveStore::new(db.clone(), documents.clone(), layout("test")).unwrap();
        Self {
            db,
            documents,
            store,
            _directory: directory,
        }
    }
    pub fn reopen(&self) -> DbArchiveStore {
        DbArchiveStore::new(self.db.clone(), self.documents.clone(), layout("test")).unwrap()
    }
    pub fn reopen_database(&self) -> DbArchiveStore {
        let db = LocalSqliteDbPlugin::new_file(self._directory.path().join("archive.db")).unwrap();
        DbArchiveStore::new(Arc::new(db), self.documents.clone(), layout("test")).unwrap()
    }
    pub async fn persist(&self, source: &EffectSource, record: &FinalTaskRecord) {
        let batch = CommittedBatch {
            created_at: None,
            commit_id: source.commit.commit_id.clone(),
            source_commit: source.commit.sequence,
            content_digest: source.commit.content_digest.clone(),
            effects: vec![Effect {
                id: source.effect_id.clone(),
                content_digest: source.content_digest.clone(),
                kind: EffectKind::SaveResult {
                    record: record.clone(),
                },
            }],
        };
        let envelope = CheckpointEnvelope {
            owner: source.commit.owner.clone(),
            checkpoint: Checkpoint {
                format_version: 1,
                commit_seq: source.commit.sequence,
                consumed: source.commit.consumed.clone(),
                tasks: VecDeque::new(),
                downstream_generation: 0,
                pending_cancel_did: None,
                runner_state: None,
                dispatch_seq: BTreeMap::new(),
                deadlines: vec![],
                deadline_history: Default::default(),
                view_drafts: BTreeMap::new(),
                last_event_id: None,
                outbox: Some(batch),
            },
            recovery: RecoveryMetadata {
                takeover_streak: 0,
                last_attempt: None,
                quarantined: false,
                last_active_at: TimestampMillis(0),
            },
        };
        self.documents
            .create_document(
                &layout("test").ckpt(&source.commit.sid).unwrap(),
                &serde_json::to_value(envelope).unwrap(),
            )
            .await
            .unwrap();
    }
    pub async fn advance_head(&self) {
        let key = layout("test").ckpt(&Sid("s0001.session".into())).unwrap();
        let mut stored = self.documents.read_document(&key).await.unwrap().unwrap();
        stored.value["checkpoint"]["commit_seq"] = serde_json::json!(2);
        stored.value["checkpoint"]["outbox"] = serde_json::Value::Null;
        self.documents
            .replace_document(&key, stored.version, &stored.value)
            .await
            .unwrap();
    }
}

pub fn final_effect() -> (EffectSource, FinalTaskRecord) {
    let record = FinalTaskRecord {
        schema_version: 1,
        scope: TaskScope {
            tenant: TenantId("tenant".into()),
            principal: PrincipalId("principal".into()),
        },
        sid: Sid("s0001.session".into()),
        tid: Tid("s0001.session.task".into()),
        status: TerminalStatus::Completed,
        snapshot: Payload::Inline(serde_json::json!({"format_version":1,"units":[]})),
        result: Payload::Inline(serde_json::json!({"message":"done"})),
        source_commit: CommitSeq(1),
        finalized_at: TimestampMillis(42),
    };
    let commit = CommitReceipt {
        sid: record.sid.clone(),
        owner: ShardOwner {
            instance: Instance {
                node: "worker".into(),
                incarnation: "one".into(),
            },
            epoch: WorkerEpoch(1),
        },
        commit_id: CommitId("commit-one".into()),
        content_digest: Digest("checkpoint-digest".into()),
        sequence: CommitSeq(1),
        consumed: None,
        proof: "not-authority".into(),
    };
    (source_for(&commit, &record), record)
}
pub fn source_for(commit: &CommitReceipt, record: &FinalTaskRecord) -> EffectSource {
    EffectSource {
        commit: commit.clone(),
        effect_id: EffectId {
            sid: record.sid.clone(),
            commit: commit.sequence,
            index: NonZeroU32::new(1).unwrap(),
        },
        content_digest: digest_json(
            &serde_json::to_value(EffectKind::SaveResult {
                record: record.clone(),
            })
            .unwrap(),
        ),
    }
}

pub struct FaultDb {
    inner: LocalSqliteDbPlugin,
    reads: AtomicUsize,
    writes: AtomicUsize,
    lose: AtomicBool,
    fail_resolution: AtomicBool,
    fail_read: AtomicBool,
    fail_before_write: AtomicBool,
    pause_read: Mutex<Option<(Arc<tokio::sync::Notify>, Arc<tokio::sync::Notify>)>>,
}
impl FaultDb {
    fn new(inner: LocalSqliteDbPlugin) -> Self {
        Self {
            inner,
            reads: AtomicUsize::new(0),
            writes: AtomicUsize::new(0),
            lose: AtomicBool::new(false),
            fail_resolution: AtomicBool::new(false),
            fail_read: AtomicBool::new(false),
            fail_before_write: AtomicBool::new(false),
            pause_read: Mutex::new(None),
        }
    }
    pub fn writes(&self) -> usize {
        self.writes.load(Ordering::SeqCst)
    }
    pub fn reads(&self) -> usize {
        self.reads.load(Ordering::SeqCst)
    }
    pub fn lose_next_write(&self) {
        self.lose.store(true, Ordering::SeqCst);
    }
    pub fn lose_write_and_resolution(&self) {
        self.lose_next_write();
        self.fail_resolution.store(true, Ordering::SeqCst);
    }
    pub fn fail_before_write(&self) {
        self.fail_before_write.store(true, Ordering::SeqCst);
    }
    pub fn pause_next_read(&self) -> (Arc<tokio::sync::Notify>, Arc<tokio::sync::Notify>) {
        let pair = (
            Arc::new(tokio::sync::Notify::new()),
            Arc::new(tokio::sync::Notify::new()),
        );
        *self.pause_read.lock().unwrap() = Some(pair.clone());
        pair
    }
}
#[async_trait]
impl DbPlugin for FaultDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        if self.fail_read.swap(false, Ordering::SeqCst) {
            return Err(DbError::Backend("injected read failure".into()));
        }
        let pause = self.pause_read.lock().unwrap().take();
        let result = self.inner.query(statement).await;
        if let Some((entered, release)) = pause {
            entered.notify_one();
            release.notified().await;
        }
        result
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.writes.fetch_add(1, Ordering::SeqCst);
        if self.fail_before_write.swap(false, Ordering::SeqCst) {
            return Err(DbError::Backend(
                "unclassified transport failure before write".into(),
            ));
        }
        let result = self.inner.execute(statement).await?;
        if self.lose.swap(false, Ordering::SeqCst) {
            if self.fail_resolution.swap(false, Ordering::SeqCst) {
                self.fail_read.store(true, Ordering::SeqCst);
            }
            return Err(DbError::Backend("injected lost response".into()));
        }
        Ok(result)
    }
    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

use super::*;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbResult, DbTransactionStep, DbTransactionStepResult,
};
use bcs_service_api::port::repo::{BotIdentity, BotIdentityUpdate};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::SeqCst};

struct CountingDb {
    inner: Arc<dyn DbPlugin>,
    reads: AtomicUsize,
    writes: AtomicUsize,
    fail_read: AtomicBool,
    fail_write: AtomicBool,
    pause_read: AtomicBool,
    read_started: tokio::sync::Notify,
    resume_read: tokio::sync::Notify,
}

impl CountingDb {
    async fn new() -> Arc<Self> {
        Arc::new(Self {
            inner: heartbeat_tests::database().await,
            reads: AtomicUsize::new(0),
            writes: AtomicUsize::new(0),
            fail_read: AtomicBool::new(false),
            fail_write: AtomicBool::new(false),
            pause_read: AtomicBool::new(false),
            read_started: tokio::sync::Notify::new(),
            resume_read: tokio::sync::Notify::new(),
        })
    }
    fn reset(&self) {
        self.reads.store(0, SeqCst);
        self.writes.store(0, SeqCst);
    }
    fn assert_calls(&self, reads: usize, writes: usize) {
        assert_eq!(
            (self.reads.load(SeqCst), self.writes.load(SeqCst)),
            (reads, writes)
        );
    }
}

#[async_trait]
impl DbPlugin for CountingDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.reads.fetch_add(1, SeqCst);
        if self.fail_read.load(SeqCst) {
            return Err(DbError::Backend("read unavailable".into()));
        }
        let rows = self.inner.query(statement).await?;
        if self.pause_read.swap(false, SeqCst) {
            self.read_started.notify_one();
            self.resume_read.notified().await;
        }
        Ok(rows)
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.writes.fetch_add(1, SeqCst);
        if self.fail_write.load(SeqCst) {
            return Err(DbError::Backend("write unavailable".into()));
        }
        self.inner.execute(statement).await
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

fn repo(db: Arc<CountingDb>) -> PersistentBotRepo {
    PersistentBotRepo::with_sql_flavor(db, DbSqlFlavor::Sqlite)
}

fn identity(id: &str) -> BotIdentity {
    BotIdentity {
        id: id.into(),
        token: None,
        deleted: false,
        capabilities: BotCapabilities::default(),
        env: None,
        created_by: None,
        actor_kind: bcs_service_api::ActorKind::Bot,
        status: bcs_service_api::ActorStatus::Online,
    }
}
async fn attach(repo: &PersistentBotRepo, id: &str, token: &str) {
    let mut op = repo.begin_identity_operation();
    op.lock_identity(id).await.unwrap();
    op.stored_identity().await.unwrap();
    op.apply(BotIdentityUpdate {
        identity: identity(id),
        token: token.into(),
        replace_persistent_token: false,
    })
    .await
    .unwrap();
}
async fn persist(repo: &PersistentBotRepo, id: &str, token: &str) {
    repo.register_with_owner_and_token(id.into(), BotCapabilities::default(), "owner", token)
        .await
        .unwrap();
}

#[tokio::test]
async fn successful_id_miss_is_reused_only_within_the_operation() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    let mut op = repo.begin_identity_operation();
    assert!(op.lock_identity("absent").await.unwrap().is_none());
    assert!(op.stored_identity().await.unwrap().is_none());
    assert!(op.stored_identity().await.unwrap().is_none());
    db.assert_calls(1, 0);
    assert!(repo.bots.read().await.is_empty());
    assert!(repo.token_to_bot.read().await.is_empty());
    drop(op);
    let mut next = repo.begin_identity_operation();
    next.lock_identity("absent").await.unwrap();
    assert!(next.stored_identity().await.unwrap().is_none());
    db.assert_calls(2, 0);
}

#[tokio::test]
async fn token_miss_does_not_hide_a_different_uuid_lookup() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "saved", "real-token").await;
    db.reset();
    let mut op = repo.begin_identity_operation();
    assert!(op.token_owner("wrong-token").await.unwrap().is_none());
    assert!(op.token_owner("wrong-token").await.unwrap().is_none());
    op.lock_identity("saved").await.unwrap();
    let stored = op.stored_identity().await.unwrap().unwrap();
    assert_eq!(stored.token.as_deref(), Some("real-token"));
    assert_eq!(op.stored_identity().await.unwrap().unwrap().id, "saved");
    db.assert_calls(2, 0);
}

#[tokio::test]
async fn cold_token_row_is_reused_after_locking() {
    let db = CountingDb::new().await;
    persist(&repo(db.clone()), "saved", "real-token").await;
    let cold = repo(db.clone());
    db.reset();
    let mut op = cold.begin_identity_operation();
    assert_eq!(
        op.token_owner("real-token").await.unwrap().as_deref(),
        Some("saved")
    );
    assert!(op.lock_identity("saved").await.unwrap().is_none());
    assert_eq!(op.stored_identity().await.unwrap().unwrap().id, "saved");
    assert_eq!(op.stored_identity().await.unwrap().unwrap().id, "saved");
    db.assert_calls(1, 0);
}

#[tokio::test]
async fn read_error_never_becomes_cached_absence_or_changes_memory() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    attach(&repo, "temporary", "old-token").await;
    repo.disconnect_streaming("temporary").await;
    db.reset();
    db.fail_read.store(true, SeqCst);
    let mut op = repo.begin_identity_operation();
    op.lock_identity("temporary").await.unwrap();
    assert!(op.stored_identity().await.is_err());
    assert_eq!(
        repo.bots.read().await["temporary"].session_token.as_deref(),
        Some("old-token")
    );
    db.fail_read.store(false, SeqCst);
    assert!(op.stored_identity().await.unwrap().is_none());
    assert!(op.stored_identity().await.unwrap().is_none());
    db.assert_calls(2, 0);
}

#[tokio::test]
async fn expired_memory_facts_remain_visible_and_replacement_clears_old_indices() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    attach(&repo, "temporary", "old-token").await;
    repo.disconnect_streaming("temporary").await;
    repo.store_token_mapping("obsolete-alias".into(), "temporary".into())
        .await;
    repo.bots
        .write()
        .await
        .get_mut("temporary")
        .unwrap()
        .last_heartbeat = Instant::now() - BOT_EXPIRY - Duration::from_secs(1);
    let mut op = repo.begin_identity_operation();
    let memory = op.lock_identity("temporary").await.unwrap().unwrap();
    assert!(memory.last_heartbeat.elapsed() > BOT_EXPIRY);
    assert!(!memory.connected);
    assert!(op.stored_identity().await.unwrap().is_none());
    op.apply(BotIdentityUpdate {
        identity: identity("temporary"),
        token: "new-token".into(),
        replace_persistent_token: false,
    })
    .await
    .unwrap();
    let tokens = repo.token_to_bot.read().await;
    assert_eq!(tokens.len(), 1);
    assert_eq!(
        tokens.get("new-token").map(String::as_str),
        Some("temporary")
    );
}

#[tokio::test]
async fn token_lookup_racing_delete_reloads_the_deleted_row() {
    let db = CountingDb::new().await;
    persist(&repo(db.clone()), "saved", "old-token").await;
    let repo = Arc::new(repo(db.clone()));
    db.reset();
    db.pause_read.store(true, SeqCst);
    let reading = {
        let repo = repo.clone();
        tokio::spawn(async move {
            let mut op = repo.begin_identity_operation();
            let id = op.token_owner("old-token").await.unwrap().unwrap();
            op.lock_identity(&id).await.unwrap();
            op.stored_identity().await.unwrap().unwrap().deleted
        })
    };
    db.read_started.notified().await;
    assert!(repo.soft_delete("saved").await);
    db.resume_read.notify_one();
    assert!(reading.await.unwrap());
    db.assert_calls(2, 1);
}

#[tokio::test]
async fn identity_scope_blocks_writers_until_dropped() {
    let db = CountingDb::new().await;
    let repo = repo(db);
    attach(&repo, "saved", "old-token").await;
    let mut op = repo.begin_identity_operation();
    op.lock_identity("saved").await.unwrap();
    let writer = repo.add_bot_info("saved", "agent_token", "new-runtime".into());
    tokio::pin!(writer);
    assert!(!futures_poll(writer.as_mut()).await);
    drop(op);
    tokio::time::timeout(Duration::from_secs(1), writer)
        .await
        .unwrap();
    assert_eq!(
        repo.get_bot_info("saved", "agent_token").await.as_deref(),
        Some("new-runtime")
    );
}
async fn futures_poll<F: Future>(future: std::pin::Pin<&mut F>) -> bool {
    tokio::select! { biased; _ = future => true, _ = tokio::task::yield_now() => false }
}

#[tokio::test]
async fn cancellation_releases_the_identity_scope() {
    let db = CountingDb::new().await;
    let repo = Arc::new(repo(db));
    let (ready, waiting) = oneshot::channel();
    let task = {
        let repo = repo.clone();
        tokio::spawn(async move {
            let mut op = repo.begin_identity_operation();
            op.lock_identity("saved").await.unwrap();
            ready.send(()).unwrap();
            std::future::pending::<()>().await;
            drop(op);
        })
    };
    waiting.await.unwrap();
    task.abort();
    assert!(task.await.unwrap_err().is_cancelled());
    let mut op = repo.begin_identity_operation();
    tokio::time::timeout(Duration::from_secs(1), op.lock_identity("saved"))
        .await
        .unwrap()
        .unwrap();
}

#[tokio::test]
async fn failed_conditional_token_update_does_not_attach_memory() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "saved", "original-token").await;
    let mut op = repo.begin_identity_operation();
    op.lock_identity("saved").await.unwrap();
    let stored = op.stored_identity().await.unwrap().unwrap();
    db.inner
        .execute(DbStatement::new(
            "UPDATE bcs_bots SET session_token = 'concurrent-token' WHERE bot_uuid = 'saved'",
        ))
        .await
        .unwrap();
    assert!(matches!(
        op.apply(BotIdentityUpdate {
            identity: stored,
            token: "replacement".into(),
            replace_persistent_token: true
        })
        .await,
        Err(ServiceError::Conflict(_))
    ));
    assert!(!repo.is_connected("saved").await);
    assert_eq!(
        repo.bots.read().await["saved"].session_token.as_deref(),
        Some("original-token")
    );
}

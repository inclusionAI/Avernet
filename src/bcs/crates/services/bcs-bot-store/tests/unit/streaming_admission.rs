use super::*;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbResult, DbTransactionStep, DbTransactionStepResult,
};
use bcs_service_api::{BotConnectParams, ConnectError};
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
fn params(id: Option<&str>, token: Option<&str>) -> BotConnectParams {
    BotConnectParams {
        bot_id: id.map(str::to_owned),
        token: token.map(str::to_owned),
        ..Default::default()
    }
}
async fn expire(repo: &PersistentBotRepo, id: &str) {
    repo.bots.write().await.get_mut(id).unwrap().last_heartbeat =
        Instant::now() - BOT_EXPIRY - Duration::from_secs(1);
}
async fn persist(repo: &PersistentBotRepo, id: &str, token: &str) {
    repo.register_with_owner_and_token(
        id.into(),
        BotCapabilities {
            name: Some("Preserved name".into()),
            ..Default::default()
        },
        "owner",
        token,
    )
    .await
    .unwrap();
}

#[tokio::test]
async fn expired_temporary_reclaim_uses_one_read_and_removes_all_old_tokens() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    let first = repo
        .connect_streaming(params(Some("temporary"), None))
        .await
        .unwrap();
    repo.disconnect_streaming("temporary").await;
    repo.store_token_mapping("obsolete-alias".into(), "temporary".into())
        .await;
    expire(&repo, "temporary").await;
    db.reset();
    let second = repo
        .connect_streaming(params(Some("temporary"), Some(&first.token)))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert!(second.is_new);
    assert_ne!(first.token, second.token);
    let tokens = repo.token_to_bot.read().await;
    assert!(!tokens.contains_key(&first.token));
    assert!(!tokens.contains_key("obsolete-alias"));
    assert_eq!(
        tokens.get(&second.token).map(String::as_str),
        Some("temporary")
    );
}

#[tokio::test]
async fn unexpired_temporary_identity_requires_its_token_and_keeps_it() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    let first = repo
        .connect_streaming(params(Some("temporary"), None))
        .await
        .unwrap();
    repo.disconnect_streaming("temporary").await;
    db.reset();
    assert!(matches!(
        repo.connect_streaming(params(Some("temporary"), None))
            .await,
        Err(ConnectError::AlreadyRegistered(_))
    ));
    db.assert_calls(0, 0);
    let second = repo
        .connect_streaming(params(None, Some(&first.token)))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert!(!second.is_new);
    assert_eq!(first.token, second.token);
}

#[tokio::test]
async fn hot_persisted_reconnect_refreshes_once_without_writing_back() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "persistent", "real-token").await;
    db.reset();
    let result = repo
        .connect_streaming(params(Some("ignored-id"), Some("real-token")))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert!(!result.is_new);
    assert_eq!(result.bot_uuid, "persistent");
    assert_eq!(
        repo.bots.read().await["persistent"]
            .capabilities
            .name
            .as_deref(),
        Some("Preserved name")
    );
}

#[tokio::test]
async fn cold_token_only_reconnect_reuses_full_row() {
    let db = CountingDb::new().await;
    persist(&repo(db.clone()), "persistent", "real-token").await;
    let cold = repo(db.clone());
    db.reset();
    let result = cold
        .connect_streaming(params(None, Some("real-token")))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert!(!result.is_new);
    assert_eq!(result.bot_uuid, "persistent");
}

#[tokio::test]
async fn expired_persisted_identity_is_reconnected_only_by_its_token() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "persistent", "real-token").await;
    expire(&repo, "persistent").await;
    db.reset();
    assert!(matches!(
        repo.connect_streaming(params(Some("persistent"), None))
            .await,
        Err(ConnectError::AlreadyRegistered(_))
    ));
    db.assert_calls(1, 0);
    db.reset();
    let result = repo
        .connect_streaming(params(None, Some("real-token")))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert!(!result.is_new);
    assert_eq!(result.token, "real-token");
}

#[tokio::test]
async fn active_connection_is_never_reclaimed_by_another_claimant() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    let first = repo
        .connect_streaming(params(Some("active"), None))
        .await
        .unwrap();
    expire(&repo, "active").await;
    db.reset();
    assert!(matches!(
        repo.connect_streaming(params(Some("active"), None)).await,
        Err(ConnectError::AlreadyConnected(_))
    ));
    db.assert_calls(0, 0);
    // Historical authenticated reconnect semantics are preserved.
    let result = repo
        .connect_streaming(params(None, Some(&first.token)))
        .await
        .unwrap();
    assert!(!result.is_new);
    assert_eq!(result.token, first.token);
}

#[tokio::test]
async fn deleted_identity_is_rejected_with_one_read() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "deleted", "old-token").await;
    assert!(repo.soft_delete("deleted").await);
    db.reset();
    assert!(matches!(
        repo.connect_streaming(params(Some("deleted"), None)).await,
        Err(ConnectError::AlreadyRegistered(_))
    ));
    db.assert_calls(1, 0);
    assert!(!repo.bots.read().await.contains_key("deleted"));
}

#[tokio::test]
async fn mock_promotion_is_durable_and_does_not_reread_identity() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "mock", "MOCK_test").await;
    repo.add_bot_info("mock", "agent_token", "runtime-credential".into())
        .await;
    db.reset();
    let result = repo
        .connect_streaming(params(Some("mock"), None))
        .await
        .unwrap();
    db.assert_calls(1, 1);
    assert!(result.is_new);
    assert_eq!(
        repo.get_bot_info("mock", "agent_token").await.as_deref(),
        Some("runtime-credential")
    );
    assert!(!bcs_service_api::is_mock_token(&result.token));
    let cold = PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Sqlite);
    db.reset();
    let reconnected = cold
        .connect_streaming(params(None, Some(&result.token)))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert_eq!(reconnected.bot_uuid, "mock");
    assert!(!reconnected.is_new);
}

#[tokio::test]
async fn database_failures_never_create_or_promote_an_identity() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    db.fail_read.store(true, SeqCst);
    assert!(matches!(
        repo.connect_streaming(params(Some("absent"), None)).await,
        Err(ConnectError::InternalError(_))
    ));
    assert!(repo.bots.read().await.is_empty());
    db.fail_read.store(false, SeqCst);
    persist(&repo, "mock", "MOCK_test").await;
    db.fail_write.store(true, SeqCst);
    assert!(matches!(
        repo.connect_streaming(params(Some("mock"), None)).await,
        Err(ConnectError::InternalError(_))
    ));
    assert!(!repo.is_connected("mock").await);
    assert_eq!(repo.load_token("mock").await.as_deref(), Some("MOCK_test"));
}

#[tokio::test]
async fn simultaneous_claims_have_one_winner_and_one_identity_read() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    let (a, b) = tokio::join!(
        repo.connect_streaming(params(Some("race"), None)),
        repo.connect_streaming(params(Some("race"), None))
    );
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
    db.assert_calls(1, 0);
    assert_eq!(repo.token_to_bot.read().await.len(), 1);
}

#[tokio::test]
async fn token_lookup_racing_delete_cannot_reuse_the_old_row() {
    let db = CountingDb::new().await;
    let repo = Arc::new(repo(db.clone()));
    persist(&repo, "deleted", "old-token").await;
    repo.bots.write().await.clear();
    repo.token_to_bot.write().await.clear();
    db.reset();
    db.pause_read.store(true, SeqCst);
    let connecting = {
        let repo = repo.clone();
        tokio::spawn(async move {
            repo.connect_streaming(params(None, Some("old-token")))
                .await
        })
    };
    db.read_started.notified().await;
    assert!(repo.soft_delete("deleted").await);
    db.resume_read.notify_one();
    assert!(matches!(
        connecting.await.unwrap(),
        Err(ConnectError::AlreadyRegistered(_))
    ));
    assert_eq!(db.reads.load(SeqCst), 2);
    assert!(!repo.is_connected("deleted").await);
}

#[tokio::test]
async fn changed_persistent_token_invalidates_even_a_current_memory_token() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "rotated", "old-token").await;
    db.inner
        .execute(DbStatement::new(
            "UPDATE bcs_bots SET session_token = 'rotated-token' WHERE bot_uuid = 'rotated'",
        ))
        .await
        .unwrap();
    db.reset();
    assert!(matches!(
        repo.connect_streaming(params(None, Some("old-token")))
            .await,
        Err(ConnectError::AlreadyRegistered(_))
    ));
    db.assert_calls(1, 0);
    assert!(!repo.is_connected("rotated").await);
}

#[tokio::test]
async fn rotated_persistent_token_can_reconnect_before_old_memory_expires() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "rotated", "old-token").await;
    repo.save_token("rotated", "new-token").await.unwrap();
    db.reset();
    let result = repo
        .connect_streaming(params(None, Some("new-token")))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert!(!result.is_new);
    assert_eq!(result.token, "new-token");
    assert_eq!(
        repo.load_token("rotated").await.as_deref(),
        Some("new-token")
    );
    assert!(!repo.token_to_bot.read().await.contains_key("old-token"));
}

#[tokio::test]
async fn reconnect_does_not_overwrite_a_concurrent_runtime_credential_update() {
    let db = CountingDb::new().await;
    let repo = Arc::new(repo(db.clone()));
    persist(&repo, "runtime", "session-token").await;
    db.pause_read.store(true, SeqCst);
    let connecting = {
        let repo = repo.clone();
        tokio::spawn(async move {
            repo.connect_streaming(params(None, Some("session-token")))
                .await
        })
    };
    db.read_started.notified().await;
    let updating = repo.add_bot_info("runtime", "agent_token", "new-runtime-credential".into());
    tokio::pin!(updating);
    // Poll the writer while the connection's database read is paused. The
    // writer either completes first or waits behind the identity lock.
    let completed = tokio::select! {
        biased;
        _ = &mut updating => true,
        _ = tokio::task::yield_now() => false,
    };
    db.resume_read.notify_one();
    connecting.await.unwrap().unwrap();
    if !completed {
        updating.await;
    }
    assert_eq!(
        repo.get_bot_info("runtime", "agent_token").await.as_deref(),
        Some("new-runtime-credential")
    );
}

#[tokio::test]
async fn token_index_is_a_lookup_hint_and_does_not_override_storage_authentication() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "rotated", "old-token").await;
    repo.save_token("rotated", "new-token").await.unwrap();
    repo.store_token_mapping("new-token".into(), "rotated".into())
        .await;
    db.reset();
    let result = repo
        .connect_streaming(params(None, Some("new-token")))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert_eq!(result.token, "new-token");
    let temporary = repo
        .connect_streaming(params(Some("temporary"), None))
        .await
        .unwrap();
    expire(&repo, "temporary").await;
    repo.store_token_mapping("stale-alias".into(), "temporary".into())
        .await;
    assert!(matches!(
        repo.connect_streaming(params(None, Some("stale-alias")))
            .await,
        Err(ConnectError::AlreadyConnected(_))
    ));
    assert_eq!(
        repo.load_token("temporary").await.as_deref(),
        Some(temporary.token.as_str())
    );
}

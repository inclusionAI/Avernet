//! Full Core -> repository contract: SQL budgets include capability refresh.
use bcs_bot::BotCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::*;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::{BotCapabilities, BotConnectParams, BotRegistryCoreService, ConnectionKind};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering::SeqCst},
};

struct CountDb {
    inner: LocalSqliteDbPlugin,
    reads: AtomicUsize,
    writes: AtomicUsize,
}
#[async_trait::async_trait]
impl DbPlugin for CountDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.reads.fetch_add(1, SeqCst);
        self.inner.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.writes.fetch_add(1, SeqCst);
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
impl CountDb {
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
#[tokio::test]
async fn streaming_core_reuses_identity_and_does_not_write_back_loaded_capabilities() {
    let db = Arc::new(CountDb {
        inner: LocalSqliteDbPlugin::new().unwrap(),
        reads: AtomicUsize::new(0),
        writes: AtomicUsize::new(0),
    });
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
        bot_uuid TEXT, env TEXT, session_token TEXT, is_deleted INTEGER DEFAULT 0,
        name TEXT, bot_info TEXT, visibility TEXT, status TEXT DEFAULT 'online',
        actor_kind TEXT DEFAULT 'bot', created_by TEXT, agent_code TEXT,
        registered_at TEXT, updated_at TEXT, PRIMARY KEY(bot_uuid, env))",
    ))
    .await
    .unwrap();
    let repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        db.clone(),
        DbSqlFlavor::Sqlite,
    ));
    let core = BotCore::with_repo(repo.clone());
    db.reset();
    let first = core
        .connect_bot(
            BotConnectParams {
                bot_id: Some("new-bot".into()),
                ..Default::default()
            },
            ConnectionKind::Streaming,
        )
        .await
        .unwrap();
    assert!(first.is_new);
    db.assert_calls(1, 0);
    repo.disconnect_streaming(&first.bot_uuid).await;
    repo.register_with_owner_and_token(
        first.bot_uuid.clone(),
        BotCapabilities {
            name: Some("Persisted".into()),
            ..Default::default()
        },
        "owner",
        &first.token,
    )
    .await
    .unwrap();
    db.reset();
    let restored = core
        .connect_bot(
            BotConnectParams {
                token: Some(first.token.clone()),
                ..Default::default()
            },
            ConnectionKind::Streaming,
        )
        .await
        .unwrap();
    assert!(!restored.is_new);
    assert_eq!(restored.token, first.token);
    db.assert_calls(1, 0);
    // Restart: a single indexed token read carries credentials and capabilities.
    let cold = BotCore::with_repo(Arc::new(PersistentBotRepo::with_sql_flavor(
        db.clone(),
        DbSqlFlavor::Sqlite,
    )));
    db.reset();
    let restored = cold
        .connect_bot(
            BotConnectParams {
                token: Some(first.token),
                ..Default::default()
            },
            ConnectionKind::Streaming,
        )
        .await
        .unwrap();
    assert!(!restored.is_new);
    assert_eq!(restored.bot_uuid, "new-bot");
    db.assert_calls(1, 0);
}

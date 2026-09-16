//! Full Core -> repository contract: SQL budgets include capability refresh.
use bcs_bot::BotCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::*;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::{BotCapabilities, BotConnectParams, BotRegistryCoreService, ConnectionKind};
use std::sync::{
    atomic::{AtomicUsize, Ordering::SeqCst},
    Arc,
};

#[tokio::test]
async fn streaming_core_reuses_identity_and_does_not_write_back_loaded_capabilities() {
    let db = CountingDb::new().await;
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

#[test]
fn streaming_admission_policy_stays_out_of_store() {
    // Storage implements facts and atomic mutations; it must not select an
    // identity, issue credentials, or return business admission decisions.
    for source in [
        include_str!("../../bcs-bot-store/src/admission.rs"),
        include_str!("../../bcs-bot-store/src/admission_sql.rs"),
        include_str!("../../bcs-bot-store/src/admission_memory.rs"),
    ] {
        for business_symbol in [
            "BotConnectParams",
            "BotConnectResult",
            "ConnectError",
            "Uuid::new_v4",
            "is_mock_token",
        ] {
            assert!(
                !source.contains(business_symbol),
                "store owns business admission symbol {business_symbol}"
            );
        }
    }
}

use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::{mock_token, ConnectError, ConnectStreamError};
use std::sync::atomic::AtomicBool;

async fn test_database() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
        bot_uuid TEXT, env TEXT, session_token TEXT, is_deleted INTEGER DEFAULT 0,
        name TEXT, bot_info TEXT, visibility TEXT, status TEXT DEFAULT 'online',
        actor_kind TEXT DEFAULT 'bot', created_by TEXT, agent_code TEXT,
        registered_at TEXT, updated_at TEXT, PRIMARY KEY(bot_uuid, env))",
    ))
    .await
    .unwrap();
    db
}
async fn connect(
    repo: &Arc<PersistentBotRepo>,
    params: BotConnectParams,
) -> Result<bcs_service_api::BotConnectResult, ConnectError> {
    BotCore::with_repo(repo.clone())
        .connect_bot(params, ConnectionKind::Streaming)
        .await
}
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
            inner: test_database().await,
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

#[async_trait::async_trait]
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

fn repo(db: Arc<CountingDb>) -> Arc<PersistentBotRepo> {
    Arc::new(PersistentBotRepo::with_sql_flavor(db, DbSqlFlavor::Sqlite))
}
fn params(id: Option<&str>, token: Option<&str>) -> BotConnectParams {
    BotConnectParams {
        bot_id: id.map(str::to_owned),
        token: token.map(str::to_owned),
        ..Default::default()
    }
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
async fn unexpired_temporary_identity_requires_its_token_and_keeps_it() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    let first = connect(&repo, params(Some("temporary"), None))
        .await
        .unwrap();
    repo.disconnect_streaming("temporary").await;
    db.reset();
    assert!(matches!(
        connect(&repo, params(Some("temporary"), None)).await,
        Err(ConnectError::AlreadyRegistered(_))
    ));
    db.assert_calls(0, 0);
    let second = connect(&repo, params(None, Some(&first.token)))
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
    let result = connect(&repo, params(Some("ignored-id"), Some("real-token")))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert!(!result.is_new);
    assert_eq!(result.bot_uuid, "persistent");
    assert_eq!(
        repo.get("persistent")
            .await
            .unwrap()
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
    let result = connect(&cold, params(None, Some("real-token")))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert!(!result.is_new);
    assert_eq!(result.bot_uuid, "persistent");
}

#[tokio::test]
async fn deleted_identity_is_rejected_with_one_read() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "deleted", "old-token").await;
    assert!(repo.soft_delete("deleted").await);
    db.reset();
    assert!(matches!(
        connect(&repo, params(Some("deleted"), None)).await,
        Err(ConnectError::AlreadyRegistered(_))
    ));
    db.assert_calls(1, 0);
    assert!(!repo.is_connected("deleted").await);
}

#[tokio::test]
async fn mock_promotion_is_durable_and_does_not_reread_identity() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "mock", "MOCK_test").await;
    repo.add_bot_info("mock", "agent_token", "runtime-credential".into())
        .await;
    db.reset();
    let result = connect(&repo, params(Some("mock"), None)).await.unwrap();
    db.assert_calls(1, 1);
    assert!(result.is_new);
    assert_eq!(
        repo.get_bot_info("mock", "agent_token").await.as_deref(),
        Some("runtime-credential")
    );
    assert!(!bcs_service_api::is_mock_token(&result.token));
    let cold = Arc::new(PersistentBotRepo::with_sql_flavor(
        db.clone(),
        DbSqlFlavor::Sqlite,
    ));
    db.reset();
    let reconnected = connect(&cold, params(None, Some(&result.token)))
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
        connect(&repo, params(Some("absent"), None)).await,
        Err(ConnectError::InternalError(_))
    ));
    assert!(repo.list_connected().await.is_empty());
    db.fail_read.store(false, SeqCst);
    persist(&repo, "mock", "MOCK_test").await;
    db.fail_write.store(true, SeqCst);
    assert!(matches!(
        connect(&repo, params(Some("mock"), None)).await,
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
        connect(&repo, params(Some("race"), None)),
        connect(&repo, params(Some("race"), None))
    );
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
    db.assert_calls(1, 0);
    assert_eq!(repo.list_connected().await.len(), 1);
}

#[tokio::test]
async fn token_lookup_racing_delete_cannot_reuse_the_old_row() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "deleted", "old-token").await;
    let repo = self::repo(db.clone());
    db.reset();
    db.pause_read.store(true, SeqCst);
    let connecting = {
        let repo = repo.clone();
        tokio::spawn(async move { connect(&repo, params(None, Some("old-token"))).await })
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
        connect(&repo, params(None, Some("old-token"))).await,
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
    let result = connect(&repo, params(None, Some("new-token")))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert!(!result.is_new);
    assert_eq!(result.token, "new-token");
    assert_eq!(
        repo.load_token("rotated").await.as_deref(),
        Some("new-token")
    );
    assert!(repo.find_bot_by_token("old-token").await.is_none());
}

#[tokio::test]
async fn reconnect_does_not_overwrite_a_concurrent_runtime_credential_update() {
    let db = CountingDb::new().await;
    let repo = repo(db.clone());
    persist(&repo, "runtime", "session-token").await;
    db.pause_read.store(true, SeqCst);
    let connecting = {
        let repo = repo.clone();
        tokio::spawn(async move { connect(&repo, params(None, Some("session-token"))).await })
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
    let result = connect(&repo, params(None, Some("new-token")))
        .await
        .unwrap();
    db.assert_calls(1, 0);
    assert_eq!(result.token, "new-token");
    let temporary = connect(&repo, params(Some("temporary"), None))
        .await
        .unwrap();

    repo.store_token_mapping("stale-alias".into(), "temporary".into())
        .await;
    assert!(matches!(
        connect(&repo, params(None, Some("stale-alias"))).await,
        Err(ConnectError::AlreadyConnected(_))
    ));
    assert_eq!(
        repo.load_token("temporary").await.as_deref(),
        Some(temporary.token.as_str())
    );
}

#[tokio::test]
async fn connect_or_promote_streaming_creates_bot_when_absent() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let repo = BotCore::with_repo(Arc::new(MemoryBotRepo::with_base_dir(
        temp_dir.path().to_path_buf(),
    )));

    let token = repo
        .connect_or_promote_streaming("plugin-bot:alice".to_string())
        .await
        .expect("create");
    assert!(!token.is_empty());
    // newly created token is a real (non-MOCK) value the bot can reconnect with
    assert!(!bcs_service_api::is_mock_token(&token));
    assert_eq!(
        repo.load_token("plugin-bot:alice").await.as_deref(),
        Some(token.as_str())
    );
}

#[tokio::test]
async fn connect_or_promote_streaming_promotes_mock_to_real() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let repo = BotCore::with_repo(Arc::new(MemoryBotRepo::with_base_dir(
        temp_dir.path().to_path_buf(),
    )));

    // provider registered a plugin bot, so the stored token is MOCK
    let mock = mock_token();
    repo.register_with_owner_and_token(
        "plugin-bot:alice".to_string(),
        BotCapabilities {
            name: Some("Plugin Bot".to_string()),
            visibility: "protected".to_string(),
            ..Default::default()
        },
        "11111111",
        &mock,
    )
    .await
    .expect("register plugin bot with mock token");
    assert!(bcs_service_api::is_mock_token(&mock));

    // plugin reconnects (empty token), bot located by id -> mock promoted to real
    let promoted = repo
        .connect_or_promote_streaming("plugin-bot:alice".to_string())
        .await
        .expect("promote");
    assert!(!bcs_service_api::is_mock_token(&promoted));
    assert_eq!(
        repo.load_token("plugin-bot:alice").await.as_deref(),
        Some(promoted.as_str())
    );
}

#[tokio::test]
async fn connect_or_promote_streaming_promote_persists_real_token_to_db_survives_restart() {
    // Scenario-3 / promote_mock durability: after promotion, the real token must
    // be in `bcs_bots` (not the stale MOCK), so a fresh repo built on the same DB
    // resolves the bot by the promoted token — i.e. no half-state where memory
    // holds the real token while the DB still keeps the MOCK.
    let db = test_database().await;
    let repo = BotCore::with_repo(Arc::new(PersistentBotRepo::new(db.clone())));

    // provider pre-registers a plugin bot → DB holds MOCK
    let mock = mock_token();
    repo.register_with_owner_and_token(
        "plugin-bot:alice".to_string(),
        BotCapabilities {
            name: Some("Plugin Bot".to_string()),
            visibility: "protected".to_string(),
            ..Default::default()
        },
        "11111111",
        &mock,
    )
    .await
    .expect("register plugin bot with mock token");
    assert!(bcs_service_api::is_mock_token(&mock));

    // plugin reconnects → promote MOCK to a real token
    let promoted = repo
        .connect_or_promote_streaming("plugin-bot:alice".to_string())
        .await
        .expect("promote");
    assert!(!bcs_service_api::is_mock_token(&promoted));

    // Simulate a BCS restart: a fresh repo reading the SAME DB.
    let repo_after = PersistentBotRepo::new(db);
    let persisted = repo_after
        .load_token("plugin-bot:alice")
        .await
        .expect("token present after restart");
    assert_eq!(
        persisted, promoted,
        "DB keeps the real promoted token (not the stale MOCK) across a restart"
    );
}

#[tokio::test]
async fn connect_or_promote_streaming_refuses_real_token_claim_with_already_registered() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let repo = BotCore::with_repo(Arc::new(MemoryBotRepo::with_base_dir(
        temp_dir.path().to_path_buf(),
    )));

    // a normally-onboarded bot with a real token, not connected
    repo.register_with_owner_and_token(
        "real-bot:alice".to_string(),
        BotCapabilities {
            name: Some("Real Bot".to_string()),
            visibility: "protected".to_string(),
            ..Default::default()
        },
        "11111111",
        "real-runtime-token",
    )
    .await
    .expect("register real-token bot");

    let err = repo
        .connect_or_promote_streaming("real-bot:alice".to_string())
        .await
        .expect_err("real-token bot refused");
    assert!(matches!(err, ConnectStreamError::AlreadyRegistered(id) if id == "real-bot:alice"));
    // token untouched
    assert_eq!(
        repo.load_token("real-bot:alice").await.as_deref(),
        Some("real-runtime-token")
    );
}

#[tokio::test]
async fn connect_or_promote_streaming_refuses_real_token_connected_with_already_connected() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let repo = BotCore::with_repo(Arc::new(MemoryBotRepo::with_base_dir(
        temp_dir.path().to_path_buf(),
    )));

    // first connect creates the bot + real token + an active ws connection
    let first = repo
        .connect_or_promote_streaming("live-bot:alice".to_string())
        .await
        .expect("first connect");
    assert!(repo.is_connected("live-bot:alice").await);

    // second connect for the same live bot => already connected
    let err = repo
        .connect_or_promote_streaming("live-bot:alice".to_string())
        .await
        .expect_err("live bot already connected");
    assert!(matches!(err, ConnectStreamError::AlreadyConnected(id) if id == "live-bot:alice"));
    assert_eq!(
        repo.load_token("live-bot:alice").await.as_deref(),
        Some(first.as_str())
    );
}

#[tokio::test]
async fn local_core_reconnects_after_restart_and_rejects_deleted_identity() {
    let dir = tempfile::tempdir().unwrap();
    let core = BotCore::with_base_dir(dir.path().into());
    core.register_with_owner_and_token(
        "saved".into(),
        BotCapabilities {
            name: Some("Saved name".into()),
            ..Default::default()
        },
        "owner",
        "saved-token",
    )
    .await
    .unwrap();
    let cold = BotCore::with_base_dir(dir.path().into());
    assert!(matches!(
        cold.connect_bot(params(Some("saved"), None), ConnectionKind::Streaming)
            .await,
        Err(ConnectError::AlreadyRegistered(_))
    ));
    let restored = cold
        .connect_bot(params(None, Some("saved-token")), ConnectionKind::Streaming)
        .await
        .unwrap();
    assert!(!restored.is_new);
    assert_eq!(
        cold.get("saved")
            .await
            .unwrap()
            .capabilities
            .name
            .as_deref(),
        Some("Saved name")
    );
    assert!(cold.soft_delete("saved").await);
    assert!(matches!(
        cold.connect_bot(
            params(Some("saved"), Some("saved-token")),
            ConnectionKind::Streaming
        )
        .await,
        Err(ConnectError::AlreadyRegistered(_))
    ));
}

#[tokio::test]
async fn local_core_mock_promotion_preserves_hidden_status_and_persistence() {
    let dir = tempfile::tempdir().unwrap();
    let core = BotCore::with_base_dir(dir.path().into());
    core.register_with_owner_and_token(
        "mock".into(),
        BotCapabilities::default(),
        "owner",
        "MOCK_test",
    )
    .await
    .unwrap();
    core.update_actor_status("mock", bcs_service_api::ActorStatus::Hidden)
        .await
        .unwrap();
    let first = core
        .connect_bot(params(Some("mock"), None), ConnectionKind::Streaming)
        .await
        .unwrap();
    assert!(first.is_new);
    assert!(!bcs_service_api::is_mock_token(&first.token));
    assert_eq!(
        core.get("mock").await.unwrap().status,
        bcs_service_api::ActorStatus::Hidden
    );
    let cold = BotCore::with_base_dir(dir.path().into());
    let restored = cold
        .connect_bot(params(None, Some(&first.token)), ConnectionKind::Streaming)
        .await
        .unwrap();
    assert!(!restored.is_new);
    assert_eq!(restored.bot_uuid, "mock");
}

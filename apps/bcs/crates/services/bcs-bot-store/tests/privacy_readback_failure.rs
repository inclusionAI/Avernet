use async_trait::async_trait;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue,
};
use bcs_db_migrations as bootstrap_migrations;
use bcs_service_api::{
    BotCapabilities, BotControlPlanePatch, BotControlPlaneRepoPort, BotRepoPort,
    FriendCheckInStrategy,
};
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};
use std::time::Duration;
use tokio::sync::Notify;
#[path = "support/bot_profile_fixture.rs"]
mod fixture;

#[derive(Clone, Copy)]
enum Fault {
    ReadOnce,
    ReadAlways,
    Deleted,
}
struct PostCommitFaultDb {
    inner: Arc<dyn DbPlugin>,
    armed: AtomicBool,
    active: AtomicBool,
    queries: AtomicUsize,
    fault: Fault,
}
#[async_trait]
impl DbPlugin for PostCommitFaultDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.fetch_add(1, Ordering::SeqCst);
        let fail = match self.fault {
            Fault::ReadOnce => self.active.swap(false, Ordering::SeqCst),
            Fault::ReadAlways => self.active.load(Ordering::SeqCst),
            Fault::Deleted => false,
        };
        if fail {
            return Err(DbError::Backend("injected post-commit read failure".into()));
        }
        self.inner.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        let is_update = statement.sql().starts_with("UPDATE bcs_bots SET");
        let result = self.inner.execute(statement).await?;
        if is_update && self.armed.swap(false, Ordering::SeqCst) {
            if matches!(self.fault, Fault::Deleted) {
                self.inner
                    .execute(DbStatement::new(
                        "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = 'readback'",
                    ))
                    .await?;
            }
            self.active.store(true, Ordering::SeqCst);
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

async fn cached_discovery_results(
    repo: &PersistentBotRepo,
) -> Vec<Vec<bcs_service_api::RegisteredBot>> {
    vec![
        repo.list_active().await,
        repo.discover("Readback").await,
        repo.find_by_skills(&[]).await,
        repo.find_by_domains(&[]).await,
        repo.find_by_scopes(&[]).await,
    ]
}

async fn seeded(fault: Fault) -> (PersistentBotRepo, Arc<PostCommitFaultDb>) {
    let (db, _) = fixture::sqlite_repo().await;
    let wrapped = Arc::new(PostCommitFaultDb {
        inner: db,
        armed: AtomicBool::new(false),
        active: AtomicBool::new(false),
        queries: AtomicUsize::new(0),
        fault,
    });
    let repo = PersistentBotRepo::with_sql_flavor(wrapped.clone(), DbSqlFlavor::Sqlite);
    repo.register_with_owner_and_token(
        "readback".into(),
        BotCapabilities {
            name: Some("Readback".into()),
            ..Default::default()
        },
        "owner",
        "readback-token",
    )
    .await
    .unwrap();
    repo.patch_control_plane(
        "readback",
        &bcs_config::resolve_env_str(),
        BotControlPlanePatch {
            visibility: Some("public".into()),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        },
    )
    .await
    .unwrap()
    .unwrap();
    assert_eq!(
        repo.try_get("readback")
            .await
            .unwrap()
            .unwrap()
            .capabilities
            .visibility,
        "public"
    );
    wrapped.armed.store(true, Ordering::SeqCst);
    (repo, wrapped)
}

#[tokio::test]
async fn committed_privacy_restriction_survives_readback_error_in_single_and_batch_reads() {
    for (patch, expected) in [
        (
            BotControlPlanePatch {
                visibility: Some("private".into()),
                ..Default::default()
            },
            "private",
        ),
        (
            BotControlPlanePatch {
                friend_check_in_strategy: Some(FriendCheckInStrategy::Approval),
                ..Default::default()
            },
            "protected",
        ),
        (
            BotControlPlanePatch {
                friend_ext: Some(
                    serde_json::from_value(
                        serde_json::json!({"view_scope_agent_friend_deps": ["D1"]}),
                    )
                    .unwrap(),
                ),
                ..Default::default()
            },
            "protected",
        ),
    ] {
        let (repo, db) = seeded(Fault::ReadOnce).await;
        assert!(
            repo.patch_control_plane("readback", &bcs_config::resolve_env_str(), patch)
                .await
                .is_err()
        );
        let rows = db
            .inner
            .query(DbStatement::new(
                "SELECT visibility FROM bcs_bots WHERE bot_uuid = 'readback'",
            ))
            .await
            .unwrap();
        assert_eq!(rows[0].get("visibility"), Some(&DbValue::from(expected)));
        assert_eq!(
            repo.try_get("readback")
                .await
                .unwrap()
                .unwrap()
                .capabilities
                .visibility,
            expected
        );
        assert_eq!(
            repo.try_get_many(&["readback".into()]).await.unwrap()[0]
                .capabilities
                .visibility,
            expected
        );
        for candidates in cached_discovery_results(&repo).await {
            assert_eq!(candidates.len(), 1);
            assert_eq!(candidates[0].capabilities.visibility, expected);
        }
    }
}

#[tokio::test]
async fn persistent_read_failure_cannot_fall_back_to_old_public_cache() {
    let (repo, _) = seeded(Fault::ReadAlways).await;
    assert!(
        repo.patch_control_plane(
            "readback",
            &bcs_config::resolve_env_str(),
            BotControlPlanePatch {
                visibility: Some("private".into()),
                ..Default::default()
            }
        )
        .await
        .is_err()
    );
    assert!(repo.try_get("readback").await.is_err());
    assert!(repo.try_get_many(&["readback".into()]).await.is_err());
    for candidates in cached_discovery_results(&repo).await {
        assert!(
            candidates.is_empty(),
            "unreadable privacy metadata must not be advertised"
        );
    }
}

#[tokio::test]
async fn disappearance_after_update_invalidates_public_cache() {
    let (repo, _) = seeded(Fault::Deleted).await;
    assert!(
        repo.patch_control_plane(
            "readback",
            &bcs_config::resolve_env_str(),
            BotControlPlanePatch {
                visibility: Some("private".into()),
                ..Default::default()
            }
        )
        .await
        .unwrap()
        .is_none()
    );
    assert!(repo.try_get("readback").await.unwrap().is_none());
    assert!(
        repo.try_get_many(&["readback".into()])
            .await
            .unwrap()
            .is_empty()
    );
    for candidates in cached_discovery_results(&repo).await {
        assert!(
            candidates.is_empty(),
            "deleted privacy metadata must not be advertised"
        );
    }
}

#[tokio::test]
async fn legacy_discovery_batches_invalidated_metadata_without_querying_healthy_entries() {
    let (repo, db) = seeded(Fault::ReadOnce).await;
    db.armed.store(false, Ordering::SeqCst);
    repo.register_with_owner_and_token(
        "readback-2".into(),
        BotCapabilities {
            name: Some("Readback Two".into()),
            ..Default::default()
        },
        "owner",
        "readback-token-2",
    )
    .await
    .unwrap();
    let env = bcs_config::resolve_env_str();
    repo.patch_control_plane(
        "readback-2",
        &env,
        BotControlPlanePatch {
            visibility: Some("public".into()),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        },
    )
    .await
    .unwrap()
    .unwrap();
    db.queries.store(0, Ordering::SeqCst);
    assert_eq!(repo.find_by_domains(&[]).await.len(), 2);
    assert_eq!(
        db.queries.load(Ordering::SeqCst),
        0,
        "healthy cached discovery stays in memory"
    );
    for id in ["readback", "readback-2"] {
        db.armed.store(true, Ordering::SeqCst);
        assert!(
            repo.patch_control_plane(
                id,
                &env,
                BotControlPlanePatch {
                    visibility: Some("private".into()),
                    ..Default::default()
                }
            )
            .await
            .is_err()
        );
    }
    db.queries.store(0, Ordering::SeqCst);
    let records = repo.find_by_domains(&[]).await;
    assert_eq!(records.len(), 2);
    assert!(
        records
            .iter()
            .all(|bot| bot.capabilities.visibility == "private")
    );
    assert_eq!(
        db.queries.load(Ordering::SeqCst),
        1,
        "invalidated entries share one batch read, not N+1"
    );
}

struct BlockingReadbackDb {
    inner: Arc<dyn DbPlugin>,
    armed: AtomicBool,
    block_readback: AtomicBool,
    queries: AtomicUsize,
    readback_started: Notify,
    release_readback: Notify,
}

#[async_trait]
impl DbPlugin for BlockingReadbackDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.fetch_add(1, Ordering::SeqCst);
        if self.block_readback.swap(false, Ordering::SeqCst) {
            self.readback_started.notify_one();
            self.release_readback.notified().await;
        }
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        let is_update = statement.sql().starts_with("UPDATE bcs_bots SET");
        let result = self.inner.execute(statement).await?;
        if is_update && self.armed.swap(false, Ordering::SeqCst) {
            self.block_readback.store(true, Ordering::SeqCst);
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

#[tokio::test]
async fn privacy_readback_does_not_block_or_serve_stale_registry_reads() {
    let (db, _) = fixture::sqlite_repo().await;
    let wrapped = Arc::new(BlockingReadbackDb {
        inner: db,
        armed: AtomicBool::new(false),
        block_readback: AtomicBool::new(false),
        queries: AtomicUsize::new(0),
        readback_started: Notify::new(),
        release_readback: Notify::new(),
    });
    let repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        wrapped.clone(),
        DbSqlFlavor::Sqlite,
    ));
    let env = bcs_config::resolve_env_str();
    repo.register_with_owner_and_token(
        "readback".into(),
        BotCapabilities {
            name: Some("Readback".into()),
            ..Default::default()
        },
        "owner",
        "readback-token",
    )
    .await
    .unwrap();
    repo.patch_control_plane(
        "readback",
        &env,
        BotControlPlanePatch {
            visibility: Some("public".into()),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        },
    )
    .await
    .unwrap()
    .unwrap();

    wrapped.armed.store(true, Ordering::SeqCst);
    let patch_repo = repo.clone();
    let patch_env = env.clone();
    let patch = tokio::spawn(async move {
        patch_repo
            .patch_control_plane(
                "readback",
                &patch_env,
                BotControlPlanePatch {
                    visibility: Some("private".into()),
                    ..Default::default()
                },
            )
            .await
    });
    tokio::time::timeout(
        Duration::from_secs(1),
        wrapped.readback_started.notified(),
    )
    .await
    .expect("privacy patch must reach its read-back");

    let cached_read = tokio::time::timeout(
        Duration::from_millis(250),
        repo.try_get("readback"),
    )
    .await;
    let observed_visibility = cached_read
        .ok()
        .and_then(|result| result.ok().flatten())
        .map(|bot| bot.capabilities.visibility);
    let discovered = tokio::time::timeout(
        Duration::from_millis(250),
        repo.find_by_domains(&[]),
    )
    .await;
    let discovered_visibility = discovered
        .ok()
        .and_then(|bots| bots.into_iter().next())
        .map(|bot| bot.capabilities.visibility);
    wrapped.release_readback.notify_one();
    patch.await.unwrap().unwrap().unwrap();
    assert_eq!(
        observed_visibility.as_deref(),
        Some("private"),
        "an in-flight privacy read-back must bypass stale cache without blocking registry reads"
    );
    assert_eq!(
        discovered_visibility.as_deref(),
        Some("private"),
        "legacy discovery must also refresh in-flight privacy metadata"
    );
    assert_eq!(
        repo.try_get("readback")
            .await
            .unwrap()
            .unwrap()
            .capabilities
            .visibility,
        "private"
    );
}

#[tokio::test]
async fn cancelled_readback_stays_fail_closed_after_a_later_successful_write() {
    let (db, _) = fixture::sqlite_repo().await;
    let wrapped = Arc::new(BlockingReadbackDb {
        inner: db,
        armed: AtomicBool::new(false),
        block_readback: AtomicBool::new(false),
        queries: AtomicUsize::new(0),
        readback_started: Notify::new(),
        release_readback: Notify::new(),
    });
    let repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        wrapped.clone(),
        DbSqlFlavor::Sqlite,
    ));
    let env = bcs_config::resolve_env_str();
    repo.register_with_owner_and_token(
        "readback".into(),
        BotCapabilities {
            name: Some("Readback".into()),
            ..Default::default()
        },
        "owner",
        "readback-token",
    )
    .await
    .unwrap();
    wrapped.armed.store(true, Ordering::SeqCst);
    let patch_repo = repo.clone();
    let patch_env = env.clone();
    let patch = tokio::spawn(async move {
        patch_repo.patch_control_plane(
            "readback",
            &patch_env,
            BotControlPlanePatch {
                visibility: Some("private".into()),
                ..Default::default()
            },
        )
        .await
    });
    tokio::time::timeout(
        Duration::from_secs(1),
        wrapped.readback_started.notified(),
    )
    .await
    .expect("privacy patch must reach its read-back");
    patch.abort();
    assert!(patch.await.unwrap_err().is_cancelled());

    repo.patch_control_plane(
        "readback",
        &env,
        BotControlPlanePatch {
            visibility: Some("public".into()),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        },
    )
    .await
    .unwrap()
    .unwrap();
    wrapped.queries.store(0, Ordering::SeqCst);
    assert_eq!(
        repo.try_get("readback")
            .await
            .unwrap()
            .unwrap()
            .capabilities
            .visibility,
        "public"
    );
    assert_eq!(
        wrapped.queries.load(Ordering::SeqCst),
        1,
        "cancellation must leave a permanent persistence bypass, not a reusable stale cache"
    );
}

struct AfterReadbackDb {
    inner: Arc<dyn DbPlugin>,
    armed: AtomicBool,
    pause_after_read: AtomicBool,
    read_completed: Notify,
    release_readback: Notify,
}

#[async_trait]
impl DbPlugin for AfterReadbackDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if self.pause_after_read.swap(false, Ordering::SeqCst) {
            let rows = self.inner.query(statement).await?;
            self.read_completed.notify_one();
            self.release_readback.notified().await;
            return Ok(rows);
        }
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        let is_update = statement.sql().starts_with("UPDATE bcs_bots SET");
        let result = self.inner.execute(statement).await?;
        if is_update && self.armed.swap(false, Ordering::SeqCst) {
            self.pause_after_read.store(true, Ordering::SeqCst);
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

#[tokio::test]
async fn legacy_visibility_write_cannot_be_overwritten_by_older_privacy_readback() {
    let (db, _) = fixture::sqlite_repo().await;
    let wrapped = Arc::new(AfterReadbackDb {
        inner: db,
        armed: AtomicBool::new(false),
        pause_after_read: AtomicBool::new(false),
        read_completed: Notify::new(),
        release_readback: Notify::new(),
    });
    let repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        wrapped.clone(),
        DbSqlFlavor::Sqlite,
    ));
    let env = bcs_config::resolve_env_str();
    repo.register_with_owner_and_token(
        "readback".into(),
        BotCapabilities {
            name: Some("Readback".into()),
            ..Default::default()
        },
        "owner",
        "readback-token",
    )
    .await
    .unwrap();
    repo.patch_control_plane(
        "readback",
        &env,
        BotControlPlanePatch {
            visibility: Some("public".into()),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        },
    )
    .await
    .unwrap()
    .unwrap();

    wrapped.armed.store(true, Ordering::SeqCst);
    let patch_repo = repo.clone();
    let patch_env = env.clone();
    let patch = tokio::spawn(async move {
        patch_repo
            .patch_control_plane(
                "readback",
                &patch_env,
                BotControlPlanePatch {
                    friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
                    ..Default::default()
                },
            )
            .await
    });
    tokio::time::timeout(
        Duration::from_secs(1),
        wrapped.read_completed.notified(),
    )
    .await
    .expect("privacy patch must complete its read-back query");

    let legacy_done = Arc::new(Notify::new());
    let legacy_repo = repo.clone();
    let legacy_signal = legacy_done.clone();
    let legacy = tokio::spawn(async move {
        legacy_repo
            .update_visibility("readback", "private")
            .await
            .unwrap();
        legacy_signal.notify_one();
    });
    // The fixed implementation serializes this legacy write behind the
    // in-flight control-plane publication, so the timeout is expected there.
    tokio::select! {
        _ = legacy_done.notified() => {}
        _ = tokio::time::sleep(Duration::from_millis(250)) => {}
    }
    wrapped.release_readback.notify_one();
    patch.await.unwrap().unwrap().unwrap();
    legacy.await.unwrap();

    let rows = wrapped
        .inner
        .query(DbStatement::new(
            "SELECT visibility FROM bcs_bots WHERE bot_uuid = 'readback'",
        ))
        .await
        .unwrap();
    assert_eq!(rows[0].get("visibility"), Some(&DbValue::from("private")));
    assert_eq!(
        repo.try_get("readback")
            .await
            .unwrap()
            .unwrap()
            .capabilities
            .visibility,
        "private",
        "an older read-back must not overwrite a newer legacy visibility write"
    );
}

#[tokio::test]
async fn capability_write_cannot_be_overwritten_by_older_privacy_readback() {
    let (db, _) = fixture::sqlite_repo().await;
    let wrapped = Arc::new(AfterReadbackDb {
        inner: db,
        armed: AtomicBool::new(false),
        pause_after_read: AtomicBool::new(false),
        read_completed: Notify::new(),
        release_readback: Notify::new(),
    });
    let repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        wrapped.clone(),
        DbSqlFlavor::Sqlite,
    ));
    let env = bcs_config::resolve_env_str();
    repo.register_with_owner_and_token(
        "readback".into(),
        BotCapabilities {
            name: Some("Readback".into()),
            ..Default::default()
        },
        "owner",
        "readback-token",
    )
    .await
    .unwrap();
    repo.patch_control_plane(
        "readback",
        &env,
        BotControlPlanePatch {
            visibility: Some("public".into()),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        },
    )
    .await
    .unwrap()
    .unwrap();

    wrapped.armed.store(true, Ordering::SeqCst);
    let patch_repo = repo.clone();
    let patch_env = env.clone();
    let patch = tokio::spawn(async move {
        patch_repo
            .patch_control_plane(
                "readback",
                &patch_env,
                BotControlPlanePatch {
                    friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
                    ..Default::default()
                },
            )
            .await
    });
    tokio::time::timeout(
        Duration::from_secs(1),
        wrapped.read_completed.notified(),
    )
    .await
    .expect("privacy patch must complete its read-back query");

    let legacy_done = Arc::new(Notify::new());
    let legacy_repo = repo.clone();
    let legacy_signal = legacy_done.clone();
    let legacy = tokio::spawn(async move {
        let mut capabilities = legacy_repo
            .try_get("readback")
            .await
            .unwrap()
            .unwrap()
            .capabilities;
        capabilities.visibility = "private".into();
        legacy_repo
            .update_capabilities("readback", capabilities)
            .await
            .unwrap();
        legacy_signal.notify_one();
    });
    tokio::select! {
        _ = legacy_done.notified() => {}
        _ = tokio::time::sleep(Duration::from_millis(250)) => {}
    }
    wrapped.release_readback.notify_one();
    patch.await.unwrap().unwrap().unwrap();
    legacy.await.unwrap();

    assert_eq!(
        repo.try_get("readback")
            .await
            .unwrap()
            .unwrap()
            .capabilities
            .visibility,
        "private",
        "an older read-back must not overwrite a newer capabilities write"
    );
}

struct BlockingUpdateDb {
    inner: Arc<dyn DbPlugin>,
    armed: AtomicBool,
    update_committed: Notify,
    release_update: Notify,
}

#[async_trait]
impl DbPlugin for BlockingUpdateDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        let is_update = statement.sql().starts_with("UPDATE bcs_bots SET");
        let result = self.inner.execute(statement).await?;
        if is_update && self.armed.swap(false, Ordering::SeqCst) {
            self.update_committed.notify_one();
            self.release_update.notified().await;
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

#[tokio::test]
async fn cancellation_while_update_is_committing_cannot_leave_stale_public_cache() {
    let (db, _) = fixture::sqlite_repo().await;
    let wrapped = Arc::new(BlockingUpdateDb {
        inner: db,
        armed: AtomicBool::new(false),
        update_committed: Notify::new(),
        release_update: Notify::new(),
    });
    let repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        wrapped.clone(),
        DbSqlFlavor::Sqlite,
    ));
    let env = bcs_config::resolve_env_str();
    repo.register_with_owner_and_token(
        "readback".into(),
        BotCapabilities {
            name: Some("Readback".into()),
            ..Default::default()
        },
        "owner",
        "readback-token",
    )
    .await
    .unwrap();
    repo.patch_control_plane(
        "readback",
        &env,
        BotControlPlanePatch {
            visibility: Some("public".into()),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        },
    )
    .await
    .unwrap()
    .unwrap();

    wrapped.armed.store(true, Ordering::SeqCst);
    let patch_repo = repo.clone();
    let patch_env = env.clone();
    let patch = tokio::spawn(async move {
        patch_repo
            .patch_control_plane(
                "readback",
                &patch_env,
                BotControlPlanePatch {
                    visibility: Some("private".into()),
                    ..Default::default()
                },
            )
            .await
    });
    tokio::time::timeout(
        Duration::from_secs(1),
        wrapped.update_committed.notified(),
    )
    .await
    .expect("privacy UPDATE must commit before the cancellation");
    patch.abort();
    assert!(patch.await.unwrap_err().is_cancelled());

    assert_eq!(
        repo.try_get("readback")
            .await
            .unwrap()
            .unwrap()
            .capabilities
            .visibility,
        "private",
        "cancelling a committed UPDATE must invalidate the previous public cache"
    );
}

#[tokio::test]
async fn empty_saved_visibility_publishes_the_database_default() {
    let (_db, repo) = fixture::sqlite_repo().await;
    repo.register_with_owner_and_token(
        "readback".into(),
        BotCapabilities {
            name: Some("Readback".into()),
            visibility: "public".into(),
            ..Default::default()
        },
        "owner",
        "readback-token",
    )
    .await
    .unwrap();
    repo.save_to_storage(
        "readback",
        &BotCapabilities {
            name: Some("Readback".into()),
            ..Default::default()
        },
    )
    .await
    .unwrap();
    assert_eq!(
        repo.try_get("readback")
            .await
            .unwrap()
            .unwrap()
            .capabilities
            .visibility,
        "private",
        "an empty stored visibility must not leave the previous public cache"
    );
}

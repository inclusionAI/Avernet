//! Existing-only Bot profile (capabilities + avatar) storage conformance
//! (bot-avatar plan Task 3).
//!
//! Memory and the real SQLite store (after the complete committed migration
//! chain) run the SAME shared contract in `support/bot_profile_contract.rs`.
//! Store-specific failure, isolation, restart and concurrent-delete cases
//! live in this root because they need direct schema or filesystem control.
//! The bcs-admin full MySQL migration-chain gate mounts the same shared
//! contract for the MySQL evidence.

use bcs_db_migrations as bootstrap_migrations;
#[path = "support/bot_profile_fixture.rs"]
mod fixture;
#[path = "support/bot_profile_contract.rs"]
mod profile_contract;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
use bcs_service_api::types::bot_profile::{AvatarChange, ExistingProfileUpdate, ProfileWrite};
use bcs_service_api::{BotCapabilities, BotRepoPort};

use profile_contract::avatar_contract;

#[tokio::test]
async fn memory_avatar_contract() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    avatar_contract(&repo).await;
}

#[tokio::test]
async fn sqlite_avatar_contract() {
    let (_db, repo) = fixture::sqlite_repo().await;
    avatar_contract(&repo).await;
}

#[tokio::test]
async fn memory_avatar_read_excludes_file_backed_humans_after_restart_and_deletion() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    repo.ensure_human_actor("avatar-reader", "Human").await.unwrap();
    let id = "human_avatar-reader";
    let caps = repo.try_get(id).await.unwrap().unwrap().capabilities;
    repo.save_to_storage(id, &caps).await.unwrap();
    assert!(dir.path().join(id).join("bot.json").exists());
    let restarted = MemoryBotRepo::with_base_dir(dir.path().into());
    assert!(restarted.read_avatar_batch(&[id.into()]).await.unwrap().is_empty());
    assert!(repo.soft_delete(id).await);
    assert!(repo.read_avatar_batch(&[id.into()]).await.unwrap().is_empty());
}

#[tokio::test]
async fn memory_avatar_read_does_not_open_known_human_records() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    repo.ensure_human_actor("avatar-reader", "Human").await.unwrap();
    let path = dir.path().join("human_avatar-reader/bot.json");
    tokio::fs::create_dir_all(path.parent().unwrap()).await.unwrap();
    tokio::fs::write(path, "invalid leftover record").await.unwrap();
    assert!(repo.read_avatar_batch(&["human_avatar-reader".into()]).await.unwrap().is_empty());
}

#[tokio::test]
async fn memory_avatar_read_prefers_live_bot_kind_over_namespace_fallback() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    repo.create_registration_if_absent(
        "human_named_bot".into(), BotCapabilities::default(), "owner", "test-token",
    ).await.unwrap();
    repo.update_avatar_existing("human_named_bot", AvatarChange::Set("https://example.test/bot.png".into()))
        .await.unwrap();
    let avatars = repo.read_avatar_batch(&["human_named_bot".into()]).await.unwrap();
    assert_eq!(avatars["human_named_bot"].as_deref(), Some("https://example.test/bot.png"));
}

#[tokio::test]
async fn memory_avatar_survives_restart_and_legacy_files_read_without_avatar() {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    assert!(
        repo.create_registration_if_absent(
            "avatar-bot".into(),
            BotCapabilities::default(),
            "owner",
            "test-runtime-token",
        )
        .await
        .unwrap()
    );
    assert_eq!(
        repo.update_avatar_existing(
            "avatar-bot",
            AvatarChange::Set("https://example.com/restarted.png".into()),
        )
        .await
        .unwrap(),
        ProfileWrite::Updated,
    );

    // 重启: a fresh repository over the same base directory still reads the
    // avatar from the same persisted bot record.
    let restarted = MemoryBotRepo::with_base_dir(dir.path().into());
    let avatars = restarted
        .read_avatar_batch(&["avatar-bot".to_string()])
        .await
        .unwrap();
    assert_eq!(
        avatars["avatar-bot"].as_deref(),
        Some("https://example.com/restarted.png")
    );

    // 旧文件: a legacy record without the avatar field still loads its
    // capabilities and reads as "no avatar" instead of failing.
    let legacy = dir.path().join("legacy-bot/bot.json");
    tokio::fs::create_dir_all(legacy.parent().unwrap())
        .await
        .unwrap();
    tokio::fs::write(
        &legacy,
        br#"{"bot_id":"legacy-bot","name":"Legacy","registered_at":1,"token":"legacy-token"}"#,
    )
    .await
    .unwrap();
    let caps = restarted.load_from_storage("legacy-bot").await.unwrap();
    assert_eq!(caps.name.as_deref(), Some("Legacy"));
    let avatars = restarted
        .read_avatar_batch(&["legacy-bot".to_string()])
        .await
        .unwrap();
    assert_eq!(avatars.get("legacy-bot"), Some(&None));
}

/// A failing durable write must publish no in-memory value: the fallible file
/// write happens first and the registry entry keeps its previous state.
#[cfg(unix)]
#[tokio::test]
async fn memory_failing_profile_writes_publish_no_memory_value() {
    use std::os::unix::fs::PermissionsExt;

    let dir = tempfile::tempdir().unwrap();
    let base = dir.path().join("bots");
    let repo = MemoryBotRepo::with_base_dir(base.clone());
    let named = |name: &str| BotCapabilities {
        name: Some(name.to_string()),
        ..Default::default()
    };
    repo.register("bot-a".into(), named("Profile A"))
        .await
        .unwrap();
    repo.register("bot-b".into(), named("Profile B"))
        .await
        .unwrap();
    assert_eq!(
        repo.update_avatar_existing(
            "bot-a",
            AvatarChange::Set("https://example.com/kept.png".into()),
        )
        .await
        .unwrap(),
        ProfileWrite::Updated,
    );

    let original = std::fs::metadata(&base).unwrap().permissions();
    let mut read_only = original.clone();
    read_only.set_mode(0o555);
    std::fs::set_permissions(&base, read_only).unwrap();

    let update = ExistingProfileUpdate {
        capabilities: BotCapabilities {
            name: Some("Never Published".to_string()),
            ..Default::default()
        },
        avatar: AvatarChange::Set("https://example.com/dropped.png".into()),
    };
    assert!(
        repo.update_profile_existing("bot-b", update)
            .await
            .is_err()
    );
    assert!(
        repo.update_avatar_existing(
            "bot-b",
            AvatarChange::Set("https://example.com/dropped.png".into()),
        )
        .await
        .is_err()
    );

    std::fs::set_permissions(&base, original).unwrap();

    let bot = repo.get("bot-b").await.unwrap();
    assert_eq!(bot.capabilities.name.as_deref(), Some("Profile B"));
    let avatars = repo
        .read_avatar_batch(&["bot-b".to_string(), "bot-a".to_string()])
        .await
        .unwrap();
    assert_eq!(avatars.get("bot-b"), Some(&None));
    assert_eq!(avatars["bot-a"].as_deref(), Some("https://example.com/kept.png"));
}

/// Missing targets must never leave a new row behind: the existing-only
/// writes have no INSERT/upsert branch.
#[tokio::test]
async fn sqlite_missing_profile_targets_never_insert_rows() {
    let (db, repo) = fixture::sqlite_repo().await;
    let count = || async {
        let rows = db
            .query(DbStatement::new("SELECT COUNT(*) AS c FROM bcs_bots"))
            .await
            .unwrap();
        rows[0].get_i64("c").unwrap().unwrap()
    };
    let before = count().await;
    let update = ExistingProfileUpdate {
        capabilities: BotCapabilities {
            name: Some("Never Created".to_string()),
            ..Default::default()
        },
        avatar: AvatarChange::Set("https://example.com/never.png".into()),
    };
    assert_eq!(
        repo.update_profile_existing("never-created", update)
            .await
            .unwrap(),
        ProfileWrite::Missing,
    );
    assert_eq!(
        repo.update_avatar_existing(
            "never-created",
            AvatarChange::Set("https://example.com/never.png".into()),
        )
        .await
        .unwrap(),
        ProfileWrite::Missing,
    );
    assert!(
        repo.read_avatar_batch(&["never-created".to_string()])
            .await
            .unwrap()
            .get("never-created")
            .is_none()
    );
    assert_eq!(count().await, before);
}

/// The batched read isolates by the repository's current environment and
/// excludes Human actor rows.
#[tokio::test]
async fn sqlite_avatar_batch_isolates_environment_and_excludes_humans() {
    let (db, repo) = fixture::sqlite_repo().await;
    db.execute(DbStatement::new(
        "INSERT INTO bcs_bots (bot_uuid, env, name, avatar_url) \
         VALUES ('avatar-foreign', 'env-foreign', 'Foreign', 'https://example.com/foreign.png')",
    ))
    .await
    .unwrap();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, env, name, avatar_url, actor_kind) \
         VALUES ('avatar-human', ?, 'Human', 'https://example.com/human.png', 'human')",
        vec![bcs_config::resolve_env_str().into()],
    ))
    .await
    .unwrap();
    let avatars = repo
        .read_avatar_batch(&["avatar-foreign".to_string(), "avatar-human".to_string()])
        .await
        .unwrap();
    assert!(!avatars.contains_key("avatar-foreign"));
    assert!(!avatars.contains_key("avatar-human"));
}

/// A storage failure of the existing-only write transaction itself leaves
/// the previous name, capabilities, avatar, token and owner intact: the
/// armed `FailingTransactionDb` refuses the whole transaction call at
/// entry (the confirm step inside it never executes), so nothing is
/// classified, written or published.
#[tokio::test]
async fn sqlite_profile_write_failure_changes_neither_profile_nor_avatar() {
    let inner: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(inner.as_ref())
        .await
        .unwrap();
    let db = Arc::new(FailingTransactionDb::new(inner));
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Sqlite);
    let caps = BotCapabilities {
        name: Some("On File".to_string()),
        summary: Some("stored summary".to_string()),
        domains: vec!["stored-domain".to_string()],
        ..Default::default()
    };
    assert!(
        repo.create_registration_if_absent(
            "avatar-bot".into(),
            caps,
            "owner",
            "test-runtime-token",
        )
        .await
        .unwrap()
    );
    assert_eq!(
        repo.update_avatar_existing(
            "avatar-bot",
            AvatarChange::Set("https://example.com/stored.png".into()),
        )
        .await
        .unwrap(),
        ProfileWrite::Updated,
    );

    db.fail_transactions();
    let update = ExistingProfileUpdate {
        capabilities: BotCapabilities {
            name: Some("Never Land".to_string()),
            summary: Some("thrown away".to_string()),
            domains: vec!["thrown-away".to_string()],
            ..Default::default()
        },
        avatar: AvatarChange::Clear,
    };
    assert!(
        repo.update_profile_existing("avatar-bot", update)
            .await
            .is_err()
    );
    assert!(
        repo.update_avatar_existing(
            "avatar-bot",
            AvatarChange::Set("https://example.com/ghost.png".into()),
        )
        .await
        .is_err()
    );
    db.accept_transactions();

    let bot = repo.try_get("avatar-bot").await.unwrap().unwrap();
    assert_eq!(bot.capabilities.name.as_deref(), Some("On File"));
    assert_eq!(bot.capabilities.summary.as_deref(), Some("stored summary"));
    assert_eq!(bot.created_by.as_deref(), Some("owner"));
    assert_eq!(
        repo.try_load_token("avatar-bot").await.unwrap().as_deref(),
        Some("test-runtime-token"),
    );
    let avatars = repo
        .read_avatar_batch(&["avatar-bot".to_string()])
        .await
        .unwrap();
    assert_eq!(
        avatars["avatar-bot"].as_deref(),
        Some("https://example.com/stored.png")
    );
}

/// A soft delete racing an avatar update can only produce one consistent
/// outcome: the update either commits first (the tombstone row then carries
/// the avatar) or observes the tombstone and reports `Missing`. The
/// identity row never drifts or duplicates.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sqlite_concurrent_soft_delete_races_avatar_update_without_row_drift() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("avatar-race.sqlite");
    let setup: Arc<LocalSqliteDbPlugin> =
        Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
    bootstrap_migrations::run_sqlite_migrations(setup.as_ref())
        .await
        .unwrap();
    let repo = PersistentBotRepo::with_sql_flavor(setup.clone(), DbSqlFlavor::Sqlite);
    for round in 0..8 {
        let id = format!("avatar-race-{round}");
        assert!(
            repo.create_registration_if_absent(
                id.clone(),
                BotCapabilities::default(),
                "owner",
                &format!("race-token-{round}"),
            )
            .await
            .unwrap()
        );
        let updater = PersistentBotRepo::with_sql_flavor(
            Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap()),
            DbSqlFlavor::Sqlite,
        );
        let deleter = PersistentBotRepo::with_sql_flavor(
            Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap()),
            DbSqlFlavor::Sqlite,
        );
        let updater_id = id.clone();
        let deleter_id = id.clone();
        let url = if round % 2 == 0 {
            "https://example.com/race-even.png"
        } else {
            "https://example.com/race-odd.png"
        };
        let (updated, deleted) = tokio::join!(
            async move {
                updater
                    .update_avatar_existing(&updater_id, AvatarChange::Set(url.into()))
                    .await
            },
            async move { deleter.soft_delete(&deleter_id).await },
        );
        let updated = updated.unwrap();
        assert!(deleted);
        let avatars = repo.read_avatar_batch(&[id.clone()]).await.unwrap();
        let expected = if updated == ProfileWrite::Updated {
            Some(url)
        } else {
            None
        };
        assert_eq!(avatars[&id].as_deref(), expected, "{id}");
        let rows = setup
            .query(DbStatement::with_params(
                "SELECT COUNT(*) AS c FROM bcs_bots WHERE env = ? AND bot_uuid = ?",
                vec![
                    bcs_db_api::DbValue::from(bcs_config::resolve_env_str()),
                    bcs_db_api::DbValue::from(id.clone()),
                ],
            ))
            .await
            .unwrap();
        assert_eq!(rows[0].get_i64("c").unwrap().unwrap(), 1, "{id}");
    }
}

/// Minimal failure-injecting DbPlugin: all reads and one-shot executes pass
/// through, while the existing-only profile write transaction can be armed
/// to fail so the store keeps its previous committed state.
struct FailingTransactionDb {
    db: Arc<dyn DbPlugin>,
    failing: AtomicBool,
}

impl FailingTransactionDb {
    fn new(db: Arc<dyn DbPlugin>) -> Self {
        Self {
            db,
            failing: AtomicBool::new(false),
        }
    }

    fn fail_transactions(&self) {
        self.failing.store(true, Ordering::SeqCst);
    }

    fn accept_transactions(&self) {
        self.failing.store(false, Ordering::SeqCst);
    }
}

#[async_trait]
impl DbPlugin for FailingTransactionDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.db.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.db.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        if self.failing.load(Ordering::SeqCst) {
            return Err(DbError::Backend("injected profile write failure".into()));
        }
        self.db.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.db.health_check().await
    }
}

#[tokio::test]
async fn sqlite_profile_write_preserves_name_from_concurrently_updated_card() {
    let (db, repo) = fixture::sqlite_repo().await;
    repo.create_registration_if_absent(
        "card-profile".into(), BotCapabilities { name: Some("Old name".into()), ..Default::default() },
        "owner", "test-token",
    ).await.unwrap();
    // Model a Card rename committed after the caller read the old capabilities.
    db.execute(DbStatement::with_params(
        "UPDATE bcs_bots SET name=?, agent_card=? WHERE bot_uuid=?",
        vec!["New Card name".into(), r#"{"name":"New Card name"}"#.into(), "card-profile".into()],
    )).await.unwrap();
    let result = repo.update_profile_existing("card-profile", ExistingProfileUpdate {
        capabilities: BotCapabilities { name: Some("Old name".into()), ..Default::default() },
        avatar: AvatarChange::Set("https://example.test/avatar.png".into()),
    }).await.unwrap();
    assert_eq!(result, ProfileWrite::Updated);
    let rows = db.query(DbStatement::new(
        "SELECT name, avatar_url FROM bcs_bots WHERE bot_uuid='card-profile'",
    )).await.unwrap();
    assert_eq!(rows[0].get_string("name").unwrap().as_deref(), Some("New Card name"));
    assert_eq!(rows[0].get_string("avatar_url").unwrap().as_deref(), Some("https://example.test/avatar.png"));
}

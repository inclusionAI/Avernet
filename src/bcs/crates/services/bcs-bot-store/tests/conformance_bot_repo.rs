use std::sync::Arc;

use bcs_bot_store::{PersistentBotRepo, MemoryBotRepo};
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{DbPlugin, DbStatement, DbValue as Value};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCapabilities, BotMetricsSnapshotPort, BotRepoPort,
    ConnectStreamError, ServiceError, Skill, mock_token,
};
use bcs_test_support::contract::port::bot_metrics_snapshot_port_contract_tests;
use bcs_test_support::contract::repo::bot_repo_port_contract_tests;

#[tokio::test]
async fn persistent_bot_repo_passes_bot_repo_contract() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_plugins(cache, db);

    bot_repo_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn memory_bot_repo_passes_bot_repo_contract() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());

    bot_repo_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn memory_unregister_soft_deletes_bot_from_default_reads() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());
    repo.register_with_owner_and_token(
        "soft-delete-bot".to_string(),
        BotCapabilities {
            name: Some("Soft Delete Bot".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        "11111111",
        "soft-delete-token",
    )
    .await
    .expect("register bot");

    assert!(repo.unregister("soft-delete-bot").await);

    assert!(repo.get("soft-delete-bot").await.is_none());
    assert!(repo.list_active().await.is_empty());
    assert!(repo.list_bots_by_creator("11111111").await.is_empty());
    assert_eq!(repo.find_bot_by_token("soft-delete-token").await, None);
}

#[tokio::test]
async fn persistent_bot_repo_unregister_marks_is_deleted_and_filters_default_reads() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_plugins(cache, db.clone());
    repo.register_with_owner_and_token(
        "soft-delete-bot".to_string(),
        BotCapabilities {
            name: Some("Soft Delete Bot".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        "11111111",
        "soft-delete-token",
    )
    .await
    .expect("register bot");

    assert!(repo.unregister("soft-delete-bot").await);

    assert!(repo.get("soft-delete-bot").await.is_none());
    assert!(repo.list_bots_by_creator("11111111").await.is_empty());
    assert_eq!(repo.find_bot_by_token("soft-delete-token").await, None);

    let rows = db
        .query(DbStatement::with_params(
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![
                Value::from("soft-delete-bot"),
                Value::from(bcs_config::resolve_env_str()),
            ],
        ))
        .await
        .expect("query soft deleted row");
    assert_eq!(rows.len(), 1);
    assert_eq!(
        rows[0].get("is_deleted").and_then(Value::as_i64),
        Some(1)
    );
}

#[tokio::test]
async fn persistent_bot_repo_register_after_soft_delete_does_not_clear_is_deleted_column() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_plugins(cache, db.clone());
    repo.register_with_owner_and_token(
        "soft-delete-bot".to_string(),
        BotCapabilities {
            name: Some("Soft Delete Bot".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        "11111111",
        "soft-delete-token",
    )
    .await
    .expect("register bot");

    assert!(repo.unregister("soft-delete-bot").await);

    repo.register_with_owner_and_token(
        "soft-delete-bot".to_string(),
        BotCapabilities {
            name: Some("Updated Soft Delete Bot".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        "11111111",
        "new-soft-delete-token",
    )
    .await
    .expect("register bot again");

    let rows = db
        .query(DbStatement::with_params(
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![
                Value::from("soft-delete-bot"),
                Value::from(bcs_config::resolve_env_str()),
            ],
        ))
        .await
        .expect("query soft deleted row");
    assert_eq!(rows.len(), 1);
    assert_eq!(
        rows[0].get("is_deleted").and_then(Value::as_i64),
        Some(1)
    );
}

#[tokio::test]
async fn persistent_bot_repo_passes_bot_metrics_snapshot_contract() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_plugins(cache, db.clone());

    seed_metrics_bot(&repo).await;
    seed_metrics_human_row(db.as_ref()).await;
    bot_metrics_snapshot_port_contract_tests(&repo).await;
    assert_metrics_actors_counted(&repo).await;
}

#[tokio::test]
async fn memory_bot_repo_passes_bot_metrics_snapshot_contract() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());

    seed_metrics_actors(&repo).await;
    bot_metrics_snapshot_port_contract_tests(&repo).await;
    assert_metrics_actors_counted(&repo).await;
}

async fn seed_metrics_actors(repo: &dyn BotRepoPort) {
    seed_metrics_bot(repo).await;
    repo.ensure_human_actor("metrics_staff", "Metrics Human")
        .await
        .expect("ensure metrics human");
}

async fn seed_metrics_bot(repo: &dyn BotRepoPort) {
    repo.register(
        "metrics_bot".to_string(),
        BotCapabilities {
            name: Some("Metrics Bot".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
    )
    .await
    .expect("register metrics bot");
}

async fn seed_metrics_human_row(db: &dyn DbPlugin) {
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots \
         (bot_uuid, name, bot_info, session_token, created_by, visibility, status, actor_kind, env, registered_at, updated_at) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        vec![
            Value::from("human_metrics_staff"),
            Value::from("Metrics Human"),
            Value::from("{}"),
            Value::from("metrics-token"),
            Value::from("metrics_staff"),
            Value::from("protected"),
            Value::from("online"),
            Value::from("human"),
            Value::from(env.as_str()),
            Value::from("2026-01-01 00:00:00"),
            Value::from("2026-01-01 00:00:00"),
        ],
    ))
        .await
        .expect("insert metrics human row");
}

async fn assert_metrics_actors_counted(repo: &dyn BotMetricsSnapshotPort) {
    let counts = repo.bot_counts().await.expect("bot counts");
    assert!(counts.iter().any(|count| {
        count.actor_kind == ActorKind::Bot
            && count.status == ActorStatus::Online
            && count.visibility.as_deref() == Some("public")
            && count.count == 1
    }));
    assert!(counts.iter().any(|count| {
        count.actor_kind == ActorKind::Human
            && count.status == ActorStatus::Online
            && count.visibility.as_deref() == Some("protected")
            && count.count == 1
    }));
}

#[tokio::test]
async fn connect_or_promote_streaming_creates_bot_when_absent() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());

    let token = repo
        .connect_or_promote_streaming("plugin-bot:alice".to_string())
        .await
        .expect("create");
    assert!(!token.is_empty());
    // newly created token is a real (non-MOCK) value the bot can reconnect with
    assert!(!bcs_service_api::is_mock_token(&token));
    assert_eq!(repo.load_token("plugin-bot:alice").await.as_deref(), Some(token.as_str()));
}

#[tokio::test]
async fn connect_or_promote_streaming_promotes_mock_to_real() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());

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
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_plugins(cache.clone(), db.clone());

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
    let repo_after = PersistentBotRepo::with_plugins(cache.clone(), db);
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
    let repo = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());

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
    let repo = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());

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
    assert_eq!(repo.load_token("live-bot:alice").await.as_deref(), Some(first.as_str()));
}

#[tokio::test]
async fn persistent_repo_update_capabilities_replaces_in_memory_and_db() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_plugins(cache, db);

    repo.register(
        "update-cap-bot".to_string(),
        BotCapabilities {
            name: Some("Update Cap Bot".to_string()),
            domains: vec!["before".to_string()],
            skills: vec![Skill::new("before_skill")],
            scopes: vec!["before_scope".to_string()],
            visibility: "public".to_string(),
            ..Default::default()
        },
    )
    .await
    .expect("register bot");

    // update_capabilities replaces capabilities wholesale (no empty-array skip,
    // unlike register). Clear fields and assert it takes effect in the live
    // in-memory registry, not only in the DB column.
    repo.update_capabilities(
        "update-cap-bot",
        BotCapabilities {
            name: Some("Update Cap Bot".to_string()),
            domains: vec![],
            skills: vec![Skill::new("after_skill")],
            scopes: vec![],
            visibility: "protected".to_string(),
            ..Default::default()
        },
    )
    .await
    .expect("update_capabilities");

    let stored = repo.get("update-cap-bot").await.expect("bot present");
    assert!(
        stored.capabilities.domains.is_empty(),
        "domains must be cleared in memory (register would skip empty arrays)"
    );
    assert!(stored.capabilities.scopes.is_empty());
    assert_eq!(
        stored
            .capabilities
            .skills
            .iter()
            .map(|s| s.name.clone())
            .collect::<Vec<_>>(),
        vec!["after_skill".to_string()]
    );
    assert_eq!(stored.capabilities.visibility, "protected");
}

#[tokio::test]
async fn persistent_repo_update_capabilities_returns_not_found_for_unknown_bot() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_plugins(cache, db);

    let err = repo
        .update_capabilities(
            "never-registered",
            BotCapabilities {
                name: Some("Ghost".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect_err("unknown bot must error");
    assert!(matches!(err, ServiceError::BotNotFound(id) if id == "never-registered"));
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            bot_uuid TEXT NOT NULL,
            name TEXT,
            bot_info TEXT,
            session_token TEXT,
            created_by TEXT,
            visibility TEXT,
            status TEXT NOT NULL DEFAULT 'online',
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            is_deleted INTEGER NOT NULL DEFAULT 0,
            agent_code TEXT DEFAULT NULL,
            env TEXT NOT NULL,
            registered_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (bot_uuid, env)
        )",
    ))
    .await
    .expect("create bcs_bots table");
    db
}

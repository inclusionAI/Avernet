use std::sync::Arc;

use bcs_bot_store::{LayottoRegistry, MemoryBotRepo};
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{DbPlugin, DbStatement, DbValue as Value};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCapabilities, BotMetricsSnapshotPort, BotRepoPort,
};
use bcs_test_support::contract::port::bot_metrics_snapshot_port_contract_tests;
use bcs_test_support::contract::repo::bot_repo_port_contract_tests;

#[tokio::test]
async fn layotto_registry_passes_bot_repo_contract() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = LayottoRegistry::with_plugins(cache, db, "contract".to_string());

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
async fn layotto_unregister_marks_is_deleted_and_filters_default_reads() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = LayottoRegistry::with_plugins(cache, db.clone(), "contract".to_string());
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
async fn layotto_register_after_soft_delete_does_not_clear_is_deleted_column() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = LayottoRegistry::with_plugins(cache, db.clone(), "contract".to_string());
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
async fn layotto_registry_passes_bot_metrics_snapshot_contract() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = LayottoRegistry::with_plugins(cache, db.clone(), "contract".to_string());

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

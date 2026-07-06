use std::sync::Arc;

use bcs_bot::BotCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::{BotCapabilities, BotRegistryCoreService};

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

fn test_caps() -> BotCapabilities {
    BotCapabilities {
        name: Some("test-bot".to_string()),
        ..Default::default()
    }
}

async fn create_persistent_bot_repo() -> PersistentBotRepo {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    PersistentBotRepo::with_plugins(cache, db)
}

#[tokio::test]
async fn bot_core_delegates_add_bot_info() {
    let registry = Arc::new(create_persistent_bot_repo().await);
    registry.register("bot-1".to_string(), test_caps()).await.unwrap();

    let core = BotCore::with_repo(registry.clone());
    core.add_bot_info("bot-1", "agent_token", "jwt-token".to_string()).await;

    let value = core.get_bot_info("bot-1", "agent_token").await;
    assert_eq!(value, Some("jwt-token".to_string()));
}

#[tokio::test]
async fn bot_core_get_bot_info_returns_none_when_not_set() {
    let registry = Arc::new(create_persistent_bot_repo().await);
    registry.register("bot-1".to_string(), test_caps()).await.unwrap();

    let core = BotCore::with_repo(registry.clone());
    let value = core.get_bot_info("bot-1", "agent_token").await;
    assert_eq!(value, None);
}

#[tokio::test]
async fn bot_core_add_bot_info_unrecognized_key_ignored() {
    let registry = Arc::new(create_persistent_bot_repo().await);
    registry.register("bot-1".to_string(), test_caps()).await.unwrap();

    let core = BotCore::with_repo(registry.clone());
    core.add_bot_info("bot-1", "unknown", "val".to_string()).await;

    let value = core.get_bot_info("bot-1", "unknown").await;
    assert_eq!(value, None);
}

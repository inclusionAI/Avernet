use std::sync::Arc;

use bcs_bot_store::LayottoRegistry;
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::BotCapabilities;

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

async fn create_layotto_registry() -> LayottoRegistry {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    LayottoRegistry::with_plugins(cache, db, "test".to_string())
}

// ============================================================
// LayottoRegistry: add_bot_info / get_bot_info
// ============================================================

#[tokio::test]
async fn layotto_add_bot_info_agent_token_stores_value() {
    let registry = create_layotto_registry().await;
    registry.register("bot-1".to_string(), test_caps()).await.unwrap();

    registry.add_bot_info("bot-1", "agent_token", "eyJ.abc.xyz".to_string()).await;

    let value = registry.get_bot_info("bot-1", "agent_token").await;
    assert_eq!(value, Some("eyJ.abc.xyz".to_string()));
}

#[tokio::test]
async fn layotto_get_bot_info_returns_none_for_unset_token() {
    let registry = create_layotto_registry().await;
    registry.register("bot-1".to_string(), test_caps()).await.unwrap();

    let value = registry.get_bot_info("bot-1", "agent_token").await;
    assert_eq!(value, None);
}

#[tokio::test]
async fn layotto_add_bot_info_unrecognized_key_is_ignored() {
    let registry = create_layotto_registry().await;
    registry.register("bot-1".to_string(), test_caps()).await.unwrap();

    registry.add_bot_info("bot-1", "unknown_key", "value".to_string()).await;

    let value = registry.get_bot_info("bot-1", "unknown_key").await;
    assert_eq!(value, None);
}

#[tokio::test]
async fn layotto_get_bot_info_nonexistent_bot_returns_none() {
    let registry = create_layotto_registry().await;
    let value = registry.get_bot_info("ghost", "agent_token").await;
    assert_eq!(value, None);
}

#[tokio::test]
async fn layotto_add_bot_info_nonexistent_bot_is_noop() {
    let registry = create_layotto_registry().await;
    registry.add_bot_info("ghost", "agent_token", "token".to_string()).await;
    let value = registry.get_bot_info("ghost", "agent_token").await;
    assert_eq!(value, None);
}

#[tokio::test]
async fn layotto_add_bot_info_overwrites_previous_value() {
    let registry = create_layotto_registry().await;
    registry.register("bot-1".to_string(), test_caps()).await.unwrap();

    registry.add_bot_info("bot-1", "agent_token", "old-token".to_string()).await;
    registry.add_bot_info("bot-1", "agent_token", "new-token".to_string()).await;

    let value = registry.get_bot_info("bot-1", "agent_token").await;
    assert_eq!(value, Some("new-token".to_string()));
}

#[tokio::test]
async fn layotto_add_bot_info_does_not_leak_to_get_agent_credentials_code() {
    let registry = create_layotto_registry().await;
    registry.register("bot-1".to_string(), test_caps()).await.unwrap();

    registry.add_bot_info("bot-1", "agent_token", "secret-jwt".to_string()).await;

    let creds = registry.get_agent_credentials("bot-1").await.unwrap();
    assert_eq!(creds.agent_token, Some("secret-jwt".to_string()));
    assert_eq!(creds.agent_code, None);
}

// Note: MemoryBotRepo now genuinely stores `agent_token` / `client_kind` via
// add_bot_info (see `memory_bot_repo_passes_bot_repo_contract` and server.rs
// agent_token persistence). The earlier "noop" expectation is obsolete and was
// removed; MemoryBotRepo must round-trip these keys like the layotto repo.

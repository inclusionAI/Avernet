use super::*;
use std::collections::BTreeMap;
use bcs_cache_api::{CacheResult, CacheSetMode, CacheTtl};
use bcs_db_local::LocalSqliteDbPlugin;

// Any cache access is a regression, including reads that silently fall back.
struct ForbiddenCache;

#[async_trait]
impl CachePlugin for ForbiddenCache {
    async fn get_value(&self, _: &str) -> CacheResult<Option<Vec<u8>>> { panic!("unexpected cache GET") }
    async fn set_value(&self, _: &str, _: Vec<u8>, _: Option<Duration>, _: CacheSetMode) -> CacheResult<bool> { panic!("unexpected cache SET") }
    async fn delete(&self, _: &str) -> CacheResult<bool> { panic!("unexpected cache DEL") }
    async fn expire(&self, _: &str, _: Duration) -> CacheResult<bool> { panic!("unexpected cache EXPIRE") }
    async fn ttl(&self, _: &str) -> CacheResult<CacheTtl> { panic!("unexpected cache TTL") }
    async fn hash_get(&self, _: &str, _: &str) -> CacheResult<Option<Vec<u8>>> { panic!("unexpected cache HGET") }
    async fn hash_get_all(&self, _: &str) -> CacheResult<BTreeMap<String, Vec<u8>>> { panic!("unexpected cache HGETALL") }
    async fn hash_set(&self, _: &str, _: &str, _: Vec<u8>) -> CacheResult<()> { panic!("unexpected cache HSET") }
    async fn hash_set_many(&self, _: &str, _: BTreeMap<String, Vec<u8>>) -> CacheResult<()> { panic!("unexpected cache hash write") }
    async fn hash_delete(&self, _: &str, _: &str) -> CacheResult<bool> { panic!("unexpected cache HDEL") }
}

async fn database() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            bot_uuid TEXT NOT NULL, name TEXT, bot_info TEXT, session_token TEXT,
            created_by TEXT, visibility TEXT, status TEXT NOT NULL DEFAULT 'online',
            actor_kind TEXT NOT NULL DEFAULT 'bot', is_deleted INTEGER NOT NULL DEFAULT 0,
            agent_code TEXT DEFAULT NULL, env TEXT NOT NULL, registered_at TEXT,
            updated_at TEXT, PRIMARY KEY (bot_uuid, env)
        )",
    )).await.unwrap();
    db
}

fn repository(db: Arc<dyn DbPlugin>) -> PersistentBotRepo {
    PersistentBotRepo::with_plugins_flavor_and_cache_key_prefix(
        Arc::new(ForbiddenCache), db, DbSqlFlavor::Sqlite, "custom-prefix:",
    )
}

async fn register(repo: &PersistentBotRepo) {
    repo.register_with_owner_and_token(
        "heartbeat-bot".into(),
        BotCapabilities {
            name: Some("Database Helper".into()),
            summary: Some("Static SQL expertise".into()),
            skills: vec![Skill::new("sql")],
            visibility: "public".into(),
            ..Default::default()
        },
        "owner", "heartbeat-token",
    ).await.unwrap();
}

fn payload() -> BotDynamicStatus {
    BotDynamicStatus {
        status: "busy".into(), dynamic_summary: Some("ephemeral-only-marker".into()),
        load: Some(0.9), updated_at: Some(1),
    }
}

#[tokio::test]
async fn heartbeat_renews_registration_without_cache_or_retained_payload() {
    let repo = repository(database().await);
    register(&repo).await;
    // Renewal applies repeatedly, not only to newly registered bots.
    for _ in 0..2 {
        repo.bots.write().await.get_mut("heartbeat-bot").unwrap().last_heartbeat =
            Instant::now() - BOT_EXPIRY - Duration::from_secs(1);
        assert!(repo.list_active().await.is_empty());
        assert!(repo.update_status("heartbeat-bot", payload()).await);
        assert_eq!(repo.list_active().await.len(), 1);
        assert!(!repo.bots.read().await["heartbeat-bot"].is_expired());
    }
    let bot = repo.get("heartbeat-bot").await.unwrap();
    assert!(serde_json::to_value(bot).unwrap().get("dynamic_status").is_none());
    assert!(!repo.update_status("unknown", payload()).await);
    assert!(!repo.bots.read().await.contains_key("unknown"));
    assert!(repo.discover("ephemeral-only-marker").await.is_empty());
    assert_eq!(repo.discover("Static SQL").await.len(), 1);
    assert_eq!(repo.find_by_skills(&["sql"]).await.len(), 1);
}

#[tokio::test]
async fn database_fallback_and_reconnect_do_not_read_status_cache() {
    let db = database().await;
    register(&repository(db.clone())).await;
    let cold = repository(db);
    let bot = cold.try_get("heartbeat-bot").await.unwrap().unwrap();
    assert_eq!(bot.capabilities.name.as_deref(), Some("Database Helper"));
    assert!(!cold.is_connected("heartbeat-bot").await);
    let (bot_id, token) = cold.reconnect_streaming("heartbeat-token".into()).await.unwrap();
    assert_eq!(bot_id, "heartbeat-bot");
    assert_eq!(token, "heartbeat-token");
    assert!(cold.is_connected(&bot_id).await);
    assert!(!cold.bots.read().await[&bot_id].is_expired());
    assert!(serde_json::to_value(cold.get(&bot_id).await.unwrap()).unwrap().get("dynamic_status").is_none());
}

#[tokio::test]
async fn deleted_bot_name_backfill_does_not_read_status_cache() {
    let db = database().await;
    let repo = repository(db.clone());
    register(&repo).await;
    assert!(repo.soft_delete("heartbeat-bot").await);
    let cold = repository(db);
    assert!(cold.get("heartbeat-bot").await.is_none());
    let bot = cold.get_including_deleted("heartbeat-bot").await.unwrap();
    assert_eq!(bot.capabilities.name.as_deref(), Some("Database Helper"));
}

use std::sync::Arc;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;

pub async fn sqlite() -> Arc<LocalSqliteDbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new("CREATE TABLE bcs_providers (provider_id TEXT NOT NULL, env TEXT NOT NULL, PRIMARY KEY (provider_id, env))")).await.unwrap();
    db.execute(DbStatement::with_params("INSERT INTO bcs_providers (provider_id, env) VALUES ('provider-a', ?)", vec![bcs_config::resolve_env_str().into()])).await.unwrap();
    db.execute(DbStatement::new("CREATE TABLE bcs_bots (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL, bot_info TEXT, session_token TEXT UNIQUE, created_by TEXT, visibility TEXT, status TEXT NOT NULL DEFAULT 'online', actor_kind TEXT NOT NULL DEFAULT 'bot', is_deleted INTEGER NOT NULL DEFAULT 0, agent_code TEXT, registered_at TEXT, updated_at TEXT, PRIMARY KEY (bot_uuid, env))")).await.unwrap();
    db.execute(DbStatement::new("CREATE TABLE bcs_provider_bot_bindings (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, provider_id TEXT NOT NULL, provider_bot_ref TEXT NOT NULL, webhook_url TEXT, disabled INTEGER NOT NULL DEFAULT 0, gmt_create TEXT DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (env, bot_uuid), UNIQUE (env, provider_id, provider_bot_ref))")).await.unwrap();
    for sql in include_str!("../../../../../migrations/sqlite/031_bot_provider_storage.sql").split(';').map(str::trim).filter(|s| !s.is_empty()) {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    db
}

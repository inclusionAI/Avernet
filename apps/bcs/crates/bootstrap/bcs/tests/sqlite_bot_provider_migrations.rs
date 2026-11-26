//! Run the full SQLite chain twice for each supported starting state. Bot avatar,
//! Provider slug and webhook checks share the database, not just the setup code.
use bcs::migrations::{run_sqlite_migrations, sqlite_target_version};
use bcs_db_api::{DbPlugin, DbRow, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;

async fn seed_legacy(db: &LocalSqliteDbPlugin) {
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_uuid TEXT NOT NULL,
            env TEXT DEFAULT NULL,
            name TEXT NOT NULL DEFAULT '',
            bot_info TEXT DEFAULT NULL,
            session_token TEXT DEFAULT NULL,
            created_by TEXT DEFAULT NULL,
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            agent_code TEXT DEFAULT NULL,
            is_deleted INTEGER NOT NULL DEFAULT 0
        )",
    ))
    .await.unwrap();
    db.execute(DbStatement::new(
        "INSERT INTO bcs_bots (bot_uuid, env, name, bot_info, session_token, created_by) \
        VALUES ('legacy-bot', 'local', 'Legacy Bot', '{\"description\":\"kept\"}', \
        'legacy-token', 'alice')",
    ))
    .await.unwrap();
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_providers (
        provider_id TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL,
        config TEXT NOT NULL, created_by TEXT NOT NULL, owners TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0, gmt_create TEXT DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (env, provider_id))",
    ))
    .await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_providers
        (provider_id, env, name, config, created_by, owners) VALUES ('old', 'local', 'Old', '{}', 'alice', '[]')"))
        .await.unwrap();
    db.execute(DbStatement::new("CREATE TABLE bcs_provider_bot_bindings (
        bot_uuid TEXT NOT NULL, provider_id TEXT NOT NULL, provider_bot_ref TEXT NOT NULL,
        env TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0,
        gmt_create TEXT DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP
    )")).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_provider_bot_bindings
        (bot_uuid, provider_id, provider_bot_ref, env) VALUES ('old', 'provider', 'ref', 'local')"))
        .await.unwrap();
}

async fn migrate(db: &LocalSqliteDbPlugin) -> Vec<DbRow> {
    run_sqlite_migrations(db).await.unwrap();
    let history = db.query(DbStatement::new(
        "SELECT version, name, checksum FROM bcs_schema_migrations ORDER BY version",
    )).await.unwrap();
    assert_eq!(history.len(), sqlite_target_version() as usize);
    let avatar = history.iter().find(|r| r.get("version") == Some(&DbValue::I64(40))).unwrap();
    assert_eq!(avatar.get("name"), Some(&DbValue::String("bot_avatar".into())));

    let fields = db.query(DbStatement::new("PRAGMA table_info(bcs_bots)")).await.unwrap();
    let avatar = fields.iter().find(|r| r.get("name") == Some(&DbValue::String("avatar_url".into()))).unwrap();
    assert_eq!(avatar.get("notnull"), Some(&DbValue::I64(0)));
    let indices = db
        .query(DbStatement::new("PRAGMA index_list(bcs_providers)"))
        .await
        .unwrap();
    assert!(indices
        .iter()
        .any(|row| row.get("name") == Some(&DbValue::String("uk_bcs_providers_env_slug".into()))));
    history
}

#[tokio::test]
async fn fresh_database_exposes_nullable_metadata_after_restart() {
    let db = LocalSqliteDbPlugin::new().unwrap();
    let history = migrate(&db).await;
    assert_eq!(migrate(&db).await, history);
    assert!(db.query(DbStatement::new("SELECT webhook_url FROM bcs_provider_bot_bindings")).await.unwrap().is_empty());
}

#[tokio::test]
async fn legacy_upgrade_preserves_bot_provider_and_binding_after_restart() {
    let db = LocalSqliteDbPlugin::new().unwrap();
    seed_legacy(&db).await;
    let history = migrate(&db).await;
    assert_preserved_rows(&db).await;
    assert_eq!(migrate(&db).await, history);
    assert_preserved_rows(&db).await;
}

async fn assert_preserved_rows(db: &LocalSqliteDbPlugin) {
    let row_sql = "SELECT name, bot_info, session_token, created_by, avatar_url \
        FROM bcs_bots WHERE bot_uuid = 'legacy-bot'";
    let rows = db.query(DbStatement::new(row_sql)).await.unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].get("name"), Some(&DbValue::String("Legacy Bot".into())));
    assert_eq!(
        rows[0].get("bot_info"),
        Some(&DbValue::String("{\"description\":\"kept\"}".into()))
    );
    assert_eq!(rows[0].get("session_token"), Some(&DbValue::String("legacy-token".into())));
    assert_eq!(rows[0].get("created_by"), Some(&DbValue::String("alice".into())));
    assert_eq!(rows[0].get("avatar_url"), Some(&DbValue::Null));
    let providers = db.query(DbStatement::new("SELECT provider_id, name, slug FROM bcs_providers")).await.unwrap();
    assert_eq!(providers.len(), 1);
    assert_eq!(providers[0].get("provider_id"), Some(&DbValue::String("old".into())));
    assert_eq!(providers[0].get("name"), Some(&DbValue::String("Old".into())));
    assert_eq!(providers[0].get("slug"), Some(&DbValue::Null));
    let bindings = db.query(DbStatement::new("SELECT bot_uuid, webhook_url FROM bcs_provider_bot_bindings")).await.unwrap();
    assert_eq!(bindings.len(), 1);
    assert_eq!(bindings[0].get("bot_uuid"), Some(&DbValue::String("old".into())));
    assert_eq!(bindings[0].get("webhook_url"), Some(&DbValue::Null));
}

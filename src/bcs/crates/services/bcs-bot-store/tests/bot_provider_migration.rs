use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;

async fn upgraded() -> LocalSqliteDbPlugin {
    let db = LocalSqliteDbPlugin::new().unwrap();
    db.execute(DbStatement::new("CREATE TABLE bcs_bots (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, session_token TEXT, is_deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (bot_uuid, env))")).await.unwrap();
    db.execute(DbStatement::new("CREATE TABLE bcs_provider_bot_bindings (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, provider_id TEXT NOT NULL, provider_bot_ref TEXT NOT NULL, webhook_url TEXT, disabled INTEGER NOT NULL DEFAULT 0)")).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid, env, session_token) VALUES ('legacy', 'test', 'test-only-runtime')")).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_provider_bot_bindings (bot_uuid, env, provider_id, provider_bot_ref) VALUES ('legacy', 'test', 'provider', 'ref')")).await.unwrap();
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../migrations/sqlite/030_bot_provider_storage.sql");
    let sql = std::fs::read_to_string(path).expect("additive Bot Provider migration must exist");
    for statement in sql.split(';').map(str::trim).filter(|s| !s.is_empty()) {
        db.execute(DbStatement::new(statement)).await.unwrap();
    }
    db
}

#[tokio::test]
async fn additive_schema_preserves_legacy_identity_and_marks_mode_unmigrated() {
    let db = upgraded().await;
    let rows = db.query(DbStatement::new("SELECT session_token, connection_mode, provider_id, provider_bot_ref, webhook_url, provider_registered_at, provider_updated_at FROM bcs_bots WHERE bot_uuid = 'legacy'")).await.unwrap();
    assert_eq!(rows[0].get_string("session_token").unwrap().as_deref(), Some("test-only-runtime"));
    assert_eq!(rows[0].get_string("connection_mode").unwrap(), None);
    assert_eq!(rows[0].get_string("provider_id").unwrap(), None);
    let bindings = db.query(DbStatement::new("SELECT provider_id, disabled FROM bcs_provider_bot_bindings WHERE bot_uuid = 'legacy'")).await.unwrap();
    assert_eq!(bindings[0].get_string("provider_id").unwrap().as_deref(), Some("provider"));
    assert_eq!(bindings[0].get_bool("disabled").unwrap(), Some(false));
}

#[tokio::test]
async fn schema_accepts_the_shared_modes_and_rejects_draft_spelling() {
    let db = upgraded().await;
    for mode in ["plugin", "gateway"] {
        db.execute(DbStatement::with_params("UPDATE bcs_bots SET connection_mode = ? WHERE bot_uuid = 'legacy'", vec![mode.into()])).await.unwrap();
        let rows = db.query(DbStatement::new("SELECT connection_mode FROM bcs_bots WHERE bot_uuid = 'legacy'")).await.unwrap();
        assert_eq!(rows[0].get_string("connection_mode").unwrap().as_deref(), Some(mode));
    }
    for mode in ["upstream", "Plugin", "Gateway", "", "bogus"] {
        assert!(db.execute(DbStatement::with_params("UPDATE bcs_bots SET connection_mode = ? WHERE bot_uuid = 'legacy'", vec![mode.into()])).await.is_err());
    }
}

#[tokio::test]
async fn provider_ref_uniqueness_spans_modes_and_preserves_deleted_identity() {
    let db = upgraded().await;
    db.execute(DbStatement::new("UPDATE bcs_bots SET provider_id = 'provider', provider_bot_ref = 'ref', connection_mode = 'gateway', is_deleted = 1 WHERE bot_uuid = 'legacy'")).await.unwrap();
    assert!(db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid, env, provider_id, provider_bot_ref, connection_mode) VALUES ('other', 'test', 'provider', 'ref', 'plugin')")).await.is_err());
    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid, env, provider_id, provider_bot_ref, connection_mode) VALUES ('other', 'different-env', 'provider', 'ref', 'plugin')")).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid, env, connection_mode) VALUES ('ordinary-a', 'test', 'plugin'), ('ordinary-b', 'test', 'plugin')")).await.unwrap();
}
